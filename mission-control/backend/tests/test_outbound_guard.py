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


# ===================================================================
# V3: redact_reply tests
# ===================================================================


def test_redact_private_ipv4():
    from app.outbound_guard import redact_reply

    text = "The server is at 192.168.0.5 on port 8000"
    result = redact_reply(text)
    assert "192.168.0.5" not in result
    assert "[REDACTED-IP]" in result
    assert "port 8000" in result


def test_redact_tailscale_ip():
    from app.outbound_guard import redact_reply

    result = redact_reply("Tailscale IP is 100.85.234.128")
    assert "100.85.234.128" not in result
    assert "[REDACTED-IP]" in result


def test_redact_10x_network():
    from app.outbound_guard import redact_reply

    result = redact_reply("The gateway is at 10.0.0.1")
    assert "10.0.0.1" not in result
    assert "[REDACTED-IP]" in result


def test_redact_api_key_sk():
    from app.outbound_guard import redact_reply

    result = redact_reply("Use key sk-ant-api03-lA_XdzWGAvZFxqCLkhlQ5w")
    assert "sk-ant-api03" not in result
    assert "[REDACTED-KEY]" in result


def test_redact_api_key_gsk():
    from app.outbound_guard import redact_reply

    result = redact_reply("Groq key is gsk_wumKHsR72x8AAPYROKJhWGdyb3FY")
    assert "gsk_wumKHsR72" not in result
    assert "[REDACTED-KEY]" in result


def test_redact_gemini_key():
    from app.outbound_guard import redact_reply

    # Use a fake key that matches the AIza pattern but is clearly test data
    fake_key = "AIza" + "X" * 35  # AIzaXXXXX...
    result = redact_reply(f"Gemini key: {fake_key}")
    assert "AIzaXXX" not in result
    assert "[REDACTED-KEY]" in result


def test_redact_jwt_token():
    from app.outbound_guard import redact_reply

    # eyJ prefix + 10 chars minimum (v4 fix: lowered from 50)
    jwt = "eyJ" + "A" * 15  # eyJAAAA... (18 chars total, short JWT header)
    result = redact_reply(f"Token: {jwt}")
    assert jwt not in result
    assert "[REDACTED-TOKEN]" in result


def test_redact_mnt_external_path():
    from app.outbound_guard import redact_reply

    result = redact_reply("Config is at /mnt/external/openclaw/docker-compose.yml")
    assert "/mnt/external/" not in result
    assert "[REDACTED-PATH]" in result


def test_redact_openclaw_home_path():
    from app.outbound_guard import redact_reply

    result = redact_reply("Key file: /home/enrico/.openclaw/managed_block.key")
    assert "/home/enrico/.openclaw/" not in result
    assert "[REDACTED-PATH]" in result


def test_redact_preserves_normal_text():
    from app.outbound_guard import redact_reply

    text = "Your active projects are: openclaw-maxwell and pi-cluster."
    assert redact_reply(text) == text


def test_redact_multiple_patterns_in_one_text():
    from app.outbound_guard import redact_reply

    text = "Server 192.168.0.5 uses key sk-ant-api03-abcdefghijklmnopqrstuv at /mnt/external/config.yml"
    result = redact_reply(text)
    assert "[REDACTED-IP]" in result
    assert "[REDACTED-KEY]" in result
    assert "[REDACTED-PATH]" in result


def test_redact_does_not_hit_public_ips():
    from app.outbound_guard import redact_reply

    text = "Google DNS is at 8.8.8.8 and Cloudflare is 1.1.1.1"
    assert redact_reply(text) == text
