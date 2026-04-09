"""Graceful degradation ladder.

When a dependency is broken, Maxwell should fail in a way the user
understands, without leaking internals. Five fall-back levels:

- **L0 full**                everything works; real LLM call proceeds
- **L1 no QMD**              (future) skip tool loop, static context only
- **L2 no daily note**       today's file unreadable — prompt has a stub
                             but Maxwell can still reason about the rest
                             of the vault. Recorded, not user-visible.
- **L3 no vault**            ~/life/ inaccessible — emit a minimal
                             canned reply; no point building a prompt
- **L4 no LLM**              OpenRouter breaker open or downstream dead;
                             canned reply, no network call
- **L5 kill switch**         operator pause: env var or kill file present;
                             ack with "paused" and do nothing else

Each level has a stable string identifier (``L0``..``L5``) that lands in
``events.jsonl`` so "days since last L>=L3" can be an SLI without parsing
free-form English.

``compute_degradation()`` returns the **highest** applicable level (L5 wins
over L4 wins over L3 wins over L2 wins over L0). ``degraded_reply()`` maps
a level to the user-visible fallback text, or ``None`` for L0 (real LLM
call should run).
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path


class DegradationLevel(str, Enum):
    L0 = "L0"  # full service
    L1 = "L1"  # no QMD (future)
    L2 = "L2"  # no daily note (stub in prompt)
    L3 = "L3"  # no vault
    L4 = "L4"  # no LLM (breaker open)
    L5 = "L5"  # kill switch


# Replies are intentionally plain and user-oriented. No mention of
# "circuit breaker", "OpenRouter", or internal module names.
_REPLIES = {
    DegradationLevel.L0: None,
    DegradationLevel.L1: None,  # invisible to user; tool loop just skipped
    DegradationLevel.L2: None,  # handled in-prompt; not a fallback reply
    DegradationLevel.L3: (
        "I can't reach my knowledge vault right now. Try again in a bit."
    ),
    DegradationLevel.L4: (
        "I'm temporarily unavailable — give me a moment and try again later."
    ),
    DegradationLevel.L5: (
        "Maxwell is paused. I'll be back shortly."
    ),
}


def degraded_reply(level: DegradationLevel):
    """Return the canned reply for a degradation level, or None for L0/L1/L2."""
    return _REPLIES[level]


def _life_dir() -> Path:
    return Path(os.environ.get("LIFE_DIR", str(Path.home() / "life")))


def compute_degradation(
    *,
    kill_switch_env: str = "",
    kill_switch_file: str = "",
    breaker_open: bool = False,
) -> DegradationLevel:
    """Return the highest applicable degradation level for the current state.

    Precedence (highest → lowest):

    1. ``L5`` if kill switch is active (env ``MAXWELL_WHATSAPP_ENABLED=0`` or
       a kill file path exists on disk)
    2. ``L4`` if the OpenRouter breaker is open
    3. ``L3`` if ``LIFE_DIR`` doesn't exist
    4. ``L0`` otherwise (L1/L2 are decided inside the prompt builder, not
       here — they don't change the call path)
    """
    if kill_switch_env == "0":
        return DegradationLevel.L5
    if kill_switch_file and Path(kill_switch_file).exists():
        return DegradationLevel.L5
    if breaker_open:
        return DegradationLevel.L4
    if not _life_dir().exists():
        return DegradationLevel.L3
    return DegradationLevel.L0
