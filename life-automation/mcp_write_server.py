#!/usr/bin/env python3
"""MCP write server for ~/life/ knowledge base.

Exposes tools for appending to daily notes, creating entities, and adding facts.
Any MCP client (Claude Code, Hermes, Cursor, Copilot) can write to ~/life/ through this.

Usage:
    python3 mcp_write_server.py              # Start MCP server (stdio transport)

Register in Claude Code:
    claude mcp add life-write python3 /path/to/mcp_write_server.py
"""
import fcntl
import json
import os
import re
from datetime import date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = str(date.today())

TYPE_TO_DIR = {
    "person": "People",
    "project": "Projects",
    "company": "Companies",
}

VALID_CATEGORIES = {
    "deployment", "decision", "configuration", "lesson",
    "preference", "event", "pending", "relationship", "contact", "financial",
}

VALID_SECTIONS = {
    "Active Projects", "What We Worked On", "Decisions Made",
    "Pending Items", "New Facts",
}

LOCK_PATH = LIFE_DIR / "logs" / "consolidate.lock"

mcp = FastMCP("life-write")


def _normalize_slug(name: str) -> str:
    """Normalize entity slug to lowercase-hyphens only."""
    slug = name.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _validate_path(path: Path) -> None:
    """Ensure path is under LIFE_DIR (no traversal)."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(LIFE_DIR.resolve())):
        raise ValueError(f"Path traversal blocked: {path}")


def _acquire_lock():
    """Acquire flock on consolidate.lock. Returns fd."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = open(LOCK_PATH, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_lock(fd):
    """Release flock."""
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()


def _get_daily_note_path() -> Path:
    """Get today's daily note path."""
    parts = TODAY.split("-")
    return LIFE_DIR / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"


def _ensure_daily_note(path: Path) -> None:
    """Create daily note from template if missing."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    template = LIFE_DIR / "Daily" / "_template.md"
    if template.exists():
        content = template.read_text(encoding="utf-8").replace("{{date}}", TODAY)
    else:
        content = f"---\ndate: {TODAY}\n---\n\n## Active Projects\n\n## What We Worked On\n\n## Pending Items\n"
    path.write_text(content, encoding="utf-8")


def _find_entity(slug: str) -> Path | None:
    """Find entity dir by scanning Projects/People/Companies."""
    for parent in TYPE_TO_DIR.values():
        d = LIFE_DIR / parent / slug
        if d.is_dir():
            return d
    return None


@mcp.tool()
def append_daily_note(content: str, section: str) -> str:
    """Append content to today's daily note under a specific section.

    Args:
        content: Text to append (markdown)
        section: Section header (e.g., "What We Worked On", "Pending Items", "New Facts")
    """
    if section not in VALID_SECTIONS:
        return f"Error: invalid section '{section}'. Valid: {', '.join(sorted(VALID_SECTIONS))}"

    fd = _acquire_lock()
    try:
        note_path = _get_daily_note_path()
        _ensure_daily_note(note_path)

        text = note_path.read_text(encoding="utf-8")
        provenance = f"<!-- source: mcp at {TODAY} -->\n"
        entry = f"{provenance}{content}\n"

        # Find section and append after it
        header = f"## {section}"
        if header in text:
            # Find the end of the section (next ## or end of file)
            idx = text.index(header) + len(header)
            # Find next section header
            next_section = text.find("\n## ", idx)
            if next_section == -1:
                # Append at end
                text = text.rstrip() + "\n" + entry
            else:
                # Insert before next section
                text = text[:next_section] + "\n" + entry + text[next_section:]
        else:
            # Section doesn't exist — add it at end
            text = text.rstrip() + f"\n\n{header}\n{entry}"

        note_path.write_text(text, encoding="utf-8")
        return f"Appended to {section} in {note_path.name}"
    finally:
        _release_lock(fd)


@mcp.tool()
def create_entity(entity_type: str, slug: str, display_name: str) -> str:
    """Create a new entity (person, project, or company) in the knowledge base.

    Args:
        entity_type: One of "person", "project", "company"
        slug: URL-friendly name (lowercase-hyphens, e.g., "john-doe")
        display_name: Human-readable name (e.g., "John Doe")
    """
    if entity_type not in TYPE_TO_DIR:
        return f"Error: invalid type '{entity_type}'. Valid: {', '.join(TYPE_TO_DIR.keys())}"

    slug = _normalize_slug(slug)
    if not slug:
        return "Error: slug is empty after normalization"
    if ".." in slug:
        return "Error: slug contains '..'"

    parent = TYPE_TO_DIR[entity_type]
    entity_dir = LIFE_DIR / parent / slug
    _validate_path(entity_dir)

    if entity_dir.exists():
        return f"Error: {parent}/{slug}/ already exists"

    fd = _acquire_lock()
    try:
        entity_dir.mkdir(parents=True, exist_ok=True)

        # Load template
        tmpl_dir = LIFE_DIR / parent / "_template"
        if (tmpl_dir / "summary.md").exists():
            summary = (tmpl_dir / "summary.md").read_text(encoding="utf-8")
            summary = summary.replace("Display Name", display_name).replace("YYYY-MM-DD", TODAY)
        else:
            summary = (
                f"---\ntype: {entity_type}\nname: {display_name}\n"
                f"created: {TODAY}\nlast-updated: {TODAY}\nstatus: active\n---\n\n"
                f"## What This Is\nCreated via MCP.\n"
            )

        (entity_dir / "summary.md").write_text(summary, encoding="utf-8")
        (entity_dir / "items.json").write_text("[]", encoding="utf-8")

        return f"Created {parent}/{slug}/ with summary.md and items.json"
    finally:
        _release_lock(fd)


@mcp.tool()
def add_fact(entity_slug: str, fact: str, category: str) -> str:
    """Add a fact to an existing entity's items.json.

    Args:
        entity_slug: Entity slug (e.g., "pi-cluster")
        fact: One-sentence fact to record
        category: One of: deployment, decision, configuration, lesson, preference, event, pending
    """
    if category not in VALID_CATEGORIES:
        return f"Error: invalid category '{category}'. Valid: {', '.join(sorted(VALID_CATEGORIES))}"

    entity_dir = _find_entity(entity_slug)
    if not entity_dir:
        return f"Error: entity '{entity_slug}' not found in Projects/People/Companies"

    items_path = entity_dir / "items.json"
    _validate_path(items_path)

    fd = _acquire_lock()
    try:
        items = []
        if items_path.exists():
            try:
                items = json.loads(items_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                items = []

        items.append({
            "date": TODAY,
            "fact": fact[:500],
            "category": category,
            "source": "mcp/life-write",
            "confidence": "single",
            "mentions": 1,
            "last_seen": TODAY,
            "temporal": False,
        })

        items_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        return f"Added fact to {entity_dir.parent.name}/{entity_slug} ({len(items)} total facts)"
    finally:
        _release_lock(fd)


if __name__ == "__main__":
    mcp.run()
