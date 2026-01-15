"""demo.text_stats agent implementation (no external services)."""

from __future__ import annotations

import re
from collections import Counter
from uuid import UUID

from cyberplat.agents.runner import AgentRunner


_NON_WORD_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_RU_RE = re.compile(r"[а-яё]", flags=re.IGNORECASE)
_EN_RE = re.compile(r"[a-z]", flags=re.IGNORECASE)


class TextStatsRunner(AgentRunner):
    def run(self, payload: dict, *, tenant_id: str, execution_id: UUID) -> dict:
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("validation_error: payload.text is required and must be a string")

        chars = len(text)
        lines = 0 if text == "" else (text.count("\n") + 1)

        normalized = _NON_WORD_RE.sub(" ", text.lower())
        tokens = [t for t in re.split(r"\s+", normalized.strip()) if t]
        words = len(tokens)

        counts = Counter(tokens)
        top_words = [{"w": w, "c": c} for (w, c) in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:10]]

        has_ru = any(_RU_RE.search(t) for t in tokens)
        has_en = any(_EN_RE.search(t) for t in tokens)
        if has_ru and has_en:
            language_guess = "mixed"
        elif has_ru:
            language_guess = "ru"
        elif has_en:
            language_guess = "en"
        else:
            language_guess = "unknown"

        return {
            "chars": chars,
            "words": words,
            "lines": lines,
            "top_words": top_words,
            "language_guess": language_guess,
        }

