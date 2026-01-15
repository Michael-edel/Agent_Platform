"""Registry for built-in AgentRunner implementations."""

from __future__ import annotations

from typing import Optional

from cyberplat.agents.runner import AgentRunner
from cyberplat.agents.impl.text_stats import TextStatsRunner


AGENT_RUNNERS: dict[str, AgentRunner] = {
    "demo.text_stats": TextStatsRunner(),
}


def get_runner(agent_code: str) -> Optional[AgentRunner]:
    return AGENT_RUNNERS.get(agent_code)

