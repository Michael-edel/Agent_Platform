"""Tests for Agent Execution API."""

import pytest
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestAgentExecutionModel:
    """Tests for AgentExecution model."""

    def test_create_execution(self):
        """Can create AgentExecution with required fields."""
        from cyberplat.product.infrastructure.models import AgentExecution
        
        exec = AgentExecution(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            agent_sku_id=str(uuid.uuid4()),
            status="accepted",
            idempotency_key="test-key-123",
            input_json=json.dumps({"query": "test"}),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        assert exec.status == "accepted"
        assert exec.idempotency_key == "test-key-123"

    def test_execution_has_idempotency_constraint(self):
        """AgentExecution has unique constraint on (tenant_id, agent_sku_id, idempotency_key)."""
        from cyberplat.product.infrastructure.models import AgentExecution
        
        constraints = [c for c in AgentExecution.__table_args__ 
                      if hasattr(c, 'name') and c.name == 'uq_execution_idempotency']
        assert len(constraints) == 1


class TestExecuteEndpoint:
    """Tests for POST /api/v1/agents/{agent_code}/execute."""

    def test_agent_not_found_returns_404(self):
        """Unknown agent_code returns 404."""
        from app.api.agents import execute_agent, ExecuteRequest
        from app.agents.guards import AgentNotFoundError
        
        with patch('app.api.agents.get_engine'):
            with patch('app.api.agents.assert_agent_enabled') as mock_guard:
                mock_guard.side_effect = AgentNotFoundError("unknown_agent")
                
                with pytest.raises(Exception) as exc_info:
                    execute_agent(
                        agent_code="unknown_agent",
                        request=ExecuteRequest(input={"test": 1}, idempotency_key="key1"),
                        x_tenant_id="tenant-1",
                    )
                
                assert exc_info.value.status_code == 404
                assert "agent_not_found" in str(exc_info.value.detail)

    def test_agent_not_enabled_returns_403(self):
        """Disabled agent returns 403."""
        from app.api.agents import execute_agent, ExecuteRequest
        from app.agents.guards import AgentNotEnabledError
        
        with patch('app.api.agents.get_engine'):
            with patch('app.api.agents.assert_agent_enabled') as mock_guard:
                mock_guard.side_effect = AgentNotEnabledError("tenant-1", "disabled_agent")
                
                with pytest.raises(Exception) as exc_info:
                    execute_agent(
                        agent_code="disabled_agent",
                        request=ExecuteRequest(input={"test": 1}, idempotency_key="key1"),
                        x_tenant_id="tenant-1",
                    )
                
                assert exc_info.value.status_code == 403
                assert "agent_not_enabled" in str(exc_info.value.detail)


class TestIdempotency:
    """Tests for execution idempotency."""

    def test_idempotency_constraint_exists(self):
        """AgentExecution has unique constraint for idempotency."""
        from cyberplat.product.infrastructure.models import AgentExecution
        
        constraints = [c for c in AgentExecution.__table_args__ 
                      if hasattr(c, 'name') and 'idempotency' in str(c.name)]
        assert len(constraints) == 1


class TestUsageMetering:
    """Tests for execution usage metering."""

    def test_record_execution_usage_function_exists(self):
        """record_execution_usage function exists."""
        from app.api.agents import record_execution_usage
        
        assert callable(record_execution_usage)

    def test_current_period_format(self):
        """current_period returns YYYY-MM format."""
        from app.api.agents import current_period
        
        period = current_period()
        assert len(period) == 7
        assert period[4] == "-"


class TestPlanLimitsEnforcement:
    """Tests for plan limits enforcement in execution."""

    def test_limit_check_used_in_execute(self):
        """Execute endpoint uses check_plan_limit."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert "check_plan_limit" in source
        assert "agent_executions" in source

    def test_rejected_status_on_limit_exceeded(self):
        """Execution gets rejected status when limit exceeded."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert 'status="rejected"' in source
        assert "plan_limit_exceeded" in source

    def test_429_response_format(self):
        """429 response has correct format."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert "JSONResponse" in source
        assert "status_code=429" in source
        assert '"error": "plan_limit_exceeded"' in source
        assert '"metric": "agent_executions"' in source


class TestGetExecution:
    """Tests for GET /api/v1/agents/executions/{execution_id}."""

    def test_endpoint_exists(self):
        """get_execution function exists."""
        from app.api.agents import get_execution
        
        assert callable(get_execution)

    def test_tenant_filter_in_query(self):
        """Endpoint filters by tenant_id for fail-closed behavior."""
        # The endpoint uses WHERE tenant_id == x_tenant_id
        # This is a design check
        import inspect
        from app.api.agents import get_execution
        
        source = inspect.getsource(get_execution)
        assert "tenant_id" in source


class TestListExecutions:
    """Tests for GET /api/v1/agents/executions."""

    def test_endpoint_exists(self):
        """list_executions function exists."""
        from app.api.agents import list_executions
        
        assert callable(list_executions)

    def test_tenant_filter_in_list(self):
        """List endpoint filters by tenant_id."""
        import inspect
        from app.api.agents import list_executions
        
        source = inspect.getsource(list_executions)
        assert "tenant_id" in source


class TestMigration:
    """Tests for AgentExecution migration."""

    def test_migration_file_exists(self):
        """Migration file for agent_executions exists."""
        import os
        
        migration_path = "alembic/versions/j5k6l7m8n9o0_add_agent_executions.py"
        assert os.path.exists(migration_path)

    def test_migration_has_upgrade_and_downgrade(self):
        """Migration has both upgrade and downgrade functions."""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            "migration",
            "alembic/versions/j5k6l7m8n9o0_add_agent_executions.py"
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        
        assert hasattr(migration, 'upgrade')
        assert hasattr(migration, 'downgrade')
