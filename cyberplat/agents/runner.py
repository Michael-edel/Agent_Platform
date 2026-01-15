"""Agent runner abstraction for built-in executor."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class AgentRunner(Protocol):
    """
    Minimal contract for executable agents.

    - Must return a JSON-serializable dict.
    - Must raise exceptions on errors (executor will mark execution as failed).
    """

    def run(self, payload: dict, *, tenant_id: str, execution_id: UUID) -> dict:  # noqa: D401
        ...

