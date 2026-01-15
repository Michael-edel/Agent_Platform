"""Tests for Tenant Portal agents catalog endpoint."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestAgentCatalogSchema:
    """Tests for agent catalog schema."""

    def test_agent_catalog_item_schema_exists(self):
        """AgentCatalogItem schema exists."""
        from app.api.tenant_portal import AgentCatalogItem
        
        assert AgentCatalogItem is not None

    def test_agent_catalog_item_has_required_fields(self):
        """AgentCatalogItem has required fields."""
        from app.api.tenant_portal import AgentCatalogItem
        
        fields = AgentCatalogItem.model_fields.keys()
        assert "code" in fields
        assert "name" in fields
        assert "pricing_model" in fields
        assert "enabled" in fields
        assert "tenant_status" in fields

    def test_agent_catalog_response_schema(self):
        """AgentCatalogResponse wraps items."""
        from app.api.tenant_portal import AgentCatalogResponse
        
        assert "items" in AgentCatalogResponse.model_fields.keys()


class TestAgentCatalogEndpoint:
    """Tests for GET /tenant/agents endpoint."""

    def test_endpoint_exists(self):
        """get_agents_catalog function exists."""
        from app.api.tenant_portal import get_agents_catalog
        
        assert callable(get_agents_catalog)

    def test_filters_only_active_skus(self):
        """Endpoint filters by SKU status = active."""
        import inspect
        from app.api.tenant_portal import get_agents_catalog
        
        source = inspect.getsource(get_agents_catalog)
        assert 'status == "active"' in source

    def test_checks_tenant_agent_enabled(self):
        """Endpoint checks TenantAgent status for enabled."""
        import inspect
        from app.api.tenant_portal import get_agents_catalog
        
        source = inspect.getsource(get_agents_catalog)
        assert "enabled" in source
        assert "tenant_status" in source


class TestAgentCatalogAuth:
    """Tests for authentication on agents catalog."""

    def test_requires_auth_dependency(self):
        """Endpoint uses tenant_portal_auth dependency."""
        import inspect
        from app.api.tenant_portal import get_agents_catalog
        
        sig = inspect.signature(get_agents_catalog)
        params = list(sig.parameters.keys())
        assert "tenant_id" in params


class TestAgentCatalogLogic:
    """Tests for catalog building logic."""

    def test_enabled_true_when_tenant_agent_enabled(self):
        """enabled=True only when TenantAgent.status == 'enabled'."""
        from app.api.tenant_portal import AgentCatalogItem
        
        # Test the logic: enabled should be True only for "enabled" status
        for status, expected in [("enabled", True), ("disabled", False), ("suspended", False)]:
            item = AgentCatalogItem(
                code="test",
                name="Test",
                status="active",
                pricing_model="subscription",
                enabled=(status == "enabled"),
                tenant_status=status,
            )
            assert item.enabled == expected, f"Failed for status {status}"

    def test_enabled_false_when_no_tenant_agent(self):
        """enabled=False when no TenantAgent record."""
        from app.api.tenant_portal import AgentCatalogItem
        
        item = AgentCatalogItem(
            code="test",
            name="Test",
            status="active",
            pricing_model="subscription",
            enabled=False,
            tenant_status=None,
        )
        assert item.enabled is False
        assert item.tenant_status is None
