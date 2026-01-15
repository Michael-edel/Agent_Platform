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

from sqlalchemy import select, update, text
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentExecution

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
    Run a single execution (stub implementation).
    
    - Sets status to running if not already
    - Executes stub logic (echo input)
    - Sets status to completed or failed
    """
    execution_id = execution.id
    now = now_iso()
    
    try:
        # Parse input
        input_data = {}
        if execution.input_json:
            try:
                input_data = json.loads(execution.input_json)
            except json.JSONDecodeError:
                input_data = {}
        
        # Stub execution: echo input
        result = {"ok": True, "echo": input_data}
        
        # Mark completed
        session.execute(
            update(AgentExecution)
            .where(AgentExecution.id == execution_id)
            .values(
                status="completed",
                result_json=json.dumps(result),
                finished_at=now_iso(),
                updated_at=now_iso(),
            )
        )
        session.commit()
        
        logger.debug(f"Execution {execution_id} completed")
        
    except Exception as e:
        # Mark failed
        error_msg = str(e)[:500]  # Truncate long messages
        
        try:
            session.execute(
                update(AgentExecution)
                .where(AgentExecution.id == execution_id)
                .values(
                    status="failed",
                    error_code="execution_failed",
                    error_message=error_msg,
                    finished_at=now_iso(),
                    updated_at=now_iso(),
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
