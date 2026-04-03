#!/usr/bin/env python3
"""
Archive today's Claude Code session metadata.
Writes ~/life/Daily/YYYY/MM/sessions-YYYY-MM-DD.json
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR_OVERRIDE", Path.home() / ".claude" / "sessions"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv

def archive_sessions() -> dict:
    """Collect today's session metadata into an archive."""
    if not SESSIONS_DIR.is_dir():
        return {"date": TODAY, "sessions": [], "count": 0, "total_minutes": 0}

    today_date = date.fromisoformat(TODAY)
    sessions = []

    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            started_ms = data.get("startedAt", 0)
            started_dt = datetime.fromtimestamp(started_ms / 1000, tz=timezone.utc)
            if started_dt.date() == today_date:
                # Use file mtime as approximate end time
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                duration_min = round((mtime - started_dt).total_seconds() / 60)
                sessions.append({
                    "session_id": data.get("sessionId", ""),
                    "cwd": data.get("cwd", ""),
                    "started": started_dt.isoformat(),
                    "duration_minutes": max(duration_min, 0),
                })
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue

    return {
        "date": TODAY,
        "count": len(sessions),
        "sessions": sessions,
        "total_minutes": sum(s["duration_minutes"] for s in sessions),
    }

def write_archive() -> str | None:
    """Write session archive to daily directory."""
    archive = archive_sessions()
    if archive["count"] == 0:
        print(f"[sessions] No sessions found for {TODAY}")
        return None

    parts = TODAY.split("-")
    out_dir = LIFE_DIR / "Daily" / parts[0] / parts[1]
    out_path = out_dir / f"sessions-{TODAY}.json"

    if not DRY_RUN:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(archive, indent=2), encoding="utf-8")

    print(f"[sessions] {'(dry) ' if DRY_RUN else ''}{archive['count']} sessions archived ({archive['total_minutes']} min total)")
    return str(out_path)

if __name__ == "__main__":
    write_archive()
