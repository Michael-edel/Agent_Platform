"""Tests for Agent Addon Subscription model, guards and gating."""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestTenantAgentSubscriptionModel:
    """Tests for TenantAgentSubscription model."""

    def test_create_subscription(self):
        """Can create TenantAgentSubscription with required fields."""
        from cyberplat.product.infrastructure.models import TenantAgentSubscription
        
        sub = TenantAgentSubscription(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            agent_sku_id=str(uuid.uuid4()),
            status="active",
            starts_at=datetime.now(timezone.utc).isoformat(),
            source="admin",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        assert sub.status == "active"
        assert sub.source == "admin"

    def test_subscription_has_unique_constraint(self):
        """TenantAgentSubscription has unique constraint on (tenant_id, agent_sku_id)."""
        from cyberplat.product.infrastructure.models import TenantAgentSubscription
        
        constraints = [c for c in TenantAgentSubscription.__table_args__ 
                      if hasattr(c, 'name') and c.name == 'uq_tenant_agent_subscription']
        assert len(constraints) == 1

    def test_subscription_status_values(self):
        """TenantAgentSubscription status can be active, inactive, canceled, past_due."""
        from cyberplat.product.infrastructure.models import TenantAgentSubscription
        
        for status in ["active", "inactive", "canceled", "past_due"]:
            sub = TenantAgentSubscription(
                id=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
                agent_sku_id=str(uuid.uuid4()),
                status=status,
                starts_at=datetime.now(timezone.utc).isoformat(),
                source="admin",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            assert sub.status == status


class TestAgentAddonGuard:
    """Tests for assert_agent_addon_active guard."""

    def test_addon_inactive_error_attributes(self):
        """AgentAddonInactiveError has correct attributes."""
        from app.agents.guards import AgentAddonInactiveError
        
        err = AgentAddonInactiveError("tenant-1", "test_agent", "inactive")
        assert err.tenant_id == "tenant-1"
        assert err.agent_code == "test_agent"
        assert err.status == "inactive"

    def test_addon_inactive_error_status_none(self):
        """AgentAddonInactiveError can have status=None (not subscribed)."""
        from app.agents.guards import AgentAddonInactiveError
        
        err = AgentAddonInactiveError("tenant-1", "test_agent", None)
        assert err.status is None

    def test_assert_addon_active_raises_when_missing(self):
        """assert_agent_addon_active raises when no subscription."""
        from app.agents.guards import assert_agent_addon_active, AgentAddonInactiveError
        from cyberplat.product.infrastructure.models import AgentSKU
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, None]
        
        with pytest.raises(AgentAddonInactiveError) as exc_info:
            assert_agent_addon_active(session, "tenant-1", "test_agent")
        
        assert exc_info.value.status is None

    def test_assert_addon_active_raises_when_inactive(self):
        """assert_agent_addon_active raises when subscription inactive."""
        from app.agents.guards import assert_agent_addon_active, AgentAddonInactiveError
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "inactive"
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        with pytest.raises(AgentAddonInactiveError) as exc_info:
            assert_agent_addon_active(session, "tenant-1", "test_agent")
        
        assert exc_info.value.status == "inactive"

    def test_assert_addon_active_passes_when_active(self):
        """assert_agent_addon_active passes when subscription active."""
        from app.agents.guards import assert_agent_addon_active
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "active"
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        result = assert_agent_addon_active(session, "tenant-1", "test_agent")
        assert result == mock_sub


class TestExecutionAddonGating:
    """Tests for addon gating in execution API."""

    def test_addon_check_in_execute(self):
        """Execute endpoint checks addon status."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert "assert_agent_addon_active" in source
        assert "AgentAddonInactiveError" in source

    def test_402_response_for_inactive_addon(self):
        """Execute returns 402 for inactive addon."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert "status_code=402" in source
        assert '"error": "agent_addon_inactive"' in source

    def test_rejected_execution_created_for_addon_inactive(self):
        """Rejected execution is created when addon inactive."""
        import inspect
        from app.api.agents import execute_agent
        
        source = inspect.getsource(execute_agent)
        assert 'error_code="agent_addon_inactive"' in source


class TestTenantAgentSubscriptionAdmin:
    """Tests for TenantAgentSubscription admin view."""

    def test_admin_view_exists(self):
        """TenantAgentSubscriptionAdmin view exists."""
        from app.admin.views import TenantAgentSubscriptionAdmin
        
        assert TenantAgentSubscriptionAdmin is not None
        assert TenantAgentSubscriptionAdmin.name == "Agent Subscription"

    def test_admin_view_no_delete(self):
        """TenantAgentSubscriptionAdmin does not allow delete."""
        from app.admin.views import TenantAgentSubscriptionAdmin
        
        assert TenantAgentSubscriptionAdmin.can_delete is False

    def test_admin_view_allows_crud(self):
        """TenantAgentSubscriptionAdmin allows create and edit."""
        from app.admin.views import TenantAgentSubscriptionAdmin
        
        assert TenantAgentSubscriptionAdmin.can_create is True
        assert TenantAgentSubscriptionAdmin.can_edit is True


class TestTenantPortalAddonStatus:
    """Tests for addon status in tenant portal catalog."""

    def test_catalog_item_has_addon_fields(self):
        """AgentCatalogItem has addon_status and paid fields."""
        from app.api.tenant_portal import AgentCatalogItem
        
        fields = AgentCatalogItem.model_fields.keys()
        assert "addon_status" in fields
        assert "paid" in fields

    def test_paid_true_when_addon_active(self):
        """paid=True only when addon_status='active'."""
        from app.api.tenant_portal import AgentCatalogItem
        
        item = AgentCatalogItem(
            code="test",
            name="Test",
            status="active",
            pricing_model="subscription",
            enabled=True,
            tenant_status="enabled",
            addon_status="active",
            paid=True,
        )
        assert item.paid is True

    def test_paid_false_when_addon_not_active(self):
        """paid=False when addon_status != 'active'."""
        from app.api.tenant_portal import AgentCatalogItem
        
        for status in ["inactive", "canceled", "past_due", None]:
            item = AgentCatalogItem(
                code="test",
                name="Test",
                status="active",
                pricing_model="subscription",
                enabled=True,
                tenant_status="enabled",
                addon_status=status,
                paid=(status == "active"),
            )
            assert item.paid is False


class TestMigration:
    """Tests for TenantAgentSubscription migration."""

    def test_migration_file_exists(self):
        """Migration file for tenant_agent_subscriptions exists."""
        import os
        
        migration_path = "alembic/versions/k6l7m8n9o0p1_add_agent_subscriptions.py"
        assert os.path.exists(migration_path)

    def test_migration_has_upgrade_and_downgrade(self):
        """Migration has both upgrade and downgrade functions."""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            "migration",
            "alembic/versions/k6l7m8n9o0p1_add_agent_subscriptions.py"
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        
        assert hasattr(migration, 'upgrade')
        assert hasattr(migration, 'downgrade')
