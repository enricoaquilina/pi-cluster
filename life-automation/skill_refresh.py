#!/usr/bin/env python3
"""
Self-updating skills — refresh skill content based on recent experience.

During nightly consolidation, skills with auto_refresh: true and sufficient
usage get refreshed using recent episodic entries matching their triggers.

Usage:
    python3 skill_refresh.py                    # Refresh qualifying skills
    python3 skill_refresh.py --dry-run          # Show what would refresh
    python3 skill_refresh.py --list             # Show refresh candidates
    python3 skill_refresh.py --force skill.md   # Force refresh one skill
"""
import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
SKILLS_DIR = LIFE_DIR / "Resources" / "skills"
BACKUP_DIR = LIFE_DIR / "logs" / "skill-backups"
MIN_USE_COUNT = 3
RECENT_HOURS = 168  # 7 days


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end < 0:
        return {}, text

    fm = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            elif val.lower() in ("true", "yes"):
                val = True
            elif val.lower() in ("false", "no"):
                val = False
            elif val.isdigit():
                val = int(val)
            fm[key] = val

    body = text[end + 3:].strip()
    return fm, body


def _write_frontmatter(path: Path, fm: dict, body: str) -> None:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_recent_activity(triggers: list[str], hours: int = RECENT_HOURS) -> list[dict]:
    try:
        from episodic import recent_activity
        events = recent_activity(hours=hours, limit=200)
    except ImportError:
        return []

    matched = []
    for e in events:
        detail = e.get("detail", "").lower()
        entity = e.get("entity", "").lower()
        text = f"{detail} {entity}"
        for trigger in triggers:
            if trigger.lower() in text:
                matched.append(e)
                break
    return matched


def find_refresh_candidates() -> list[dict]:
    if not SKILLS_DIR.exists():
        return []

    candidates = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        fm, body = _parse_frontmatter(path)
        if fm.get("type") != "skill":
            continue
        if not fm.get("auto_refresh", False):
            continue

        use_count = fm.get("use_count", 0)
        if use_count < MIN_USE_COUNT:
            continue

        triggers = fm.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [triggers]
        if not triggers:
            triggers = [path.stem.replace("-", " ")]

        recent = get_recent_activity(triggers)
        if not recent:
            continue

        candidates.append({
            "path": path,
            "name": fm.get("name", path.stem),
            "frontmatter": fm,
            "body": body,
            "triggers": triggers,
            "use_count": use_count,
            "recent_events": recent,
        })

    return candidates


def build_refresh_context(candidate: dict) -> str:
    events = candidate["recent_events"][:10]
    lines = [f"Recent activity matching '{candidate['name']}':"]
    for e in events:
        ts = e.get("ts", "")[:10]
        platform = e.get("platform", "?")
        detail = e.get("detail", "")[:120]
        entity = e.get("entity", "")
        lines.append(f"- {ts} [{platform}] [{entity}] {detail}")
    return "\n".join(lines)


def refresh_skill(candidate: dict, dry_run: bool = False) -> bool:
    path = candidate["path"]
    fm = candidate["frontmatter"]

    if not dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{path.stem}-{date.today().isoformat()}.md"
        shutil.copy2(path, backup)

    context = build_refresh_context(candidate)

    fm["last_refreshed"] = date.today().isoformat()
    fm["refresh_context"] = f"{len(candidate['recent_events'])} events from last 7d"

    if dry_run:
        print(f"  Would refresh: {candidate['name']}")
        print(f"  Triggers: {candidate['triggers']}")
        print(f"  Use count: {candidate['use_count']}")
        print(f"  Recent events: {len(candidate['recent_events'])}")
        print(f"  Context preview:\n{context[:300]}")
        return True

    _write_frontmatter(path, fm, candidate["body"])

    print(f"  Refreshed: {candidate['name']} (backed up to {backup.name})")

    try:
        from episodic import log_event
        log_event("nightly-consolidate", "skill_created",
                  entity=path.stem,
                  detail=f"Auto-refreshed skill: {candidate['name']}",
                  importance=3)
    except Exception:
        pass

    return True


def cmd_list():
    candidates = find_refresh_candidates()
    if not candidates:
        print("No skills qualify for auto-refresh.")
        print("Requirements: auto_refresh: true, use_count >= 3, recent matching activity")
        return

    print(f"Skills qualifying for auto-refresh ({len(candidates)}):")
    for c in candidates:
        print(f"  {c['name']} — {c['use_count']} uses, {len(c['recent_events'])} recent events")


def main():
    parser = argparse.ArgumentParser(description="Auto-refresh skills based on recent activity")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--force", help="Force refresh a specific skill file")
    args = parser.parse_args()

    if args.list:
        cmd_list()
        return

    if args.force:
        path = SKILLS_DIR / args.force if not args.force.startswith("/") else Path(args.force)
        if not path.exists():
            print(f"Skill not found: {path}", file=sys.stderr)
            sys.exit(1)
        fm, body = _parse_frontmatter(path)
        triggers = fm.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [triggers]
        if not triggers:
            triggers = [path.stem.replace("-", " ")]
        candidate = {
            "path": path,
            "name": fm.get("name", path.stem),
            "frontmatter": fm,
            "body": body,
            "triggers": triggers,
            "use_count": fm.get("use_count", 0),
            "recent_events": get_recent_activity(triggers),
        }
        refresh_skill(candidate, dry_run=args.dry_run)
        return

    candidates = find_refresh_candidates()
    if not candidates:
        print("[skill-refresh] No skills qualify for refresh.")
        return

    count = 0
    for c in candidates:
        if refresh_skill(c, dry_run=args.dry_run):
            count += 1

    print(f"[skill-refresh] {'Would refresh' if args.dry_run else 'Refreshed'} {count} skill(s).")


if __name__ == "__main__":
    main()
