"""Agent Execution Worker - processes accepted executions.

Lightweight built-in executor (no external queues).
Enabled via AGENT_EXECUTOR_ENABLED=true env var.
"""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update, text
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentExecution, AgentSKU
from cyberplat.agents.registry import get_runner, get_timeout_seconds
from cyberplat.agents.errors import (
    AgentExecutionError,
    AgentErrorCode,
    validation_error,
    runner_not_found,
    execution_error,
)
from cyberplat.agents.context import ExecutionContext, ExecutionCancelled
from app.agents.metrics import inc_completed, inc_failed

logger = logging.getLogger(__name__)


def _month_key_utc(now: datetime) -> tuple[int, int]:
    return now.year, now.month


def _increment_monthly_usage(engine, tenant_id: str, agent_code: str, year: int, month: int, now: str) -> None:
    """
    Increment tenant_usage_monthly.completed_executions (best-effort, idempotent via caller guard).
    """
    import uuid as _uuid
    usage_id = str(_uuid.uuid4())

    stmt = text(
        """
        INSERT INTO tenant_usage_monthly
          (id, tenant_id, agent_code, year, month, completed_executions, updated_at)
        VALUES
          (:id, :tenant_id, :agent_code, :year, :month, 1, :updated_at)
        ON CONFLICT (tenant_id, agent_code, year, month)
        DO UPDATE SET
          completed_executions = tenant_usage_monthly.completed_executions + 1,
          updated_at = excluded.updated_at
        """
    )
    # SQLite and Postgres both support this ON CONFLICT form.
    with engine.connect() as conn:
        conn.execute(
            stmt,
            {
                "id": usage_id,
                "tenant_id": tenant_id,
                "agent_code": agent_code,
                "year": year,
                "month": month,
                "updated_at": now,
            },
        )
        conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_executor_enabled() -> bool:
    """Check if executor is enabled via environment."""
    return os.getenv("AGENT_EXECUTOR_ENABLED", "false").lower() == "true"


def get_poll_interval_seconds() -> float:
    """Get poll interval from environment (default 1 second)."""
    ms = int(os.getenv("AGENT_EXECUTOR_POLL_INTERVAL_MS", "1000"))
    return max(ms / 1000.0, 0.1)  # Min 100ms


def claim_next_execution(session: Session) -> Optional[AgentExecution]:
    """
    Claim next accepted execution for processing.
    
    Uses simple SELECT + UPDATE for SQLite compatibility.
    For PostgreSQL, could use FOR UPDATE SKIP LOCKED.
    
    Returns execution or None if queue is empty.
    """
    # Find one accepted execution (oldest first)
    query = (
        select(AgentExecution)
        .where(AgentExecution.status == "accepted")
        .order_by(AgentExecution.created_at)
        .limit(1)
    )
    
    execution = session.execute(query).scalar_one_or_none()
    
    if not execution:
        return None
    
    # Claim it by setting status to running
    now = now_iso()
    session.execute(
        update(AgentExecution)
        .where(AgentExecution.id == execution.id)
        .where(AgentExecution.status == "accepted")  # Ensure still accepted
        .values(status="running", started_at=now, updated_at=now)
    )
    session.commit()
    
    # Re-fetch to get updated state
    execution = session.execute(
        select(AgentExecution).where(AgentExecution.id == execution.id)
    ).scalar_one_or_none()
    
    # If status is not running, another worker claimed it
    if execution and execution.status != "running":
        return None

    # Capture monotonic start time as close as possible to the running transition.
    if execution:
        try:
            execution._started_monotonic = time.monotonic()  # type: ignore[attr-defined]
        except Exception:
            pass

    return execution


def def run_execution(session: Session, execution: AgentExecution) -> None:
    """
    Run a single execution.
    
    - Executes runner based on agent_code (from AgentSKU)
    - Sets status to completed or failed
    - Safe no-op if execution already completed/failed/rejected
    """
    execution_id = execution.id
    if getattr(execution, "status", None) in {"completed", "failed", "rejected"}:
        return
    started_monotonic = getattr(execution, "_started_monotonic", None)
    if not isinstance(started_monotonic, (int, float)):
        started_monotonic = time.monotonic()
    
    # Cache agent_code early for error handling (avoids race condition)
    agent_code = "unknown"
    try:
        # Load agent_code from SKU
        sku = session.execute(
            select(AgentSKU).where(AgentSKU.id == execution.agent_sku_id)
        ).scalar_one_or_none()
        
        if not sku or not sku.code:
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            err = AgentExecutionError(
                code=AgentErrorCode.RUNNER_NOT_FOUND,
                message=f"agent_code not found for execution {execution_id}",
                details={"agent_sku_id": str(execution.agent_sku_id)}
            )
            err_dict = err.to_dict()
            err_dict["meta"] = {"duration_ms": duration_ms}
            now = now_iso()
            res = session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    result_json=json.dumps(err_dict),
                    error_code=err.code.value,
                    error_message=err.message[:500],
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
                inc_failed("unknown", err.code.value)
            return
        
        agent_code = sku.code
        runner = get_runner(agent_code)
        if not runner:
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            err = runner_not_found(agent_code)
            err_dict = err.to_dict()
            err_dict["meta"] = {"duration_ms": duration_ms}
            now = now_iso()
            res = session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    result_json=json.dumps(err_dict),
                    error_code=err.code.value,
                    error_message=err.message[:500],
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
                inc_failed(agent_code, err.code.value)
            return

        # Parse payload
        payload: dict = {}
        if execution.input_json:
            payload = json.loads(execution.input_json)

        # Execute runner
        try:
            exec_uuid = UUID(str(execution_id))
        except Exception:
            exec_uuid = UUID(int=0)

        timeout_seconds = get_timeout_seconds(agent_code)
        ctx = ExecutionContext(
            tenant_id=execution.tenant_id,
            execution_id=exec_uuid,
            _engine=session.get_bind(),
        )

        def _run():
            # First check in case cancellation was requested right after claim.
            ctx.check_cancelled()
            return runner.run(payload, tenant_id=execution.tenant_id, execution_id=exec_uuid, ctx=ctx)

        try:
            if timeout_seconds is None:
                result = _run()
            else:
                # Best-effort cancellation: thread keeps running after timeout.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(_run)
                    result = fut.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            err = AgentExecutionError(
                code=AgentErrorCode.TIMEOUT,
                message="execution timed out",
                details={"timeout_seconds": timeout_seconds},
            )
            err_dict = err.to_dict()
            err_dict["meta"] = {"duration_ms": duration_ms}
            now = now_iso()
            res = session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    result_json=json.dumps(err_dict),
                    error_code=err.code.value,
                    error_message=err.message[:500],
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
                inc_failed(agent_code, err.code.value)
            return

        if not isinstance(result, dict):
            raise TypeError("runner_result_invalid: runner must return dict")

        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        meta = result.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["duration_ms"] = duration_ms
        result["meta"] = meta

        # Ensure JSON-serializable (after adding meta)
        result_json = json.dumps(result)
        now = now_iso()
        res = session.execute(
            update(AgentExecution)
            .where(AgentExecution.id == execution_id)
            .where(AgentExecution.status == "running")
            .values(
                status="completed",
                result_json=result_json,
                error_code=None,
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        session.commit()
        if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
            # Usage-based billing: count only completed executions and only once.
            try:
                # Skip if already counted.
                counted = session.execute(
                    select(AgentExecution.usage_counted_at).where(AgentExecution.id == execution_id)
                ).scalar_one_or_none()
                if not counted and bool(getattr(sku, "usage_enabled", False)):
                    # Mark counted first (idempotency), then increment aggregate.
                    mark = session.execute(
                        update(AgentExecution)
                        .where(AgentExecution.id == execution_id)
                        .where(AgentExecution.usage_counted_at.is_(None))
                        .values(usage_counted_at=now, updated_at=now)
                    )
                    session.commit()
                    if getattr(mark, "rowcount", 0) and getattr(mark, "rowcount", 0) > 0:
                        dt_now = datetime.now(timezone.utc)
                        year, month = _month_key_utc(dt_now)
                        _increment_monthly_usage(session.get_bind(), execution.tenant_id, agent_code, year, month, now)
            except Exception:
                # Billing aggregation should not break execution completion.
                logger.warning("Failed to record usage-based billing aggregate", exc_info=True)
            inc_completed(agent_code)
        logger.debug(f"Execution {execution_id} completed (agent_code={agent_code})")

    except Exception as e:
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        # Normalize error taxonomy (no raw traceback stored).
        if isinstance(e, ExecutionCancelled):
            err = e.err
        elif isinstance(e, (ValueError, KeyError)):
            err: AgentExecutionError = validation_error(
                message=str(e) or "validation_error",
                details={"exception_type": type(e).__name__},
            )
        else:
            err = execution_error(
                message=f"{type(e).__name__}: {str(e)}"[:200],
                details={"exception_type": type(e).__name__},
            )
        err_dict = err.to_dict()
        err_dict["meta"] = {"duration_ms": duration_ms}

        try:
            now = now_iso()
            # agent_code already cached at function start, no need to re-query
            res = session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    result_json=json.dumps(err_dict),
                    error_code=err.code.value,
                    error_message=err.message[:500],
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            if getattr(res, "rowcount", 0) and getattr(res, "rowcount", 0) > 0:
                inc_failed(agent_code, err.code.value)
        except Exception as e2:
            logger.exception(f"Failed to mark execution {execution_id} as failed: {e2}")

        logger.exception(f"Execution {execution_id} failed: {e}")



def process_pending_executions(engine, max_per_tick: int = 10) -> int:
    """
    Process pending executions in a single tick.
    
    Returns number of executions processed.
    """
    processed = 0
    
    for _ in range(max_per_tick):
        with Session(engine) as session:
            execution = claim_next_execution(session)
            
            if not execution:
                break  # Queue empty
            
            run_execution(session, execution)
            processed += 1
    
    return processed


# ============================================
# Background Worker
# ============================================

_executor_thread: Optional[threading.Thread] = None
_executor_stop_event = threading.Event()


def _executor_loop(engine) -> None:
    """Background executor loop."""
    poll_interval = get_poll_interval_seconds()
    logger.info(f"Executor loop started (poll_interval={poll_interval}s)")
    
    while not _executor_stop_event.is_set():
        try:
            processed = process_pending_executions(engine, max_per_tick=10)
            if processed > 0:
                logger.debug(f"Processed {processed} executions")
        except Exception as e:
            logger.exception(f"Executor tick error: {e}")
        
        _executor_stop_event.wait(poll_interval)
    
    logger.info("Executor loop stopped")


def start_executor(engine) -> None:
    """Start background executor thread if enabled."""
    global _executor_thread
    
    if not is_executor_enabled():
        logger.info("Agent executor disabled (AGENT_EXECUTOR_ENABLED != true)")
        return
    
    if _executor_thread and _executor_thread.is_alive():
        logger.warning("Executor already running")
        return
    
    _executor_stop_event.clear()
    _executor_thread = threading.Thread(
        target=_executor_loop,
        args=(engine,),
        daemon=True,
        name="agent-executor",
    )
    _executor_thread.start()
    logger.info("Agent executor started")


def stop_executor() -> None:
    """Stop background executor thread."""
    global _executor_thread
    
    if not _executor_thread:
        return
    
    _executor_stop_event.set()
    _executor_thread.join(timeout=5.0)
    _executor_thread = None
    logger.info("Agent executor stopped")
