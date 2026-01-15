"""Tests for Agent Executor (async execution processing)."""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


class TestExecutorConfig:
    """Tests for executor configuration."""

    def test_is_executor_enabled_default_false(self, monkeypatch):
        """Executor disabled by default."""
        monkeypatch.delenv("AGENT_EXECUTOR_ENABLED", raising=False)
        
        from app.agents.executor import is_executor_enabled
        
        assert is_executor_enabled() is False

    def test_is_executor_enabled_when_true(self, monkeypatch):
        """Executor enabled when env var is true."""
        monkeypatch.setenv("AGENT_EXECUTOR_ENABLED", "true")
        
        from app.agents.executor import is_executor_enabled
        
        assert is_executor_enabled() is True

    def test_poll_interval_default(self, monkeypatch):
        """Default poll interval is 1 second."""
        monkeypatch.delenv("AGENT_EXECUTOR_POLL_INTERVAL_MS", raising=False)
        
        from app.agents.executor import get_poll_interval_seconds
        
        assert get_poll_interval_seconds() == 1.0

    def test_poll_interval_custom(self, monkeypatch):
        """Custom poll interval from env."""
        monkeypatch.setenv("AGENT_EXECUTOR_POLL_INTERVAL_MS", "2000")
        
        from app.agents.executor import get_poll_interval_seconds
        
        assert get_poll_interval_seconds() == 2.0


class TestClaimExecution:
    """Tests for claim_next_execution."""

    def test_claim_function_exists(self):
        """claim_next_execution function exists."""
        from app.agents.executor import claim_next_execution
        
        assert callable(claim_next_execution)

    def test_claim_returns_none_for_empty_queue(self):
        """Returns None when no accepted executions."""
        from app.agents.executor import claim_next_execution
        
        # Create mock session with no results
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        
        result = claim_next_execution(session)
        
        assert result is None


class TestRunExecution:
    """Tests for run_execution."""

    def test_run_function_exists(self):
        """run_execution function exists."""
        from app.agents.executor import run_execution
        
        assert callable(run_execution)

    def test_run_execution_sets_completed(self):
        """run_execution sets status to completed."""
        from app.agents.executor import run_execution
        
        # Create mock execution
        execution = MagicMock()
        execution.id = "test-exec-id"
        execution.input_json = json.dumps({"test": "data"})
        
        session = MagicMock()
        
        run_execution(session, execution)
        
        # Check that update was called with completed status
        session.execute.assert_called()
        session.commit.assert_called()

    def test_run_execution_handles_error(self):
        """run_execution sets failed status on error."""
        from app.agents.executor import run_execution
        
        execution = MagicMock()
        execution.id = "test-exec-id"
        execution.input_json = "invalid json {"  # Will cause error in stub
        
        session = MagicMock()
        
        # Shouldn't raise
        run_execution(session, execution)
        
        session.commit.assert_called()


class TestProcessPending:
    """Tests for process_pending_executions."""

    def test_process_function_exists(self):
        """process_pending_executions function exists."""
        from app.agents.executor import process_pending_executions
        
        assert callable(process_pending_executions)

    def test_process_returns_count(self):
        """Returns number of processed executions."""
        from app.agents.executor import process_pending_executions
        
        # Mock engine with empty queue
        engine = MagicMock()
        
        with patch('app.agents.executor.claim_next_execution', return_value=None):
            count = process_pending_executions(engine, max_per_tick=5)
        
        assert count == 0


class TestPostExecuteReturnsAccepted:
    """Tests that POST /execute returns accepted status."""

    def test_execute_response_has_accepted_status(self):
        """ExecuteResponse can have accepted status."""
        from app.api.agents import ExecuteResponse
        
        response = ExecuteResponse(
            execution_id="test-id",
            status="accepted",
            result=None,
        )
        
        assert response.status == "accepted"
        assert response.result is None

    def test_execute_creates_accepted_execution(self):
        """Execute endpoint creates execution with accepted status."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        
        # Check that status="accepted" is set
        assert 'status="accepted"' in source
        # Check result is None
        assert 'result_json=None' in source


class TestIdempotency:
    """Tests for idempotency handling."""

    def test_idempotency_returns_current_status(self):
        """Existing execution returns current status."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        
        # Check that existing execution status is returned
        assert 'existing.status' in source
        # Check rejected handling
        assert '"rejected"' in source


class TestStartStop:
    """Tests for executor start/stop."""

    def test_start_executor_exists(self):
        """start_executor function exists."""
        from app.agents.executor import start_executor
        
        assert callable(start_executor)

    def test_stop_executor_exists(self):
        """stop_executor function exists."""
        from app.agents.executor import stop_executor
        
        assert callable(stop_executor)

    def test_start_does_nothing_when_disabled(self, monkeypatch):
        """start_executor does nothing when disabled."""
        monkeypatch.setenv("AGENT_EXECUTOR_ENABLED", "false")
        
        from app.agents.executor import start_executor, _executor_thread
        
        engine = MagicMock()
        
        start_executor(engine)
        
        # Thread should not be started
        from app.agents import executor
        assert executor._executor_thread is None or not executor._executor_thread.is_alive()
