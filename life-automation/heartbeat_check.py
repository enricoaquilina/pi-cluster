#!/usr/bin/env python3
"""
Heartbeat check: reads Active Projects from today's daily note,
detects staleness and blocked status, escalates if needed.
"""
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = date.fromisoformat(os.environ.get("CONSOLIDATION_DATE", str(date.today())))
DRY_RUN = "--dry-run" in sys.argv
JSON_OUTPUT = "--json" in sys.argv


def daily_note_path(d: date) -> Path:
    return LIFE_DIR / "Daily" / str(d.year) / f"{d.month:02d}" / f"{d}.md"


def extract_section(content: str, header: str) -> str:
    pattern = rf'^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)'
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    text = match.group(1).strip()
    return re.sub(r'<!--.*?-->', '', text).strip()


def parse_active_projects(content: str) -> list[dict]:
    section = extract_section(content, "Active Projects")
    if not section:
        return []
    projects = []
    for line in section.split("\n"):
        line = line.strip()
        # Match: - **[[slug]]**: status text  OR  - **slug**: status text
        match = re.match(r'^-\s+\*\*\[\[([^\]]+)\]\]\*\*:\s*(.+)$', line)
        if not match:
            match = re.match(r'^-\s+\*\*([^*]+)\*\*:\s*(.+)$', line)
        if match:
            projects.append({
                "slug": match.group(1).strip().lower(),
                "status_text": match.group(2).strip(),
            })
    return projects


def detect_flags(status_text: str) -> list[str]:
    text = status_text.lower()
    flags = []
    if "blocked" in text:
        flags.append("blocked")
    if "needs coding session" in text:
        flags.append("needs_coding")
    if "needs work" in text or "needs attention" in text:
        flags.append("needs_attention")
    if "in progress" in text:
        flags.append("in_progress")
    if "complete" in text or "operational" in text:
        flags.append("stable")
    if "running" in text and "blocked" not in text:
        flags.append("running")
    return flags


def days_since_last_session(slug: str, today: date, digest_dir: Path = None) -> int:
    """Count days since the last Claude Code session that touched this project.
    Returns 0 if a session was found today, max_days if never found."""
    if digest_dir is None:
        digest_dir = LIFE_DIR / "Daily"
    for i in range(30):
        d = today - timedelta(days=i)
        digest = digest_dir / str(d.year) / f"{d.month:02d}" / f"sessions-digest-{d}.jsonl"
        if not digest.exists():
            continue
        for line in digest.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                # BUG: checks summary instead of files_touched — slug "pi" matches "pipeline"
                if slug in entry.get("summary", ""):
                    return i
            except json.JSONDecodeError:
                continue
    return 30


def get_project_health_score(slug: str, today: date) -> float:
    """Compute a 0-1 health score for a project.
    Based on staleness (days since last mention) and consecutive blocked days.
    Returns 1.0 for healthy, 0.0 for critical."""
    staleness = compute_staleness(slug, today)
    blocked = compute_blocked_days(slug, today)
    # Freshness decays with staleness, penalized by blocked days
    score = 1.0 / staleness - (blocked * 0.1)
    return max(0.0, min(1.0, score))


def compute_staleness(slug: str, today: date, max_days: int = 30) -> int:
    for i in range(max_days):
        d = today - timedelta(days=i)
        path = daily_note_path(d)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if f"[[{slug}]]" in content or f"**{slug}**" in content:
                return i
    return max_days


def compute_blocked_days(slug: str, today: date, max_days: int = 30) -> int:
    count = 0
    for i in range(max_days):
        d = today - timedelta(days=i)
        path = daily_note_path(d)
        if not path.exists():
            break
        content = path.read_text(encoding="utf-8")
        section = extract_section(content, "Active Projects")
        # Check if this project's line contains "blocked"
        slug_found = False
        for line in section.split("\n"):
            if slug in line.lower() and "blocked" in line.lower():
                slug_found = True
                break
        if slug_found:
            count += 1
        else:
            break  # consecutive streak broken
    return count


def check_heartbeat() -> dict:
    note_path = daily_note_path(TODAY)
    if not note_path.exists():
        return {"timestamp": TODAY.isoformat(), "projects": [], "escalations": []}

    content = note_path.read_text(encoding="utf-8")
    projects_raw = parse_active_projects(content)

    projects = []
    escalations = []

    for proj in projects_raw:
        slug = proj["slug"]
        flags = detect_flags(proj["status_text"])
        staleness = compute_staleness(slug, TODAY)
        blocked_days = compute_blocked_days(slug, TODAY) if "blocked" in flags else 0

        needs_attention = blocked_days >= 3 or staleness >= 7 or "needs_coding" in flags or "needs_attention" in flags
        attention_reason = None
        if blocked_days >= 3:
            attention_reason = f"blocked for {blocked_days} consecutive days"
        elif staleness >= 7:
            attention_reason = f"not mentioned in {staleness} days"
        elif "needs_coding" in flags:
            attention_reason = "needs coding session"
        elif "needs_attention" in flags:
            attention_reason = "needs attention"

        projects.append({
            "slug": slug,
            "status_text": proj["status_text"],
            "flags": flags,
            "staleness_days": staleness,
            "blocked_days": blocked_days,
            "needs_attention": needs_attention,
            "attention_reason": attention_reason,
        })

        if blocked_days >= 3:
            escalations.append({
                "slug": slug,
                "reason": "blocked_3_days",
                "action": "pending_item_created",
                "detail": f"{slug} blocked for {blocked_days} consecutive days",
            })
            if not DRY_RUN:
                create_escalation_pending(slug, attention_reason)

    return {
        "timestamp": TODAY.isoformat(),
        "projects": projects,
        "escalations": escalations,
    }


def create_escalation_pending(slug: str, reason: str) -> None:
    note_path = daily_note_path(TODAY)
    if not note_path.exists():
        return
    content = note_path.read_text(encoding="utf-8")
    pending_item = f"- [ ] ESCALATION: [[{slug}]] — {reason}"
    if pending_item in content:
        return  # idempotent
    # Handle both "## Pending Items" (init.sh template) and "## Pending" (legacy)
    if "## Pending Items" in content:
        content = content.replace(
            "## Pending Items",
            f"## Pending Items\n{pending_item}",
            1,
        )
    else:
        content = content.replace(
            "## Pending",
            f"## Pending\n{pending_item}",
            1,
        )
    note_path.write_text(content, encoding="utf-8")


def log_heartbeat(record: dict) -> None:
    log_path = LIFE_DIR / "logs" / "heartbeat.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    existing.append(record)
    # Keep last 90 entries
    if len(existing) > 90:
        existing = existing[-90:]
    log_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def main() -> None:
    record = check_heartbeat()
    if not DRY_RUN:
        log_heartbeat(record)
    if JSON_OUTPUT:
        json.dump(record, sys.stdout, indent=2)
    else:
        for proj in record["projects"]:
            status = "ATTENTION" if proj["needs_attention"] else "OK"
            print(f"[heartbeat] {status}: [[{proj['slug']}]] — {proj['status_text']}")
            if proj["attention_reason"]:
                print(f"  → {proj['attention_reason']}")
        if record["escalations"]:
            for esc in record["escalations"]:
                print(f"[heartbeat] ESCALATION: {esc['detail']}")
        else:
            print(f"[heartbeat] {len(record['projects'])} projects checked, no escalations")


if __name__ == "__main__":
    main()
