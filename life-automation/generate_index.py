#!/usr/bin/env python3
"""Generate ~/life/index.md — a structured catalog of the knowledge base.

Regenerated nightly. Only writes if body content changed (avoids noisy git diffs).

Usage:
    python3 generate_index.py              # Generate/update index.md
    python3 generate_index.py --dry-run    # Print to stdout, don't write

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import re
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    from datetime import date
    TODAY = str(date.today())

DRY_RUN = "--dry-run" in sys.argv
ENTITY_TYPES = ["Projects", "People", "Companies"]


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter for comparison."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            return text[end + 3:].strip()
    return text.strip()


def _first_content_line(path: Path) -> str:
    """Get first non-frontmatter, non-empty line from a markdown file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return ""


def _count_items(path: Path) -> int:
    """Count entries in an items.json file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return 0


def _get_status(summary_path: Path) -> str:
    """Extract status from summary.md frontmatter."""
    try:
        text = summary_path.read_text(encoding="utf-8")
        match = re.search(r"^status:\s*(\S+)", text, re.MULTILINE)
        return match.group(1) if match else "unknown"
    except (OSError, UnicodeDecodeError):
        return "unknown"


def _get_last_updated(summary_path: Path) -> str:
    """Extract last-updated from summary.md frontmatter."""
    try:
        text = summary_path.read_text(encoding="utf-8")
        match = re.search(r"^last-updated:\s*(\S+)", text, re.MULTILINE)
        return match.group(1) if match else ""
    except (OSError, UnicodeDecodeError):
        return ""


def scan_entities(entity_type: str) -> list[dict]:
    """Scan an entity type directory and return metadata."""
    entity_dir = LIFE_DIR / entity_type
    if not entity_dir.is_dir():
        return []
    results = []
    for d in sorted(entity_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        summary = d / "summary.md"
        if not summary.exists():
            continue
        desc = _first_content_line(summary) or d.name
        items_count = _count_items(d / "items.json")
        status = _get_status(summary)
        updated = _get_last_updated(summary)
        results.append({
            "slug": d.name, "desc": desc, "items": items_count,
            "status": status, "updated": updated,
        })
    return results


def scan_areas() -> list[str]:
    """List files in Areas/."""
    areas_dir = LIFE_DIR / "Areas"
    if not areas_dir.is_dir():
        return []
    results = []
    for d in sorted(areas_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        files = [f.stem for f in sorted(d.iterdir()) if f.suffix == ".md"]
        if files:
            results.append(f"- {d.name}/ — {', '.join(files)}")
    return results


def scan_resources() -> list[str]:
    """List resources."""
    res_dir = LIFE_DIR / "Resources"
    if not res_dir.is_dir():
        return []
    results = []
    for d in sorted(res_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        desc = _first_content_line(d / "summary.md") if (d / "summary.md").exists() else "(empty)"
        results.append(f"- {d.name}/ — {desc}")
    return results


def scan_daily() -> list[str]:
    """Count daily notes and session digests per month."""
    daily_dir = LIFE_DIR / "Daily"
    if not daily_dir.is_dir():
        return []
    results = []
    for year_dir in sorted(daily_dir.iterdir()):
        if not year_dir.is_dir() or year_dir.name.startswith(("_", ".")):
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            notes = len(list(month_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md")))
            digests = sum(
                sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
                for f in month_dir.glob("sessions-digest-*.jsonl")
                if f.stat().st_size > 0
            )
            label = f"{year_dir.name}-{month_dir.name}"
            parts = []
            if notes:
                parts.append(f"{notes} notes")
            if digests:
                parts.append(f"{digests} sessions")
            if parts:
                results.append(f"- {label}: {', '.join(parts)}")
    return results


def count_md_files() -> int:
    """Count all .md files in ~/life (excluding .git and index.md itself)."""
    return len([f for f in LIFE_DIR.rglob("*.md")
                if ".git" not in f.parts and f.name != "index.md"])


def count_relationships() -> tuple[int, str]:
    """Count relationships and get last update date."""
    try:
        data = json.loads((LIFE_DIR / "relationships.json").read_text(encoding="utf-8"))
        if not data:
            return 0, ""
        last = max(e.get("last_seen", "") for e in data)
        return len(data), last
    except (OSError, json.JSONDecodeError):
        return 0, ""


def generate() -> str:
    """Generate the index.md content."""
    lines = [
        f"---\ngenerated: {TODAY}\nauto_maintained: true\n---\n",
        "# Knowledge Base Index\n",
        "> Auto-generated nightly. Do not edit manually.\n",
    ]

    for entity_type in ENTITY_TYPES:
        entities = scan_entities(entity_type)
        active = [e for e in entities if e["status"] == "active"]
        count_label = f" ({len(active)} active)" if active else ""
        lines.append(f"## {entity_type}{count_label}\n")
        if not entities:
            lines.append("_(none yet)_\n")
        else:
            for e in entities:
                parts = []
                if e["items"]:
                    parts.append(f"{e['items']} facts")
                if e["updated"]:
                    parts.append(f"updated {e['updated']}")
                meta = f" ({', '.join(parts)})" if parts else ""
                lines.append(f"- **[[{e['slug']}]]** — {e['desc']}{meta}")
            lines.append("")

    areas = scan_areas()
    lines.append("## Areas\n")
    if areas:
        lines.extend(areas)
        lines.append("")
    else:
        lines.append("_(none yet)_\n")

    resources = scan_resources()
    lines.append("## Resources\n")
    if resources:
        lines.extend(resources)
        lines.append("")
    else:
        lines.append("_(none yet)_\n")

    daily = scan_daily()
    lines.append("## Daily Notes\n")
    if daily:
        lines.extend(daily)
        lines.append("")
    else:
        lines.append("_(none yet)_\n")

    md_count = count_md_files()
    rel_count, rel_date = count_relationships()
    stats_parts = [f"{md_count} markdown files"]
    if rel_count:
        stats_parts.append(f"{rel_count} relationships")
        if rel_date:
            stats_parts.append(f"last updated {rel_date}")
    lines.append("## Stats\n")
    lines.append(f"- {', '.join(stats_parts)}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    content = generate()

    if DRY_RUN:
        print(content)
        return

    index_path = LIFE_DIR / "index.md"
    if index_path.exists():
        old_body = _strip_frontmatter(index_path.read_text(encoding="utf-8"))
        new_body = _strip_frontmatter(content)
        if old_body == new_body:
            print("[index] No changes — skipping write")
            return

    index_path.write_text(content, encoding="utf-8")
    print(f"[index] Updated {index_path}")


if __name__ == "__main__":
    main()
