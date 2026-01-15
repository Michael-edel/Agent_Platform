"""demo.sentiment_basic agent implementation (no external services)."""

from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from cyberplat.agents.runner import AgentRunner
from cyberplat.agents.context import ExecutionContext


_NON_WORD_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)

# Very small lexicon (RU/EN) for demo purposes.
_POS_WORDS = {
    # EN
    "good",
    "great",
    "excellent",
    "love",
    "awesome",
    "happy",
    "nice",
    "amazing",
    # RU
    "хорошо",
    "отлично",
    "прекрасно",
    "люблю",
    "классно",
    "счастлив",
    "супер",
    "нравится",
}

_NEG_WORDS = {
    # EN
    "bad",
    "terrible",
    "awful",
    "hate",
    "sad",
    "worse",
    "worst",
    "angry",
    # RU
    "плохо",
    "ужасно",
    "ненавижу",
    "грустно",
    "хуже",
    "самый_плохой",
    "злой",
    "раздражает",
}


class SentimentBasicRunner(AgentRunner):
    def run(self, payload: dict, *, tenant_id: str, execution_id: UUID, ctx: Optional[ExecutionContext] = None) -> dict:
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("validation_error: payload.text is required and must be a string")

        normalized = _NON_WORD_RE.sub(" ", text.lower())
        tokens = [t for t in re.split(r"\s+", normalized.strip()) if t]
        if not tokens:
            # Empty content: neutral by default.
            return {"label": "neutral", "score": 0.0, "meta": {"tokens": 0}}

        pos = sum(1 for t in tokens if t in _POS_WORDS)
        neg = sum(1 for t in tokens if t in _NEG_WORDS)
        score = (pos - neg) / max(len(tokens), 1)
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        # Clamp score to [-1, 1] just in case.
        if score > 1.0:
            score = 1.0
        if score < -1.0:
            score = -1.0

        return {
            "label": label,
            "score": float(score),
            "meta": {"tokens": len(tokens), "pos": pos, "neg": neg},
        }

