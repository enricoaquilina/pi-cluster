#!/usr/bin/env python3
"""
Ingest Maxwell dispatch results into ~/life/ for nightly consolidation.
Reads from MC API dispatch log + agent-runs.json, writes maxwell-YYYY-MM-DD.md.

Usage:
    python3 ingest_dispatches.py [--dry-run]

Environment:
    LIFE_DIR            — ~/life/ directory (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (YYYY-MM-DD)
    MC_API_URL          — MC API base URL (default: http://localhost:3000/api)
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv
MC_API = os.environ.get("MC_API_URL", "http://localhost:3000/api")

TARGET_DATE = date.fromisoformat(TODAY)


def _fetch_dispatches() -> list[dict]:
    """Fetch today's dispatches from MC API."""
    url = f"{MC_API}/dispatch/log?limit=200"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, HTTPError, OSError) as e:
        print(f"[ingest] WARNING: MC API error: {e}", file=sys.stderr)
        return None  # sentinel: API unreachable
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ingest] WARNING: Invalid JSON from MC API: {e}", file=sys.stderr)
        return None

    items = data.get("items", [])
    total = data.get("total", 0)
    limit = data.get("limit", 200)
    if total > limit:
        print(f"[ingest] WARNING: Dispatch log has {total} entries, only fetched {limit}", file=sys.stderr)

    # Filter to today's date (convert UTC created_at to local date)
    today_items = []
    for item in items:
        created_at = item.get("created_at")
        if created_at is None:
            continue
        try:
            dt = datetime.fromisoformat(created_at)
            local_date = dt.astimezone().date()
        except (ValueError, TypeError):
            continue
        if local_date == TARGET_DATE:
            today_items.append(item)

    return today_items


def _fetch_heartbeat_actions() -> list[dict]:
    """Read today's heartbeat actions from agent-runs.json."""
    runs_path = LIFE_DIR / "logs" / "agent-runs.json"
    if not runs_path.exists():
        return []
    try:
        data = json.loads(runs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[ingest] WARNING: agent-runs.json error: {e}", file=sys.stderr)
        return []

    if not isinstance(data, list):
        return []

    today_actions = []
    for entry in data:
        ts = entry.get("timestamp", "")
        try:
            entry_date = datetime.fromisoformat(ts).date()
        except (ValueError, TypeError):
            continue
        if entry_date == TARGET_DATE:
            for action in entry.get("actions", []):
                today_actions.append({
                    "ts": ts,
                    "title": action.get("title", "unknown"),
                    "action": action.get("action", "unknown"),
                    "persona": action.get("persona", "unknown"),
                })

    return today_actions


def _format_dispatch(item: dict) -> str:
    """Format a single dispatch item as a markdown bullet."""
    created_at = item.get("created_at", "")
    try:
        dt = datetime.fromisoformat(created_at).astimezone()
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        time_str = "??:??"

    persona = item.get("persona") or "unknown"
    delegate = item.get("delegate") or "unknown"
    elapsed = item.get("elapsed_ms", 0)
    status = item.get("status", "unknown")

    # Extract task title from prompt_preview (first line, truncated)
    preview = item.get("prompt_preview", "")
    # First meaningful line (skip "Task:" prefix if present)
    title = "untitled"
    for line in preview.split("\n"):
        line = line.strip()
        if line.startswith("Task:"):
            title = line[5:].strip()[:80]
            break
        if line and not line.startswith("#"):
            title = line[:80]
            break

    elapsed_str = f"{elapsed / 1000:.0f}s" if elapsed else "?"
    return f"- {time_str} — **{persona}** ({delegate}): {title} ({elapsed_str}, {status})"


def _format_heartbeat(action: dict) -> str:
    """Format a heartbeat action as a markdown bullet."""
    ts = action.get("ts", "")
    try:
        dt = datetime.fromisoformat(ts)
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        time_str = "??:??"

    title = action.get("title", "unknown")
    act = action.get("action", "unknown")
    persona = action.get("persona", "unknown")
    return f"- {time_str} — {title}: {act} → {persona}"


def main():
    dispatches = _fetch_dispatches()
    heartbeat = _fetch_heartbeat_actions()

    lines = [
        "---",
        f"date: {TODAY}",
        "source: maxwell-ingest",
        "---",
        "",
        "## Maxwell Dispatch Activity",
    ]

    if dispatches is None:
        lines.append("_MC API unreachable — dispatch data unavailable._")
    elif not dispatches:
        lines.append("_No dispatch activity today._")
    else:
        for item in dispatches:
            lines.append(_format_dispatch(item))

    if heartbeat:
        lines.append("")
        lines.append("## Heartbeat Actions")
        for action in heartbeat:
            lines.append(_format_heartbeat(action))

    content = "\n".join(lines) + "\n"

    if DRY_RUN:
        print(content)
        return

    # Write output
    parts = TODAY.split("-")
    out_dir = LIFE_DIR / "Daily" / parts[0] / parts[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"maxwell-{TODAY}.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"[ingest] Wrote {out_path} ({len(dispatches) if dispatches else 0} dispatches, {len(heartbeat)} heartbeat actions)")


if __name__ == "__main__":
    main()
