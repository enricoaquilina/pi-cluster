"""Phase 8.0.1 — global LLM kill switch.

Every LLM-calling script (8A `lint_knowledge_llm.py`, 8C `rewrite_summaries.py`,
and any future consumer of the `llm_client` wrapper) must check this FIRST,
before any work. If the switch is active, the script exits 0 with a log line
and does not contact the LLM.

Two activation mechanisms (either suffices):

1. Environment variable ``LIFE_LLM_DISABLED`` set to any non-empty value.
2. Sentinel file ``$LIFE_DIR/.llm-disabled`` exists (content ignored; presence
   is the signal).

Usage
-----
::

    from life_kill_switch import check_llm_kill_switch

    def main() -> int:
        reason = check_llm_kill_switch(script="lint_knowledge_llm")
        if reason:
            return 0
        ...
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SENTINEL_FILENAME = ".llm-disabled"


def _life_dir() -> Path:
    return Path(os.environ.get("LIFE_DIR", str(Path.home() / "life")))


def is_llm_disabled() -> tuple[bool, str]:
    """Return (disabled, reason).

    ``reason`` is an empty string when ``disabled`` is False.
    """
    env = os.environ.get("LIFE_LLM_DISABLED", "")
    if env:
        return True, f"env LIFE_LLM_DISABLED={env}"
    sentinel = _life_dir() / SENTINEL_FILENAME
    if sentinel.exists():
        return True, f"sentinel {sentinel}"
    return False, ""


def check_llm_kill_switch(*, script: str, stream=None) -> str:
    """Emit a log line and return the reason if the switch is active.

    Returns the reason string (truthy) when the switch is active so the caller
    can do ``if check_llm_kill_switch(script="..."): return 0``. Returns an
    empty string otherwise.
    """
    disabled, reason = is_llm_disabled()
    if not disabled:
        return ""
    out = stream or sys.stderr
    print(f"[{script}] LIFE_LLM_DISABLED active ({reason}); skipping LLM work", file=out)
    return reason


if __name__ == "__main__":
    # CLI usage: `python3 life_kill_switch.py` exits 0 if enabled, 1 if disabled.
    disabled, reason = is_llm_disabled()
    if disabled:
        print(f"LLM disabled: {reason}", file=sys.stderr)
        sys.exit(1)
    print("LLM enabled")
    sys.exit(0)
