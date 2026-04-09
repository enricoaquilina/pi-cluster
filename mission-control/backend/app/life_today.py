"""Canonical 'today' for the ~/life/ knowledge base.

Maxwell's system prompt, nightly consolidation, cc_start_hook.sh, and the
MCP write server must all agree on what 'today' is. Disagreement around
midnight or DST transitions produces silent off-by-one-day bugs (the
system says "no daily note" when there is one, or writes to the wrong
dated file). Every component that needs 'today' imports from here.

Timezone is pinned to Europe/Rome per the user profile.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")


def _now() -> datetime:
    """Indirection seam for tests to freeze the clock."""
    return datetime.now(timezone.utc)


def life_today() -> date:
    """Return today's date in the canonical vault timezone (Europe/Rome)."""
    return _now().astimezone(TZ).date()


def life_today_filename() -> str:
    """Return the filename for today's daily note, e.g. '2026-04-09.md'."""
    return f"{life_today().isoformat()}.md"


def life_today_daily_path() -> str:
    """Return the vault-relative path for today's daily note.

    Format: Daily/YYYY/MM/YYYY-MM-DD.md
    """
    d = life_today()
    return f"Daily/{d.year:04d}/{d.month:02d}/{d.isoformat()}.md"
