"""Agent Execution API v1 endpoints."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import AgentSKU, AgentExecution
from cyberplat.billing.infrastructure.models_sqlalchemy import BillingUsage
from app.agents.guards import (
    assert_agent_enabled,
    assert_agent_addon_active,
    AgentNotFoundError,
    AgentNotEnabledError,
    AgentAddonInactiveError,
)
from app.billing.limits import check_plan_limit, PlanLimitExceededError


router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# --- Schemas ---

class ExecuteRequest(BaseModel):
    input: dict[str, Any]
    idempotency_key: str


class ExecuteResponse(BaseModel):
    execution_id: str
    status: str
    result: Optional[dict[str, Any]] = None


class ExecutionDetailResponse(BaseModel):
    execution_id: str
    agent_code: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, str]] = None


class ExecutionListItem(BaseModel):
    execution_id: str
    agent_code: str
    status: str
    created_at: str


# --- Helpers ---

def get_tenant_id(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> str:
    """Extract tenant ID from header."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return x_tenant_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_period() -> str:
    """Get current billing period in YYYY-MM format."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def record_execution_usage(
    engine,
    tenant_id: str,
    execution_id: str,
    agent_code: str,
    period: str,
) -> None:
    """Record usage for agent execution."""
    from sqlalchemy.orm import Session
    
    with Session(engine) as session:
        usage = BillingUsage(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            event_id=f"exec:{execution_id}",
            artifact_id=None,
            event_type="agent_execution",
            metric="agent_executions",
            units=1,
            unit_price_minor=0,  # Pricing logic elsewhere
            amount_minor=0,
            currency="USD",
            period=period,
            created_at=now_iso(),
        )
        session.add(usage)
        session.commit()


# --- Endpoints ---

@router.post("/{agent_code}/execute")
def execute_agent(
    agent_code: str,
    request: ExecuteRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
):
    """
    Execute an agent for the tenant.
    
    - Checks agent is enabled for tenant (guards)
    - Checks plan limits (agent_executions metric)
    - Idempotent: same (tenant_id, agent_sku_id, idempotency_key) returns existing execution
    - Rejected executions return 429 and are idempotent
    """
    tenant_id = x_tenant_id
    engine = get_engine()
    period = current_period()
    
    with Session(engine) as session:
        # Guard: check agent enabled
        try:
            tenant_agent = assert_agent_enabled(session, tenant_id, agent_code)
        except AgentNotFoundError as e:
            raise HTTPException(
                status_code=404,
                detail={"error": "agent_not_found", "agent_code": e.agent_code},
            )
        except AgentNotEnabledError as e:
            raise HTTPException(
                status_code=403,
                detail={"error": "agent_not_enabled", "agent_code": e.agent_code, "tenant_id": e.tenant_id},
            )
        
        # Get SKU for agent_sku_id
        sku = session.execute(
            select(AgentSKU).where(AgentSKU.code == agent_code)
        ).scalar_one()
        
        # Check idempotency: existing execution?
        existing = session.execute(
            select(AgentExecution).where(
                AgentExecution.tenant_id == tenant_id,
                AgentExecution.agent_sku_id == sku.id,
                AgentExecution.idempotency_key == request.idempotency_key,
            )
        ).scalar_one_or_none()
        
        if existing:
            # Return existing execution (idempotent)
            if existing.status == "rejected":
                # Check error code to return appropriate status
                if existing.error_code == "agent_addon_inactive":
                    return JSONResponse(
                        status_code=402,
                        content={
                            "error": "agent_addon_inactive",
                            "agent_code": agent_code,
                            "tenant_id": tenant_id,
                            "status": None,
                        },
                    )
                # Default to 429 for limit exceeded
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "plan_limit_exceeded",
                        "metric": "agent_executions",
                        "limit": 0,
                        "used": 0,
                        "tenant_id": tenant_id,
                    },
                )
            result = json.loads(existing.result_json) if existing.result_json else None
            return ExecuteResponse(
                execution_id=existing.id,
                status=existing.status,
                result=result,
            )
        
        # Check add-on subscription is active
        try:
            assert_agent_addon_active(session, tenant_id, agent_code)
        except AgentAddonInactiveError as e:
            # Create rejected execution for idempotency
            now = now_iso()
            execution_id = str(uuid.uuid4())
            execution = AgentExecution(
                id=execution_id,
                tenant_id=tenant_id,
                agent_sku_id=sku.id,
                status="rejected",
                idempotency_key=request.idempotency_key,
                input_json=json.dumps(request.input),
                result_json=None,
                error_code="agent_addon_inactive",
                error_message=f"Agent add-on not active: {e.status or 'not subscribed'}",
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=now,
            )
            session.add(execution)
            session.commit()
            
            return JSONResponse(
                status_code=402,
                content={
                    "error": "agent_addon_inactive",
                    "agent_code": e.agent_code,
                    "tenant_id": e.tenant_id,
                    "status": e.status,
                },
            )
        
        # Check plan limits before creating execution
        limit_check = check_plan_limit(
            tenant_id=tenant_id,
            metric="agent_executions",
            increment=1,
            period=period,
            engine=engine,
        )
        
        now = now_iso()
        execution_id = str(uuid.uuid4())
        
        if not limit_check.allowed and limit_check.reason == "limit_exceeded":
            # Create rejected execution for idempotency
            execution = AgentExecution(
                id=execution_id,
                tenant_id=tenant_id,
                agent_sku_id=sku.id,
                status="rejected",
                idempotency_key=request.idempotency_key,
                input_json=json.dumps(request.input),
                result_json=None,
                error_code="plan_limit_exceeded",
                error_message=f"Limit exceeded for agent_executions: {limit_check.used}/{limit_check.limit}",
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=now,
            )
            session.add(execution)
            session.commit()
            
            return JSONResponse(
                status_code=429,
                content={
                    "error": "plan_limit_exceeded",
                    "metric": "agent_executions",
                    "limit": limit_check.limit or 0,
                    "used": limit_check.used,
                    "tenant_id": tenant_id,
                },
            )
        
        # Create execution with status="accepted" (async processing)
        execution = AgentExecution(
            id=execution_id,
            tenant_id=tenant_id,
            agent_sku_id=sku.id,
            status="accepted",
            idempotency_key=request.idempotency_key,
            input_json=json.dumps(request.input),
            result_json=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
        )
        
        session.add(execution)
        session.commit()
    
    # Record usage at accept time (before execution)
    try:
        record_execution_usage(engine, tenant_id, execution_id, agent_code, period)
    except Exception:
        pass  # Usage recording failure should not fail the execution
    
    return ExecuteResponse(
        execution_id=execution_id,
        status="accepted",
        result=None,
    )


@router.get("/executions/{execution_id}", response_model=ExecutionDetailResponse)
def get_execution(
    execution_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
):
    """
    Get execution details.
    
    - Fail-closed: only returns execution belonging to tenant
    """
    tenant_id = x_tenant_id
    
    engine = get_engine()
    with Session(engine) as session:
        execution = session.execute(
            select(AgentExecution).where(
                AgentExecution.id == execution_id,
                AgentExecution.tenant_id == tenant_id,  # Fail-closed
            )
        ).scalar_one_or_none()
        
        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")
        
        # Get agent code
        sku = session.execute(
            select(AgentSKU).where(AgentSKU.id == execution.agent_sku_id)
        ).scalar_one_or_none()
        
        agent_code = sku.code if sku else "unknown"
        
        result = json.loads(execution.result_json) if execution.result_json else None
        error = None
        if execution.error_code or execution.error_message:
            error = {
                "code": execution.error_code or "",
                "message": execution.error_message or "",
            }
        
        return ExecutionDetailResponse(
            execution_id=execution.id,
            agent_code=agent_code,
            status=execution.status,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            result=result,
            error=error,
        )


@router.get("/executions", response_model=list[ExecutionListItem])
def list_executions(
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    agent_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """
    List executions for tenant.
    
    - Optionally filter by agent_code
    - Sorted by created_at desc
    """
    tenant_id = x_tenant_id
    
    engine = get_engine()
    with Session(engine) as session:
        query = select(AgentExecution).where(
            AgentExecution.tenant_id == tenant_id
        )
        
        # Filter by agent_code if provided
        if agent_code:
            sku = session.execute(
                select(AgentSKU).where(AgentSKU.code == agent_code)
            ).scalar_one_or_none()
            
            if sku:
                query = query.where(AgentExecution.agent_sku_id == sku.id)
            else:
                # No such agent, return empty
                return []
        
        query = query.order_by(desc(AgentExecution.created_at)).limit(limit)
        
        executions = session.execute(query).scalars().all()
        
        # Get all SKU codes for display
        sku_ids = {e.agent_sku_id for e in executions}
        skus = {}
        if sku_ids:
            for sku in session.execute(select(AgentSKU).where(AgentSKU.id.in_(sku_ids))).scalars():
                skus[sku.id] = sku.code
        
        return [
            ExecutionListItem(
                execution_id=e.id,
                agent_code=skus.get(e.agent_sku_id, "unknown"),
                status=e.status,
                created_at=e.created_at,
            )
            for e in executions
        ]
