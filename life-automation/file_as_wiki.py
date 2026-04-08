#!/usr/bin/env python3
"""File session insights as wiki pages in ~/life.

Implements Karpathy's "query-as-contribution" pattern: good answers
from sessions become permanent wiki knowledge.

Usage:
    file_as_wiki.py --title "Redis Caching" --content "..." --type resource
    file_as_wiki.py --title "Architecture Note" --entity pi-cluster
    file_as_wiki.py --dry-run  # preview without writing

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = str(date.today())
DRY_RUN = "--dry-run" in sys.argv


def _slugify(title: str) -> str:
    slug = title.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def file_as_resource(title: str, content: str, session_id: str = "") -> str:
    """Create a wiki page under Resources/."""
    slug = _slugify(title)
    if not slug:
        return "Error: title produces empty slug"

    out_dir = LIFE_DIR / "Resources" / slug
    out_path = out_dir / "summary.md"

    if out_path.exists():
        return f"Error: Resources/{slug}/summary.md already exists"

    frontmatter = (
        f"---\ntype: resource\nname: {title}\ncreated: {TODAY}\n"
        f"source: session/{session_id or 'manual'}\n---\n\n"
    )
    full_content = frontmatter + content + "\n"

    if DRY_RUN:
        print(f"[wiki] DRY RUN: Would create Resources/{slug}/summary.md ({len(full_content)} chars)")
        return f"Would create Resources/{slug}/summary.md"

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full_content, encoding="utf-8")
    return f"Created Resources/{slug}/summary.md"


def file_to_entity(entity_slug: str, content: str, session_id: str = "") -> str:
    """Append content to an existing entity's summary.md."""
    # Find entity
    for parent in ["Projects", "People", "Companies"]:
        entity_dir = LIFE_DIR / parent / entity_slug
        if entity_dir.is_dir():
            summary = entity_dir / "summary.md"
            if not summary.exists():
                return f"Error: {parent}/{entity_slug}/summary.md not found"

            provenance = f"\n\n<!-- filed from session/{session_id or 'manual'} on {TODAY} -->\n"
            appendix = provenance + content + "\n"

            if DRY_RUN:
                print(f"[wiki] DRY RUN: Would append to {parent}/{entity_slug}/summary.md ({len(appendix)} chars)")
                return f"Would append to {parent}/{entity_slug}/summary.md"

            with open(summary, "a", encoding="utf-8") as f:
                f.write(appendix)
            return f"Appended to {parent}/{entity_slug}/summary.md"

    return f"Error: entity '{entity_slug}' not found"


def main() -> None:
    title = ""
    content = ""
    entity = ""
    session_id = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        elif args[i] == "--content" and i + 1 < len(args):
            content = args[i + 1]
            i += 2
        elif args[i] == "--entity" and i + 1 < len(args):
            entity = args[i + 1]
            i += 2
        elif args[i] == "--type" and i + 1 < len(args):
            i += 2  # accepted but not used (resource is default)
        elif args[i] == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        else:
            i += 1

    # Read content from stdin if not provided
    if not content and not sys.stdin.isatty():
        content = sys.stdin.read()

    if not content:
        print("Usage: file_as_wiki.py --title 'Title' --content 'Content' [--entity slug | --type resource]")
        print("  Or pipe content: echo 'Content' | file_as_wiki.py --title 'Title'")
        sys.exit(1)

    if entity:
        result = file_to_entity(entity, content, session_id)
    else:
        if not title:
            print("Error: --title required for new resource pages", file=sys.stderr)
            sys.exit(1)
        result = file_as_resource(title, content, session_id)

    print(f"[wiki] {result}")


if __name__ == "__main__":
    main()
