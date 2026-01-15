"""Agent Execution Worker - processes accepted executions.

Lightweight built-in executor (no external queues).
Enabled via AGENT_EXECUTOR_ENABLED=true env var.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update, text
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentExecution, AgentSKU
from cyberplat.agents.registry import get_runner

logger = logging.getLogger(__name__)


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
    
    return execution


def run_execution(session: Session, execution: AgentExecution) -> None:
    """
    Run a single execution.
    
    - Executes runner based on agent_code (from AgentSKU)
    - Sets status to completed or failed
    - Safe no-op if execution already completed/failed/rejected
    """
    execution_id = execution.id
    if getattr(execution, "status", None) in {"completed", "failed", "rejected"}:
        return
    
    try:
        # Load agent_code from SKU
        sku = session.execute(
            select(AgentSKU).where(AgentSKU.id == execution.agent_sku_id)
        ).scalar_one_or_none()
        agent_code = sku.code if sku else None
        if not agent_code:
            raise RuntimeError("agent_code not found for execution")

        runner = get_runner(agent_code)
        if not runner:
            now = now_iso()
            session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    error_code="runner_not_found",
                    error_message=f"runner_not_found: {agent_code}",
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
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

        result = runner.run(payload, tenant_id=execution.tenant_id, execution_id=exec_uuid)
        if not isinstance(result, dict):
            raise TypeError("runner_result_invalid: runner must return dict")

        # Ensure JSON-serializable
        result_json = json.dumps(result)

        now = now_iso()
        session.execute(
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

        logger.debug(f"Execution {execution_id} completed (agent_code={agent_code})")
        
    except Exception as e:
        # Mark failed
        error_code = "validation_error" if isinstance(e, ValueError) else "execution_failed"
        error_msg = f"{type(e).__name__}: {e}"[:500]  # Truncate long messages
        
        try:
            now = now_iso()
            session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .where(AgentExecution.status == "running")
                .values(
                    status="failed",
                    error_code=error_code,
                    error_message=error_msg,
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.commit()
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
