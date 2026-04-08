#!/usr/bin/env python3
"""Compile-once wiki: LLM rewrites entity summaries from facts.

Only rewrites entities with `auto_maintained: true` in frontmatter.
Preserves manual prose — only updates "What Matters Right Now" and "Key Facts" sections.

Usage:
    python3 rewrite_summaries.py              # Rewrite all eligible entities
    python3 rewrite_summaries.py --entity slug  # Rewrite a specific entity
    python3 rewrite_summaries.py --dry-run    # Preview without writing

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    from datetime import date
    TODAY = str(date.today())

DRY_RUN = "--dry-run" in sys.argv
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
MIN_FACTS = 3  # Only rewrite entities with enough facts

ENTITY_PATTERNS = [
    "Projects/*/summary.md",
    "People/*/summary.md",
    "Companies/*/summary.md",
]


def _is_auto_maintained(summary_path: Path) -> bool:
    """Check if entity opts in to auto-rewriting."""
    try:
        text = summary_path.read_text(encoding="utf-8")
        return "auto_maintained: true" in text
    except OSError:
        return False


def _get_last_rewritten(summary_path: Path) -> str:
    """Extract last-rewritten date from frontmatter."""
    try:
        text = summary_path.read_text(encoding="utf-8")
        match = re.search(r"^last-rewritten:\s*(\S+)", text, re.MULTILINE)
        return match.group(1) if match else ""
    except OSError:
        return ""


def _load_facts(entity_dir: Path) -> list[dict]:
    """Load active facts from items.json."""
    items_path = entity_dir / "items.json"
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
        return [i for i in items if i.get("confidence") not in ("superseded", "archived")]
    except (OSError, json.JSONDecodeError):
        return []


def _rewrite_sections(summary_path: Path, slug: str, facts: list[dict]) -> str | None:
    """Call Claude Haiku to generate updated sections. Returns new section content."""
    fact_lines = "\n".join(f"- [{f.get('category', '?')}] {f.get('fact', '')}" for f in facts[:30])

    prompt = (
        f"You are updating a wiki page for entity '{slug}'. "
        f"Based ONLY on these facts, write two short sections. "
        f"Do NOT add information not in the facts. Do NOT hallucinate.\n\n"
        f"Facts:\n{fact_lines}\n\n"
        f"Output exactly this format (no code fences):\n"
        f"## What Matters Right Now\n[2-3 bullet points about current state]\n\n"
        f"## Key Facts\n[3-5 bullet points of the most important facts]"
    )

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--output-format", "text", "--no-session-persistence",
             "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[rewrite] ERROR: {slug} — {e}", file=sys.stderr)
    return None


def rewrite_entity(summary_path: Path) -> bool:
    """Rewrite an entity's auto-maintained sections. Returns True if rewritten."""
    entity_dir = summary_path.parent
    slug = entity_dir.name
    entity_type = entity_dir.parent.name

    facts = _load_facts(entity_dir)
    if len(facts) < MIN_FACTS:
        return False

    if DRY_RUN:
        print(f"[rewrite] DRY RUN: {entity_type}/{slug} ({len(facts)} facts)")
        return False

    # Backup
    backup = summary_path.with_suffix(".md.bak")
    shutil.copy2(summary_path, backup)

    # Get new sections from LLM
    new_sections = _rewrite_sections(summary_path, slug, facts)
    if not new_sections:
        backup.unlink(missing_ok=True)
        return False

    # Read current summary
    text = summary_path.read_text(encoding="utf-8")

    # Replace "What Matters Right Now" and "Key Facts" sections
    for section in ["What Matters Right Now", "Key Facts"]:
        # Remove old section
        pattern = rf"(## {section}\n)([\s\S]*?)(?=\n## |\Z)"
        text = re.sub(pattern, "", text)

    # Append new sections before "Open Questions" or at end
    if "## Open Questions" in text:
        text = text.replace("## Open Questions", f"{new_sections}\n\n## Open Questions")
    else:
        text = text.rstrip() + f"\n\n{new_sections}\n"

    # Update last-rewritten in frontmatter
    if "last-rewritten:" in text:
        text = re.sub(r"last-rewritten:\s*\S+", f"last-rewritten: {TODAY}", text)
    else:
        text = text.replace("status: active", f"status: active\nlast-rewritten: {TODAY}", 1)

    summary_path.write_text(text, encoding="utf-8")
    print(f"[rewrite] Updated {entity_type}/{slug} ({len(facts)} facts)")
    return True


def main() -> None:
    target = None
    for i, arg in enumerate(sys.argv):
        if arg == "--entity" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]

    rewritten = 0
    for pattern in ENTITY_PATTERNS:
        for summary_path in sorted(LIFE_DIR.glob(pattern)):
            if summary_path.parent.name.startswith("_"):
                continue
            if target and summary_path.parent.name != target:
                continue
            if not _is_auto_maintained(summary_path):
                continue
            if rewrite_entity(summary_path):
                rewritten += 1

    if not rewritten and not DRY_RUN:
        print("[rewrite] No entities eligible (need auto_maintained: true in frontmatter + 3+ facts)")
    print(f"[rewrite] Done — {rewritten} entities rewritten")


if __name__ == "__main__":
    main()
