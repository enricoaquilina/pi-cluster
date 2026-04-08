#!/usr/bin/env python3
"""Lint ~/life/ knowledge base for consistency issues.

Checks:
  1. Orphan relationships — entities in relationships.json without entity dirs
  2. Stale relationships — edges with last_seen > 30 days
  3. Empty entity dirs — dirs in Projects/People/Companies without summary.md

Usage:
    python3 lint_knowledge.py              # Check all
    python3 lint_knowledge.py --dry-run    # Same (lint is read-only by default)
    python3 lint_knowledge.py --json       # Output structured JSON

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    TODAY = str(date.today())

JSON_OUTPUT = "--json" in sys.argv

ENTITY_TYPES = ["Projects", "People", "Companies"]
TYPE_MAP = {"person": "People", "project": "Projects", "company": "Companies"}

findings: list[dict] = []


def finding(category: str, message: str, path: str = "") -> None:
    findings.append({"category": category, "message": message, "path": path})
    if not JSON_OUTPUT:
        print(f"[lint] {category}: {message}")


def check_orphan_relationships(relationships: list[dict]) -> None:
    """Check that all entities in relationships.json have entity dirs."""
    seen_slugs: set[str] = set()
    for edge in relationships:
        for role in ("from", "to"):
            slug = edge.get(role, "")
            role_type = edge.get(f"{role}_type", "")
            parent = TYPE_MAP.get(role_type, "")
            if not slug or not parent or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            entity_dir = LIFE_DIR / parent / slug
            if not entity_dir.is_dir():
                finding(
                    "ORPHAN",
                    f"'{slug}' ({role_type}) in relationships.json has no {parent}/{slug}/ dir",
                    str(entity_dir),
                )


def check_stale_relationships(relationships: list[dict]) -> None:
    """Check for relationships not seen in 30+ days."""
    try:
        cutoff = str(date.fromisoformat(TODAY) - timedelta(days=30))
    except ValueError:
        return
    for edge in relationships:
        last_seen = edge.get("last_seen", "")
        if last_seen and last_seen < cutoff:
            finding(
                "STALE",
                f"{edge.get('from', '?')} → {edge.get('to', '?')} last seen {last_seen}",
            )


def check_empty_entity_dirs() -> None:
    """Check for entity dirs without summary.md."""
    for entity_type in ENTITY_TYPES:
        entity_dir = LIFE_DIR / entity_type
        if not entity_dir.is_dir():
            continue
        for d in sorted(entity_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("_") or d.name.startswith("."):
                continue
            if not (d / "summary.md").exists():
                finding(
                    "EMPTY_DIR",
                    f"{entity_type}/{d.name}/ has no summary.md",
                    str(d),
                )


def _load_aliases() -> dict[str, str]:
    """Load .wiki-link-aliases file. Returns slug→canonical mapping."""
    aliases: dict[str, str] = {}
    path = LIFE_DIR / ".wiki-link-aliases"
    if not path.exists():
        return aliases
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            aliases[key.strip()] = val.strip()
    return aliases


def check_wiki_links() -> None:
    """Check [[wiki-links]] in recent daily notes against entity dirs."""
    aliases = _load_aliases()
    # Build set of all entity dir names
    entity_dirs: set[str] = set()
    for etype in ENTITY_TYPES:
        edir = LIFE_DIR / etype
        if edir.is_dir():
            entity_dirs.update(
                d.name for d in edir.iterdir()
                if d.is_dir() and not d.name.startswith("_")
            )

    # Scan daily notes from last 30 days (by filename date)
    cutoff = date.fromisoformat(TODAY) - timedelta(days=30)
    seen_slugs: set[str] = set()
    daily_dir = LIFE_DIR / "Daily"
    if not daily_dir.is_dir():
        return
    for note in daily_dir.rglob("*.md"):
        try:
            note_date = date.fromisoformat(note.stem)
        except ValueError:
            continue
        if note_date < cutoff:
            continue
        try:
            text = note.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in re.finditer(r"\[\[([a-z0-9-]+)\]\]", text):
            slug = match.group(1)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            canonical = aliases.get(slug, slug)
            if canonical == "SKIP":
                continue
            if canonical not in entity_dirs:
                finding(
                    "BROKEN_LINK",
                    f"[[{slug}]] in {note.name} — no entity dir found",
                    str(note),
                )


def main() -> None:
    # Load relationships
    rel_path = LIFE_DIR / "relationships.json"
    relationships: list[dict] = []
    try:
        relationships = json.loads(rel_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[lint] SKIP: relationships.json not found")
    except json.JSONDecodeError as e:
        print(f"[lint] SKIP: relationships.json malformed ({e})")

    if relationships:
        check_orphan_relationships(relationships)
        check_stale_relationships(relationships)

    check_empty_entity_dirs()
    check_wiki_links()

    if JSON_OUTPUT:
        json.dump(findings, sys.stdout, indent=2)
        print()
    elif not findings:
        print("[lint] All clean — no issues found")
    else:
        print(f"[lint] {len(findings)} issue(s) found")


if __name__ == "__main__":
    main()
