#!/usr/bin/env python3
"""Gather life maintenance data for the /weekly skill.

Outputs JSON to stdout. Works around known bugs in heartbeat_check.py
(regex mismatch) and carry_forward.py (checkbox-only format).

Usage:
    python3 weekly_review_data.py --mode full     # Full weekly review data
    python3 weekly_review_data.py --mode daily     # Lightweight daily maintenance
    python3 weekly_review_data.py --mode pulse     # Quick health check (alias for daily)

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = date.fromisoformat(os.environ.get("CONSOLIDATION_DATE", str(date.today())))

MODE = "full"
for i, arg in enumerate(sys.argv):
    if arg == "--mode" and i + 1 < len(sys.argv):
        MODE = sys.argv[i + 1]
if MODE == "pulse":
    MODE = "daily"

LOOKBACK_DAYS = 7 if MODE == "full" else 3


def daily_note_path(d: date) -> Path:
    return LIFE_DIR / "Daily" / str(d.year) / f"{d.month:02d}" / f"{d}.md"


def extract_section(content: str, header: str) -> str:
    pattern = rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    text = match.group(1).strip()
    return re.sub(r"<!--.*?-->", "", text).strip()


def parse_active_projects(content: str) -> list[dict]:
    """Parse active projects — handles actual note format: - [[slug]] — status"""
    section = extract_section(content, "Active Projects")
    if not section:
        return []
    projects = []
    for line in section.split("\n"):
        line = line.strip()
        # Actual format: - [[slug]] — status text
        match = re.match(r"^-\s+\[\[([^\]]+)\]\]\s*[—–\-]+\s*(.+)$", line)
        if not match:
            # Fallback: - **[[slug]]**: status (heartbeat_check format)
            match = re.match(r"^-\s+\*\*\[\[([^\]]+)\]\]\*\*:\s*(.+)$", line)
        if not match:
            # Fallback: - **slug**: status
            match = re.match(r"^-\s+\*\*([^*]+)\*\*:\s*(.+)$", line)
        if match:
            projects.append({
                "slug": match.group(1).strip().lower(),
                "status_text": match.group(2).strip(),
            })
    return projects


def parse_pending_items(content: str) -> list[str]:
    """Parse pending items — handles both checkbox and plain format."""
    section = extract_section(content, "Pending Items")
    if not section:
        return []
    items = []
    for line in section.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- [x]"):
            continue
        if stripped.startswith("- [ ] "):
            items.append(stripped[6:].strip())
        elif stripped.startswith("- ") and len(stripped) > 2:
            items.append(stripped[2:].strip())
    return items


def count_wiki_mentions(content: str) -> Counter:
    """Count [[slug]] mentions across content."""
    return Counter(m.lower() for m in re.findall(r"\[\[([^\]]+)\]\]", content))


def detect_flags(status_text: str) -> list[str]:
    text = status_text.lower()
    flags = []
    if "blocked" in text:
        flags.append("blocked")
    if "needs coding" in text:
        flags.append("needs_coding")
    if "needs work" in text or "needs attention" in text:
        flags.append("needs_attention")
    if "deadline" in text:
        flags.append("has_deadline")
    return flags


def scan_notes(lookback: int) -> dict:
    """Scan recent daily notes and aggregate data."""
    all_projects = {}
    all_pending = {}
    all_mentions = Counter()
    days_with_notes = 0
    days_without_notes = 0

    for i in range(lookback):
        d = TODAY - timedelta(days=i)
        path = daily_note_path(d)
        if not path.exists():
            days_without_notes += 1
            continue
        days_with_notes += 1
        content = path.read_text(encoding="utf-8")

        for proj in parse_active_projects(content):
            slug = proj["slug"]
            if slug not in all_projects:
                all_projects[slug] = {
                    "slug": slug,
                    "status_text": proj["status_text"],
                    "last_seen_date": str(d),
                    "staleness_days": i,
                    "blocked_days": 0,
                    "health_flags": detect_flags(proj["status_text"]),
                }
            if "blocked" in detect_flags(proj["status_text"]):
                all_projects[slug]["blocked_days"] += 1

        for item in parse_pending_items(content):
            if item not in all_pending:
                all_pending[item] = {
                    "text": item,
                    "first_seen": str(d),
                    "source_date": str(d),
                    "age_days": i,
                    "seen_count": 0,
                }
            all_pending[item]["seen_count"] += 1

        all_mentions += count_wiki_mentions(content)

    return {
        "projects": all_projects,
        "pending": all_pending,
        "mentions": all_mentions,
        "days_with_notes": days_with_notes,
        "days_without_notes": days_without_notes,
    }


def get_entity_dirs() -> set[str]:
    """Get all existing entity directory slugs."""
    dirs = set()
    for parent in ["Projects", "People", "Companies"]:
        parent_path = LIFE_DIR / parent
        if parent_path.exists():
            for d in parent_path.iterdir():
                if d.is_dir() and not d.name.startswith("_"):
                    dirs.add(d.name)
    return dirs


def get_fact_health() -> dict:
    """Import and run fact health scan from generate_decay_dashboard."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from generate_decay_dashboard import scan_entities, HEALTHY_LEVELS
        entities = scan_entities()
        total = sum(e["total"] for e in entities)
        healthy = sum(
            sum(e["counts"].get(lv, 0) for lv in HEALTHY_LEVELS)
            for e in entities
        )
        stale = sum(e["counts"].get("stale", 0) for e in entities)
        archived = sum(e["counts"].get("archived", 0) for e in entities)
        decaying_soon = []
        for e in entities:
            for f in e["decaying_soon"]:
                decaying_soon.append({
                    "entity": e["slug"],
                    "fact": f["fact"],
                    "days_remaining": f["threshold"] - f["days"],
                })
        return {
            "total": total,
            "healthy": healthy,
            "stale": stale,
            "archived": archived,
            "decaying_soon": decaying_soon[:10],
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "healthy": 0, "stale": 0, "archived": 0, "decaying_soon": []}


def get_entity_stats() -> dict:
    """Import and run entity graph stats."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from entity_graph import graph_stats
        return graph_stats()
    except Exception as e:
        return {"error": str(e), "entity_count": 0, "edge_count": 0}


def get_orphan_entities() -> list[str]:
    """Read orphan entities from nightly lint log."""
    log_path = LIFE_DIR / "logs" / "pending-entities.log"
    if not log_path.exists():
        return []
    orphans = set()
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            orphans.add(parts[1].strip())
    return sorted(orphans)


def get_week_summary_path() -> tuple[str, bool]:
    """Check if this week's summary exists."""
    iso_year, iso_week, _ = TODAY.isocalendar()
    filename = f"W{iso_week:02d}-summary.md"
    path = LIFE_DIR / "Daily" / str(iso_year) / filename
    return str(path), path.exists()


def build_projects_to_review(
    active_slugs: set[str], entity_dirs: set[str], note_data: dict
) -> dict:
    """Cross-reference project dirs with recent activity."""
    project_dirs = set()
    projects_path = LIFE_DIR / "Projects"
    if projects_path.exists():
        for d in projects_path.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                project_dirs.add(d.name)

    possibly_stale = []
    for slug in project_dirs - active_slugs:
        mention_count = note_data["mentions"].get(slug, 0)
        if mention_count == 0:
            possibly_stale.append(slug)

    possibly_complete = []
    for slug in project_dirs:
        summary_path = LIFE_DIR / "Projects" / slug / "summary.md"
        if summary_path.exists():
            content = summary_path.read_text(encoding="utf-8")
            if re.search(r"status:\s*(completed?|done|archived)", content, re.IGNORECASE):
                possibly_complete.append(slug)

    needs_unblocking = []
    for slug, proj in note_data["projects"].items():
        if proj["blocked_days"] >= 2:
            needs_unblocking.append({"slug": slug, "blocked_days": proj["blocked_days"]})

    return {
        "possibly_stale": possibly_stale,
        "possibly_complete": possibly_complete,
        "needs_unblocking": needs_unblocking,
    }


def main() -> None:
    note_data = scan_notes(LOOKBACK_DAYS)
    entity_dirs = get_entity_dirs()
    active_slugs = set(note_data["projects"].keys())

    projects = []
    for slug, proj in note_data["projects"].items():
        proj["entity_exists"] = slug in entity_dirs
        fact_count = 0
        items_path = LIFE_DIR / "Projects" / slug / "items.json"
        if items_path.exists():
            try:
                facts = json.loads(items_path.read_text(encoding="utf-8"))
                fact_count = len(facts) if isinstance(facts, list) else 0
            except (json.JSONDecodeError, OSError):
                pass
        proj["fact_count"] = fact_count
        projects.append(proj)

    pending = sorted(
        note_data["pending"].values(),
        key=lambda x: x["age_days"],
        reverse=True,
    )

    promotion_candidates = []
    if MODE == "full":
        for topic, count in note_data["mentions"].most_common():
            if count >= 3 and topic not in entity_dirs:
                promotion_candidates.append({
                    "topic": topic,
                    "mention_count": count,
                    "has_entity": False,
                })

    iso_year, iso_week, _ = TODAY.isocalendar()
    summary_path, summary_exists = get_week_summary_path()

    result = {
        "mode": MODE,
        "generated": str(TODAY),
        "week_number": f"W{iso_week:02d}",
        "lookback_days": LOOKBACK_DAYS,
        "days_with_notes": note_data["days_with_notes"],
        "days_without_notes": note_data["days_without_notes"],
        "week_summary_path": summary_path,
        "week_summary_exists": summary_exists,
        "active_projects": projects,
        "pending_items": pending,
        "entity_stats": get_entity_stats(),
        "fact_health": get_fact_health(),
    }

    if MODE == "full":
        result["promotion_candidates"] = promotion_candidates
        result["orphan_entities"] = get_orphan_entities()
        result["projects_to_review"] = build_projects_to_review(
            active_slugs, entity_dirs, note_data
        )

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
