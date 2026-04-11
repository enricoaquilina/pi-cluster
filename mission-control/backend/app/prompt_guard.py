"""Lightweight prompt injection classifier using Meta's Prompt Guard.

Runs Meta's Prompt-Guard-86M model (86M params, CPU-only) to classify
incoming prompts as BENIGN or INJECTION before they reach the LLM.

Configuration:
    PROMPT_GUARD_ENABLED: "1" (default) to enable, "0" to disable
    PROMPT_GUARD_THRESHOLD: confidence threshold (default 0.8)

The model is lazy-loaded on first use (~2s cold start, then ~50ms/check).
Fails open on any error — never blocks legitimate requests.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger("mission-control.prompt_guard")

PROMPT_GUARD_ENABLED = os.getenv("PROMPT_GUARD_ENABLED", "1") == "1"
PROMPT_GUARD_THRESHOLD = float(os.getenv("PROMPT_GUARD_THRESHOLD", "0.8"))


@lru_cache(maxsize=1)
def _load_model():
    """Lazy-load the Prompt Guard model on first use."""
    from transformers import pipeline  # type: ignore[import-untyped]

    return pipeline(
        "text-classification",
        model="meta-llama/Prompt-Guard-86M",
        device="cpu",
    )


def check_injection(text: str) -> tuple[bool, float]:
    """Check if text contains a prompt injection attempt.

    Returns ``(is_injection, confidence)``.

    - Enabled by default (``PROMPT_GUARD_ENABLED=1``).
    - Fails open: returns ``(False, 0.0)`` on any model error.
    - Truncates input to 512 chars (model's effective window).
    """
    if not PROMPT_GUARD_ENABLED:
        return False, 0.0
    try:
        classifier = _load_model()
        result = classifier(text[:512])
        label = result[0]["label"]
        score = result[0]["score"]
        is_injection = label == "INJECTION" and score >= PROMPT_GUARD_THRESHOLD
        if is_injection:
            logger.warning(
                "prompt_guard: injection detected (score=%.3f): %s",
                score,
                text[:100],
            )
        return is_injection, score
    except Exception as e:
        logger.error("prompt_guard: model error: %s", e)
        return False, 0.0
