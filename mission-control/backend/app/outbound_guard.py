"""Outbound regex guard — last line of defense against path hallucination.

If Maxwell's reply still references a known-bad filesystem path (the exact
``~/.openclaw/workspace/memory/`` hallucination that prompted the whole fix,
or a variant), the guard replaces the reply with a safe fallback and logs a
HIGH alert. This bounds user-visible damage even if the system-prompt fix
missed a case, and gives us a measurable SLI: *zero guard hits over 7 days*.

The pattern list is intentionally small and extensible — each entry is a
compiled regex. Add a new pattern whenever a new hallucination class is seen
in the wild.
"""

from __future__ import annotations

import logging
import re
from typing import List, Pattern, Tuple

logger = logging.getLogger("mission-control.outbound_guard")

# ── Pattern set ──────────────────────────────────────────────────────────────
# Rules for adding patterns:
#  - Match the HALLUCINATION, never a legitimate path. Test with
#    tests/test_outbound_guard.py::test_does_not_hit_on_legitimate_life_paths.
#  - Use re.IGNORECASE for filesystem-component matching (some locales /
#    transcription errors produce mixed case).
#  - Prefer anchoring to distinctive substrings (".openclaw/workspace/memory")
#    rather than broad patterns that catch legitimate paths.

HALLUCINATED_PATH_PATTERNS: List[Pattern[str]] = [
    # The exact 2026-04-09 WhatsApp incident: any reference to a
    # ~/.openclaw/workspace/memory path, with optional home prefix.
    re.compile(r"(?:~|/home/[^/\s]+|\.)?/?\.openclaw/workspace/memory", re.IGNORECASE),
]

SAFE_FALLBACK = (
    "I'm not sure about that — could you rephrase? I couldn't find a "
    "trustworthy answer in my memory for this one."
)


def guard_reply(text: str) -> Tuple[bool, str]:
    """Check ``text`` against the hallucination patterns.

    Returns ``(hit, replacement_or_original)``:

    - If any pattern matches, returns ``(True, SAFE_FALLBACK)`` and logs a
      warning with the matched substring so the class can be audited.
    - Otherwise returns ``(False, text)`` unchanged.
    """
    for pattern in HALLUCINATED_PATH_PATTERNS:
        match = pattern.search(text)
        if match:
            logger.warning(
                "outbound_guard: hallucinated path detected, replacing reply: %r",
                match.group(0),
            )
            return True, SAFE_FALLBACK
    return False, text


# ── Redaction patterns (v3) ──────────────────────────────────────────────────
# Deterministic regex redaction for sensitive data in outbound responses.
# Unlike guard_reply (which replaces the entire reply), redact_reply does
# inline substitution — preserving the useful parts of the response.

_REDACT_PATTERNS: List[Tuple[Pattern[str], str]] = [
    # Private IPv4 ranges (RFC 1918)
    (re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"), "[REDACTED-IP]"),
    # Tailscale CGNAT range (100.64.0.0/10)
    (re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"), "[REDACTED-IP]"),
    # API keys: OpenAI/Anthropic style
    (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "[REDACTED-KEY]"),
    # API keys: Groq style
    (re.compile(r"gsk_[a-zA-Z0-9_-]{20,}"), "[REDACTED-KEY]"),
    # API keys: Google/Gemini style
    (re.compile(r"AIza[a-zA-Z0-9_-]{30,}"), "[REDACTED-KEY]"),
    # JWT tokens (min 10 chars: real JWT headers are ~33 chars after eyJ)
    (re.compile(r"eyJ[a-zA-Z0-9_-]{10,}"), "[REDACTED-TOKEN]"),
    # Sensitive filesystem paths
    (re.compile(r"/mnt/external/[^\s\"']+"), "[REDACTED-PATH]"),
    (re.compile(r"/home/\w+/\.openclaw/[^\s\"']+"), "[REDACTED-PATH]"),
]


def redact_reply(text: str) -> str:
    """Redact sensitive patterns from outbound responses.

    Returns the text with inline substitutions. Does not replace the
    entire reply — only the matched substrings are swapped.
    """
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
