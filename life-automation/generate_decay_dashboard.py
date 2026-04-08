#!/usr/bin/env python3
"""Generate a fact confidence decay dashboard.

Scans all items.json files and reports fact health by entity.

Usage:
    python3 generate_decay_dashboard.py              # Generate dashboard
    python3 generate_decay_dashboard.py --dry-run    # Print to stdout

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    TODAY = str(date.today())

DRY_RUN = "--dry-run" in sys.argv

# Thresholds (matching decay_facts.py)
STALE_THRESHOLD = 14
ESTABLISHED_STALE_THRESHOLD = 30
TEMPORAL_STALE_THRESHOLD = 7
TEMPORAL_EST_STALE_THRESHOLD = 14

ENTITY_PATTERNS = [
    "Projects/*/items.json",
    "People/*/items.json",
    "Companies/*/items.json",
]

CONFIDENCE_LEVELS = ["single", "confirmed", "established", "stale", "archived", "superseded"]
HEALTHY_LEVELS = {"single", "confirmed", "established"}


def _days_since(date_str: str) -> int:
    """Days between date_str and TODAY."""
    try:
        return (date.fromisoformat(TODAY) - date.fromisoformat(date_str)).days
    except (ValueError, TypeError):
        return 999


def _stale_threshold(item: dict) -> int:
    """Get the stale threshold for a fact based on its type."""
    temporal = item.get("temporal", False)
    established = item.get("confidence") == "established"
    if temporal and established:
        return TEMPORAL_EST_STALE_THRESHOLD
    if temporal:
        return TEMPORAL_STALE_THRESHOLD
    if established:
        return ESTABLISHED_STALE_THRESHOLD
    return STALE_THRESHOLD


def scan_entities() -> list[dict]:
    """Scan all items.json files and return entity health data."""
    results = []
    for pattern in ENTITY_PATTERNS:
        for items_path in sorted(LIFE_DIR.glob(pattern)):
            entity_slug = items_path.parent.name
            entity_type = items_path.parent.parent.name
            try:
                items = json.loads(items_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(items, list) or not items:
                continue

            counts: dict[str, int] = {level: 0 for level in CONFIDENCE_LEVELS}
            stale_facts: list[dict] = []
            decaying_soon: list[dict] = []

            for item in items:
                conf = item.get("confidence", "single")
                counts[conf] = counts.get(conf, 0) + 1

                last_seen = item.get("last_seen", "")
                if not last_seen:
                    continue
                days = _days_since(last_seen)
                threshold = _stale_threshold(item)

                if conf in HEALTHY_LEVELS and days >= threshold:
                    stale_facts.append({
                        "fact": item.get("fact", "?")[:80],
                        "last_seen": last_seen,
                        "days": days,
                    })
                elif conf in HEALTHY_LEVELS and days >= threshold - 3:
                    decaying_soon.append({
                        "fact": item.get("fact", "?")[:80],
                        "last_seen": last_seen,
                        "days": days,
                        "threshold": threshold,
                    })

            results.append({
                "slug": entity_slug,
                "type": entity_type,
                "total": len(items),
                "counts": counts,
                "stale_facts": stale_facts,
                "decaying_soon": decaying_soon,
            })
    return results


def generate() -> str:
    """Generate the dashboard markdown."""
    entities = scan_entities()

    total_facts = sum(e["total"] for e in entities)
    total_healthy = sum(
        sum(e["counts"].get(level, 0) for level in HEALTHY_LEVELS) for e in entities
    )
    total_stale = sum(e["counts"].get("stale", 0) for e in entities)
    total_archived = sum(e["counts"].get("archived", 0) for e in entities)

    lines = [
        f"---\ngenerated: {TODAY}\n---\n",
        "# Fact Confidence Dashboard\n",
    ]

    # Summary
    if not entities:
        lines.append("No facts tracked yet.\n")
        return "\n".join(lines)

    pct_h = f"{total_healthy / total_facts * 100:.0f}%" if total_facts else "0%"
    pct_s = f"{total_stale / total_facts * 100:.0f}%" if total_facts else "0%"
    pct_a = f"{total_archived / total_facts * 100:.0f}%" if total_facts else "0%"

    lines.append("## Summary\n")
    lines.append(f"- Total facts: {total_facts}")
    lines.append(f"- Healthy: {total_healthy} ({pct_h}) | Stale: {total_stale} ({pct_s}) | Archived: {total_archived} ({pct_a})\n")

    # By Entity table
    lines.append("## By Entity\n")
    lines.append("| Entity | Total | Single | Confirmed | Established | Stale | Archived |")
    lines.append("|--------|-------|--------|-----------|-------------|-------|----------|")
    for e in sorted(entities, key=lambda x: x["total"], reverse=True):
        c = e["counts"]
        lines.append(
            f"| {e['slug']} | {e['total']} | {c.get('single', 0)} | "
            f"{c.get('confirmed', 0)} | {c.get('established', 0)} | "
            f"{c.get('stale', 0)} | {c.get('archived', 0)} |"
        )
    lines.append("")

    # Attention Needed
    attention = [e for e in entities if e["stale_facts"]]
    if attention:
        lines.append("## Attention Needed\n")
        for e in attention:
            for f in e["stale_facts"][:3]:
                lines.append(f"- **{e['slug']}**: \"{f['fact']}\" (last seen {f['last_seen']}, {f['days']} days ago)")
        lines.append("")

    # Decaying Soon
    decaying = [e for e in entities if e["decaying_soon"]]
    if decaying:
        lines.append("## Decaying Soon (within 3 days of threshold)\n")
        for e in decaying:
            for f in e["decaying_soon"][:3]:
                lines.append(
                    f"- **{e['slug']}**: \"{f['fact']}\" "
                    f"(last seen {f['days']} days ago, threshold {f['threshold']})"
                )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    content = generate()

    if DRY_RUN:
        print(content)
        return

    out_path = LIFE_DIR / "logs" / "decay-dashboard.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"[decay-dashboard] Written to {out_path}")


if __name__ == "__main__":
    main()
