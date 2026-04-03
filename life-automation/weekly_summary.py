#!/usr/bin/env python3
"""
Generate weekly summary from the past 7 daily notes.
Output: ~/life/Daily/YYYY/WXX-summary.md
"""
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = date.fromisoformat(os.environ.get("CONSOLIDATION_DATE", str(date.today())))
DRY_RUN = "--dry-run" in sys.argv

def get_daily_notes(ref_date: date, days: int = 7) -> list[tuple[str, str]]:
    """Return (date_str, content) for the last N days that have notes."""
    today = ref_date
    notes = []
    for i in range(days):
        d = today - timedelta(days=i)
        path = LIFE_DIR / "Daily" / str(d.year) / f"{d.month:02d}" / f"{d}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            # Skip template-only notes (no real content)
            stripped = re.sub(r'---.*?---', '', content, flags=re.DOTALL).strip()
            stripped = re.sub(r'<!--.*?-->', '', stripped).strip()
            stripped = re.sub(r'^## .+$', '', stripped, flags=re.MULTILINE).strip()
            if stripped and stripped != "- [ ]":
                notes.append((str(d), content))
    return list(reversed(notes))  # chronological order

def extract_section(content: str, header: str) -> str:
    """Extract content under a ## header."""
    pattern = rf'^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)'
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    text = match.group(1).strip()
    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text).strip()
    return text

def generate_summary(notes: list[tuple[str, str]], ref_date: date) -> str:
    """Generate weekly summary markdown from daily notes."""
    week_num = ref_date.isocalendar()[1]
    year = ref_date.year

    all_work = []
    all_decisions = []
    all_pending = []
    all_projects = set()
    all_facts = []

    for date_str, content in notes:
        work = extract_section(content, "What We Worked On")
        if work:
            all_work.append(f"### {date_str}\n{work}")

        decisions = extract_section(content, "Decisions Made")
        if decisions:
            all_decisions.append(f"**{date_str}:** {decisions}")

        pending = extract_section(content, "Pending Items")
        if pending and pending != "- [ ]":
            for line in pending.split("\n"):
                line = line.strip()
                if line.startswith("- [ ]"):
                    all_pending.append(f"{line} *(from {date_str})*")

        projects = extract_section(content, "Active Projects")
        if projects:
            for line in projects.split("\n"):
                line = line.strip()
                if line.startswith("- **") or line.startswith("**"):
                    all_projects.add(line)

        facts = extract_section(content, "New Facts Learned")
        if facts:
            all_facts.append(facts)

    summary = f"""---
type: weekly-summary
week: W{week_num:02d}
year: {year}
period: {notes[0][0] if notes else 'N/A'} to {notes[-1][0] if notes else 'N/A'}
days-with-notes: {len(notes)}
---

# Weekly Summary — {year} W{week_num:02d}

## Work Log
{chr(10).join(all_work) if all_work else '_No work logged this week._'}

## Key Decisions
{chr(10).join(all_decisions) if all_decisions else '_No decisions recorded._'}

## Open Pending Items
{chr(10).join(all_pending) if all_pending else '_All items resolved._'}

## Active Projects
{chr(10).join(sorted(all_projects)) if all_projects else '_No projects tracked._'}

## Facts Learned
{chr(10).join(all_facts) if all_facts else '_No new facts._'}
"""
    return summary

def write_summary() -> str | None:
    """Generate and write weekly summary. Returns path or None."""
    notes = get_daily_notes(TODAY, 7)
    if not notes:
        print("[weekly] No daily notes found for the past 7 days")
        return None

    summary = generate_summary(notes, TODAY)
    week_num = TODAY.isocalendar()[1]

    out_dir = LIFE_DIR / "Daily" / str(TODAY.year)
    out_path = out_dir / f"W{week_num:02d}-summary.md"

    if not DRY_RUN:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(summary, encoding="utf-8")

    print(f"[weekly] {'(dry) ' if DRY_RUN else ''}Summary written: {out_path}")
    return str(out_path)

if __name__ == "__main__":
    write_summary()
