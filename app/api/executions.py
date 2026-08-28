"""Execution management endpoints (cancel)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import AgentExecution, AgentSKU
from app.agents.metrics import inc_failed
from app.security.auth import require_roles


router = APIRouter(prefix="/api/v1/executions", tags=["executions"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CancelResponse(BaseModel):
    execution_id: str
    status: str
    error_code: Optional[str] = None
    cancel_requested: bool = False


@router.post("/{execution_id}/cancel", response_model=CancelResponse)
def cancel_execution(
    execution_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Best-effort cancellation.

    - Only `accepted` can be cancelled (transition to rejected + error_code=cancelled).
    - `running` cannot be cancelled (409).
    - Terminal states are idempotent (200).
    """
    tenant_id = x_tenant_id
    engine = get_engine()

    with Session(engine) as session:
        execution = session.execute(
            select(AgentExecution).where(
                AgentExecution.id == execution_id,
                AgentExecution.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")

        if execution.status == "running":
            now = now_iso()
            session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.tenant_id == tenant_id)
                .where(AgentExecution.status == "running")
                .values(cancel_requested=True, cancel_requested_at=now, updated_at=now)
            )
            session.commit()
            return CancelResponse(
                execution_id=execution.id,
                status="running",
                error_code=execution.error_code,
                cancel_requested=True,
            )

        if execution.status != "accepted":
            # Terminal or already rejected: idempotent.
            return CancelResponse(
                execution_id=execution.id,
                status=execution.status,
                error_code=execution.error_code,
                cancel_requested=bool(getattr(execution, "cancel_requested", False)),
            )

        sku = session.execute(select(AgentSKU).where(AgentSKU.id == execution.agent_sku_id)).scalar_one_or_none()
        agent_code = sku.code if sku else "unknown"

        now = now_iso()
        res = session.execute(
            update(AgentExecution)
            .where(AgentExecution.id == execution_id)
            .where(AgentExecution.tenant_id == tenant_id)
            .where(AgentExecution.status == "accepted")
            .values(
                status="rejected",
                error_code="cancelled",
                error_message="cancelled",
                finished_at=now,
                updated_at=now,
            )
        )
        session.commit()
        if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
            inc_failed(agent_code, "cancelled")

        return CancelResponse(execution_id=execution_id, status="rejected", error_code="cancelled")

