"""Phase 8B — FTS5 query escape (unit + property tests)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

from session_search import _escape_fts5_query, ensure_db  # noqa: E402


# ============================================================= unit cases


@pytest.mark.parametrize("raw,expected", [
    ("pi-cluster", '"pi-cluster"'),
    ("gym-tracker-app", '"gym-tracker-app"'),
    ("plain", "plain"),
    ("", ""),
    # Reserved bareword operators must be quoted
    ("AND", '"AND"'),
    ("or", '"or"'),
    ("NOT", '"NOT"'),
    ("NEAR", '"NEAR"'),
    # Mixed query: non-hyphenated + hyphenated
    ("foo pi-cluster bar", 'foo "pi-cluster" bar'),
    # Punctuation in token
    ("pi-cluster.", '"pi-cluster."'),
    # Already-quoted stays the same (idempotent)
    ('"pi-cluster"', '"pi-cluster"'),
    # Double quote inside token gets doubled
    ('foo"bar', '"foo""bar"'),
    # Parenthesis
    ("foo(bar)", '"foo(bar)"'),
    # Star
    ("foo*", '"foo*"'),
])
def test_escape_unit(raw, expected):
    assert _escape_fts5_query(raw) == expected


def test_escape_truncates_to_512():
    long = "x" * 1000
    out = _escape_fts5_query(long)
    # The input has no whitespace → single bareword → truncated length
    assert len(out) <= 520  # 512 + possible quoting overhead


def test_escape_idempotent_simple():
    for s in ["pi-cluster", "plain", "foo bar", "AND"]:
        once = _escape_fts5_query(s)
        twice = _escape_fts5_query(once)
        assert once == twice


# ============================================================= sqlite round-trip


@pytest.fixture
def fts5_db(tmp_path):
    db = tmp_path / "s.db"
    conn = ensure_db(db)
    conn.execute(
        "INSERT INTO sessions(session_id, ts, summary, session_type, decisions, files_touched) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "2026-04-01T00:00:00Z", "worked on pi-cluster deployment", "ops", "", ""),
    )
    conn.execute(
        "INSERT INTO sessions_meta(session_id, ts, session_type) VALUES (?, ?, ?)",
        ("s1", "2026-04-01T00:00:00Z", "ops"),
    )
    conn.commit()
    return conn


def test_fts5_hyphen_round_trip(fts5_db):
    escaped = _escape_fts5_query("pi-cluster")
    rows = fts5_db.execute(
        "SELECT session_id FROM sessions WHERE sessions MATCH ?",
        (escaped,),
    ).fetchall()
    assert len(rows) == 1


def test_fts5_unquoted_hyphen_would_fail(fts5_db):
    """Sanity: without escaping, hyphen is the MINUS operator and causes an error."""
    with pytest.raises(sqlite3.OperationalError):
        fts5_db.execute(
            "SELECT session_id FROM sessions WHERE sessions MATCH ?",
            ("pi-cluster",),
        ).fetchall()


def test_fts5_reserved_word_round_trip(fts5_db):
    # Should not raise — AND as a phrase, not an operator
    _escape_fts5_query("AND")  # ensure no crash in escape
    escaped = _escape_fts5_query("AND")
    # Querying 'AND' as a phrase is a valid FTS5 query even if no rows match
    fts5_db.execute(
        "SELECT session_id FROM sessions WHERE sessions MATCH ?",
        (escaped,),
    ).fetchall()


# ============================================================= property tests


ALPHA = st.text(alphabet="abcdefghijklmnop0123456789-_.", min_size=1, max_size=20)


@given(st.text(max_size=100))
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_escape_never_raises(s):
    """Escape must never raise on arbitrary text input."""
    _escape_fts5_query(s)


@given(st.lists(ALPHA, min_size=1, max_size=5))
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_escape_idempotent_property(tokens):
    q = " ".join(tokens)
    once = _escape_fts5_query(q)
    twice = _escape_fts5_query(once)
    assert once == twice


@given(st.lists(ALPHA, min_size=1, max_size=3))
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_escape_survives_fts5_match(fts5_db, tokens):
    """Every escaped output parses as a valid FTS5 MATCH query."""
    q = " ".join(tokens)
    escaped = _escape_fts5_query(q)
    if not escaped:
        return  # empty query is a separate path
    try:
        fts5_db.execute(
            "SELECT 1 FROM sessions WHERE sessions MATCH ? LIMIT 1",
            (escaped,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        # Acceptable runtime errors (not syntax errors):
        if "no such column" in msg or "fts5: syntax error" in msg:
            raise
