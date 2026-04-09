"""Canonical 'today' function: single source of truth for date-of-day.

Maxwell's system prompt, nightly consolidation, cc_start_hook, and MCP write
server must all agree on what 'today' is. Disagreement around midnight or
DST transitions has produced silent off-by-one-day bugs. life_today() pins
the timezone to Europe/Rome and exposes a single canonical function.
"""

from datetime import datetime, timezone, date
from unittest.mock import patch
from zoneinfo import ZoneInfo


def test_life_today_returns_date_object():
    """life_today() returns a datetime.date."""
    from app.life_today import life_today

    result = life_today()
    assert isinstance(result, date)


def test_life_today_uses_europe_rome_timezone():
    """At 23:50 UTC on 2026-04-09, Rome is 01:50 on 2026-04-10 (CEST)."""
    from app.life_today import life_today

    fake_now = datetime(2026, 4, 9, 23, 50, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 4, 10)


def test_life_today_winter_utc_plus_one():
    """At 23:30 UTC on 2026-01-15, Rome is 00:30 on 2026-01-16 (CET, UTC+1)."""
    from app.life_today import life_today

    fake_now = datetime(2026, 1, 15, 23, 30, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 1, 16)


def test_life_today_early_morning_rome_still_same_day():
    """At 01:00 Rome time (23:00 UTC previous day, CET winter), today is the Rome day."""
    from app.life_today import life_today

    # 2026-01-15 01:00 Rome = 2026-01-15 00:00 UTC (winter CET = UTC+1)
    fake_now = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 1, 15)


def test_life_today_dst_spring_forward_pre():
    """Before DST switch (2026-03-29 00:55 Rome = 2026-03-28 23:55 UTC), date is 2026-03-29."""
    from app.life_today import life_today

    # 2026-03-29 00:55 Europe/Rome (still CET, UTC+1) = 2026-03-28 23:55 UTC
    fake_now = datetime(2026, 3, 28, 23, 55, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 3, 29)


def test_life_today_dst_spring_forward_post():
    """After DST switch (03:05 CEST on 2026-03-29 = 01:05 UTC), date is still 2026-03-29."""
    from app.life_today import life_today

    # 2026-03-29 03:05 CEST (UTC+2) = 2026-03-29 01:05 UTC
    fake_now = datetime(2026, 3, 29, 1, 5, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 3, 29)


def test_life_today_dst_fall_back():
    """On DST fall-back (2026-10-25 02:30 CEST → 02:30 CET), date remains 2026-10-25."""
    from app.life_today import life_today

    # 2026-10-25 02:30 CEST = 2026-10-25 00:30 UTC
    fake_now = datetime(2026, 10, 25, 0, 30, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today() == date(2026, 10, 25)


def test_life_today_filename_format():
    """life_today_filename() returns 'YYYY-MM-DD.md' format for daily notes."""
    from app.life_today import life_today_filename

    fake_now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today_filename() == "2026-04-09.md"


def test_life_today_daily_note_relative_path():
    """life_today_daily_path() returns Daily/YYYY/MM/YYYY-MM-DD.md relative path."""
    from app.life_today import life_today_daily_path

    fake_now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.life_today._now", return_value=fake_now):
        assert life_today_daily_path() == "Daily/2026/04/2026-04-09.md"


def test_life_today_zoneinfo_is_europe_rome():
    """The module exposes TZ = ZoneInfo('Europe/Rome') as a public constant."""
    from app.life_today import TZ

    assert isinstance(TZ, ZoneInfo)
    assert str(TZ) == "Europe/Rome"
