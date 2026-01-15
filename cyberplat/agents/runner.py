"""Agent runner abstraction for built-in executor."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from cyberplat.agents.context import ExecutionContext


class AgentRunner(Protocol):
    """
    Minimal contract for executable agents.

    - Must return a JSON-serializable dict.
    - Must raise exceptions on errors (executor will mark execution as failed).
    """

    def run(
        self,
        payload: dict,
        *,
        tenant_id: str,
        execution_id: UUID,
        ctx: Optional[ExecutionContext] = None,
    ) -> dict:  # noqa: D401
        ...

