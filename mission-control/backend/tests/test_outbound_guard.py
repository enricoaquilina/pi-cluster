"""Outbound regex guard — last line of defense against path hallucination.

If Maxwell's reply still references a known-bad filesystem path (e.g. the
~/.openclaw/workspace/memory/ hallucination that prompted this whole fix),
the guard replaces the reply with a safe fallback and flags a HIGH alert.

This bounds user-visible damage regardless of whether the system-prompt fix
caught every case and gives us a measurable SLI ("zero guard hits over 7d").
"""


def test_passthrough_when_no_bad_patterns():
    from app.outbound_guard import guard_reply

    hit, text = guard_reply("Here's your daily summary. Everything is fine.")
    assert hit is False
    assert text == "Here's your daily summary. Everything is fine."


def test_hits_on_hallucinated_openclaw_memory_path():
    """The exact hallucination from the 2026-04-09 WhatsApp incident."""
    from app.outbound_guard import guard_reply

    bad = (
        "I cannot directly access a memory file for today, 2026-04-09.md, "
        "as it doesn't exist in the expected path ~/.openclaw/workspace/memory/."
    )
    hit, text = guard_reply(bad)
    assert hit is True
    assert "~/.openclaw/workspace/memory" not in text
    # User-facing safe fallback.
    assert "rephrase" in text.lower() or "not sure" in text.lower()


def test_hits_on_dot_openclaw_memory_variants():
    from app.outbound_guard import guard_reply

    variants = [
        "/home/enrico/.openclaw/workspace/memory/",
        ".openclaw/workspace/memory/",
        "~/.openclaw/workspace/memory/MEMORY.md",
    ]
    for v in variants:
        hit, _ = guard_reply(f"Looking in {v} for your note")
        assert hit is True, f"Variant not caught: {v}"


def test_does_not_hit_on_legitimate_life_paths():
    from app.outbound_guard import guard_reply

    legit = [
        "Today's note lives at ~/life/Daily/2026/04/2026-04-09.md",
        "See ~/life/Areas/about-me/hard-rules.md",
        "I read ~/life/Projects/pi-cluster/summary.md",
    ]
    for msg in legit:
        hit, text = guard_reply(msg)
        assert hit is False, f"False positive: {msg}"
        assert text == msg


def test_returns_tuple_of_bool_and_string():
    from app.outbound_guard import guard_reply

    result = guard_reply("hello")
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_case_insensitive_on_path_components():
    from app.outbound_guard import guard_reply

    hit, _ = guard_reply("Check ~/.OpenClaw/Workspace/Memory/")
    assert hit is True


def test_patterns_list_is_exported():
    """The pattern set is discoverable and extensible."""
    from app.outbound_guard import HALLUCINATED_PATH_PATTERNS

    assert isinstance(HALLUCINATED_PATH_PATTERNS, list)
    assert len(HALLUCINATED_PATH_PATTERNS) >= 1
