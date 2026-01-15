"""Tests for TenantAgent model, admin and guards."""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestTenantAgentModel:
    """Tests for TenantAgent model."""

    def test_create_tenant_agent(self):
        """Can create TenantAgent with required fields."""
        from cyberplat.product.infrastructure.models import TenantAgent
        
        ta = TenantAgent(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            agent_sku_id=str(uuid.uuid4()),
            status="enabled",
            activated_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        assert ta.status == "enabled"
        assert ta.tenant_id is not None
        assert ta.agent_sku_id is not None

    def test_tenant_agent_has_unique_constraint(self):
        """TenantAgent has unique constraint on (tenant_id, agent_sku_id)."""
        from cyberplat.product.infrastructure.models import TenantAgent
        
        constraints = [c for c in TenantAgent.__table_args__ 
                      if hasattr(c, 'name') and c.name == 'uq_tenant_agent']
        assert len(constraints) == 1

    def test_tenant_agent_status_values(self):
        """TenantAgent status can be enabled, disabled, or suspended."""
        from cyberplat.product.infrastructure.models import TenantAgent
        
        for status in ["enabled", "disabled", "suspended"]:
            ta = TenantAgent(
                id=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
                agent_sku_id=str(uuid.uuid4()),
                status=status,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            assert ta.status == status


class TestTenantAgentAdmin:
    """Tests for TenantAgent admin view."""

    def test_admin_view_exists(self):
        """TenantAgentAdmin view exists."""
        from app.admin.views import TenantAgentAdmin
        
        assert TenantAgentAdmin is not None
        assert TenantAgentAdmin.name == "Tenant Agent"

    def test_admin_view_no_delete(self):
        """TenantAgentAdmin does not allow delete."""
        from app.admin.views import TenantAgentAdmin
        
        assert TenantAgentAdmin.can_delete is False

    def test_admin_view_allows_crud(self):
        """TenantAgentAdmin allows create and edit."""
        from app.admin.views import TenantAgentAdmin
        
        assert TenantAgentAdmin.can_create is True
        assert TenantAgentAdmin.can_edit is True
        assert TenantAgentAdmin.can_view_details is True


class TestAgentGuards:
    """Tests for agent access guards."""

    def test_agent_not_found_error(self):
        """AgentNotFoundError has correct attributes."""
        from app.agents.guards import AgentNotFoundError
        
        err = AgentNotFoundError("test_agent")
        assert err.agent_code == "test_agent"
        assert "test_agent" in str(err)

    def test_agent_not_enabled_error(self):
        """AgentNotEnabledError has correct attributes."""
        from app.agents.guards import AgentNotEnabledError
        
        err = AgentNotEnabledError("tenant-123", "test_agent")
        assert err.tenant_id == "tenant-123"
        assert err.agent_code == "test_agent"
        assert "tenant-123" in str(err)
        assert "test_agent" in str(err)

    def test_assert_agent_enabled_raises_not_found(self):
        """assert_agent_enabled raises AgentNotFoundError when SKU missing."""
        from app.agents.guards import assert_agent_enabled, AgentNotFoundError
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        
        with pytest.raises(AgentNotFoundError) as exc_info:
            assert_agent_enabled(session, "tenant-1", "nonexistent")
        
        assert exc_info.value.agent_code == "nonexistent"

    def test_assert_agent_enabled_raises_not_enabled(self):
        """assert_agent_enabled raises AgentNotEnabledError when not enabled."""
        from app.agents.guards import assert_agent_enabled, AgentNotEnabledError
        from cyberplat.product.infrastructure.models import AgentSKU
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        mock_sku.code = "test_agent"
        
        session = MagicMock()
        # First call returns SKU, second call returns None (no TenantAgent)
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, None]
        
        with pytest.raises(AgentNotEnabledError) as exc_info:
            assert_agent_enabled(session, "tenant-1", "test_agent")
        
        assert exc_info.value.tenant_id == "tenant-1"
        assert exc_info.value.agent_code == "test_agent"

    def test_assert_agent_enabled_passes_when_enabled(self):
        """assert_agent_enabled returns TenantAgent when enabled."""
        from app.agents.guards import assert_agent_enabled
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgent
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        mock_sku.code = "test_agent"
        
        mock_ta = MagicMock(spec=TenantAgent)
        mock_ta.status = "enabled"
        mock_ta.tenant_id = "tenant-1"
        mock_ta.agent_sku_id = "sku-1"
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_ta]
        
        result = assert_agent_enabled(session, "tenant-1", "test_agent")
        
        assert result == mock_ta


class TestTenantAgentMigration:
    """Tests for TenantAgent migration."""

    def test_migration_file_exists(self):
        """Migration file for tenant_agents exists."""
        import os
        
        migration_path = "alembic/versions/i4j5k6l7m8n9_add_tenant_agents.py"
        assert os.path.exists(migration_path)

    def test_migration_has_upgrade_and_downgrade(self):
        """Migration has both upgrade and downgrade functions."""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            "migration",
            "alembic/versions/i4j5k6l7m8n9_add_tenant_agents.py"
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        
        assert hasattr(migration, 'upgrade')
        assert hasattr(migration, 'downgrade')
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)
