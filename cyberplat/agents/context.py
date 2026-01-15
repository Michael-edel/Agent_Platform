"""ExecutionContext for cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentExecution
from cyberplat.agents.errors import AgentExecutionError, cancelled


class ExecutionCancelled(Exception):
    """Raised by cooperative runners when cancellation is requested."""

    def __init__(self, err: AgentExecutionError):
        self.err = err
        super().__init__(err.message)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecutionContext:
    tenant_id: str
    execution_id: UUID
    _engine: object

    def check_cancelled(self) -> None:
        """
        Best-effort cancellation check.

        Reads AgentExecution.cancel_requested from DB and raises ExecutionCancelled
        when a cancellation has been requested.
        """
        with Session(self._engine) as session:
            flag = session.execute(
                select(AgentExecution.cancel_requested).where(
                    AgentExecution.id == str(self.execution_id),
                    AgentExecution.tenant_id == self.tenant_id,
                )
            ).scalar_one_or_none()
        if flag:
            raise ExecutionCancelled(cancelled(details={"cancel_requested_at": now_iso()}))

