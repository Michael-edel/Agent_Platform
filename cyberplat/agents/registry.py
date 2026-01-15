"""Registry for built-in AgentRunner implementations."""

from __future__ import annotations

import os
from typing import Optional

from cyberplat.agents.runner import AgentRunner
from cyberplat.agents.impl.text_stats import TextStatsRunner
from cyberplat.agents.impl.sentiment_basic import SentimentBasicRunner


AGENT_RUNNERS: dict[str, AgentRunner] = {
    "demo.text_stats": TextStatsRunner(),
    "demo.sentiment_basic": SentimentBasicRunner(),
}

AGENT_TIMEOUT_SECONDS: dict[str, int] = {
    "demo.text_stats": 2,
    "demo.sentiment_basic": 2,
}


def get_runner(agent_code: str) -> Optional[AgentRunner]:
    return AGENT_RUNNERS.get(agent_code)


def get_timeout_seconds(agent_code: str) -> Optional[float]:
    if agent_code in AGENT_TIMEOUT_SECONDS:
        return float(AGENT_TIMEOUT_SECONDS[agent_code])

    raw = os.getenv("AGENT_DEFAULT_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except Exception:
        return None

