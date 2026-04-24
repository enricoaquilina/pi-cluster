#!/usr/bin/env python3
"""
Generate a markdown summary of recent cross-platform activity from the episodic log.

Shows what each agent harness (Claude Code, Maxwell, Hermes, etc.) did recently,
so every platform has visibility into the others' actions.

Usage:
    python3 cross_platform_summary.py                           # Last 24h, all platforms
    python3 cross_platform_summary.py --hours 48                # Last 48h
    python3 cross_platform_summary.py --exclude-platform claude-code  # Hide one platform
    python3 cross_platform_summary.py --max-lines 10            # Cap output length
    python3 cross_platform_summary.py --json                    # JSON output
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime

try:
    from episodic import recent_activity
except ImportError:
    print("[cross-platform] WARNING: episodic module not found", file=sys.stderr)
    sys.exit(0)

PLATFORM_DISPLAY = {
    "claude-code": "Claude Code",
    "maxwell": "Maxwell/OpenClaw",
    "hermes": "Hermes Agent",
    "opencode": "OpenCode",
    "cursor": "Cursor",
    "windsurf": "Windsurf",
    "cline": "Cline",
    "aider": "Aider",
    "codex": "Codex",
    "mcp-client": "MCP Client",
    "nightly-consolidate": "Nightly Consolidation",
    "heartbeat-agent": "Heartbeat Agent",
}


def format_summary(
    hours: int = 24,
    exclude_platforms: list[str] | None = None,
    max_lines: int = 20,
) -> str:
    events = recent_activity(hours=hours, limit=500)

    if exclude_platforms:
        events = [e for e in events if e.get("platform") not in exclude_platforms]

    if not events:
        return ""

    by_platform: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_platform[e.get("platform", "unknown")].append(e)

    lines = [f"## Cross-Platform Activity (last {hours}h)"]
    total_lines = 1

    for platform, entries in sorted(by_platform.items(), key=lambda x: -len(x[1])):
        if total_lines >= max_lines:
            break

        display = PLATFORM_DISPLAY.get(platform, platform)
        event_types = defaultdict(int)
        for e in entries:
            event_types[e.get("event", "unknown")] += 1
        type_summary = ", ".join(f"{c} {t.replace('_', ' ')}" for t, c in sorted(event_types.items(), key=lambda x: -x[1]))

        lines.append(f"### {display} ({len(entries)} events: {type_summary})")
        total_lines += 1

        for entry in entries[:5]:
            if total_lines >= max_lines:
                break
            ts_str = entry.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_str).astimezone()
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                time_str = "??:??"

            detail = entry.get("detail", "")[:80]
            entity = entry.get("entity", "")
            prefix = f"[{entity}] " if entity else ""
            lines.append(f"- {time_str}: {prefix}{detail}")
            total_lines += 1

        remaining = len(entries) - 5
        if remaining > 0 and total_lines < max_lines:
            lines.append(f"  _...and {remaining} more_")
            total_lines += 1

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Cross-platform activity summary")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--exclude-platform", action="append", default=[])
    parser.add_argument("--max-lines", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.json:
        events = recent_activity(hours=args.hours, limit=500)
        if args.exclude_platform:
            events = [e for e in events if e.get("platform") not in args.exclude_platform]
        print(json.dumps(events, indent=2))
    else:
        summary = format_summary(
            hours=args.hours,
            exclude_platforms=args.exclude_platform or None,
            max_lines=args.max_lines,
        )
        if summary:
            print(summary)


if __name__ == "__main__":
    main()
