"""Tests for AgentSKU model and admin."""

import pytest
import uuid
from datetime import datetime, timezone


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestAgentSKUModel:
    """Tests for AgentSKU model."""

    def test_create_agent_sku(self):
        """Can create AgentSKU with required fields."""
        from cyberplat.product.infrastructure.models import AgentSKU
        
        sku = AgentSKU(
            id=str(uuid.uuid4()),
            code="sales_assistant",
            name="Sales Assistant",
            description="AI-powered sales assistant",
            status="active",
            pricing_model="subscription",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        assert sku.code == "sales_assistant"
        assert sku.name == "Sales Assistant"
        assert sku.status == "active"
        assert sku.pricing_model == "subscription"

    def test_agent_sku_has_unique_code_constraint(self):
        """AgentSKU model has unique constraint on code."""
        from cyberplat.product.infrastructure.models import AgentSKU
        
        # Check table args contain unique constraint
        constraints = [c for c in AgentSKU.__table_args__ if hasattr(c, 'name') and 'uq' in str(c.name)]
        assert len(constraints) > 0
        assert any('code' in str(c) for c in constraints)

    def test_agent_sku_status_values(self):
        """AgentSKU status can be active, deprecated, or disabled."""
        from cyberplat.product.infrastructure.models import AgentSKU
        
        for status in ["active", "deprecated", "disabled"]:
            sku = AgentSKU(
                id=str(uuid.uuid4()),
                code=f"test_{status}",
                name=f"Test {status}",
                status=status,
                pricing_model="subscription",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            assert sku.status == status

    def test_agent_sku_pricing_models(self):
        """AgentSKU supports different pricing models."""
        from cyberplat.product.infrastructure.models import AgentSKU
        
        for model in ["subscription", "usage_based"]:
            sku = AgentSKU(
                id=str(uuid.uuid4()),
                code=f"test_{model}",
                name=f"Test {model}",
                status="active",
                pricing_model=model,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            assert sku.pricing_model == model


class TestAgentSKUAdmin:
    """Tests for AgentSKU admin view."""

    def test_admin_view_exists(self):
        """AgentSKUAdmin view exists."""
        from app.admin.views import AgentSKUAdmin
        
        assert AgentSKUAdmin is not None
        assert AgentSKUAdmin.name == "Agent SKU"

    def test_admin_view_no_delete(self):
        """AgentSKUAdmin does not allow delete."""
        from app.admin.views import AgentSKUAdmin
        
        assert AgentSKUAdmin.can_delete is False

    def test_admin_view_allows_crud(self):
        """AgentSKUAdmin allows create and edit."""
        from app.admin.views import AgentSKUAdmin
        
        assert AgentSKUAdmin.can_create is True
        assert AgentSKUAdmin.can_edit is True
        assert AgentSKUAdmin.can_view_details is True

    def test_admin_view_column_list(self):
        """AgentSKUAdmin has expected columns."""
        from app.admin.views import AgentSKUAdmin
        
        expected = ["code", "name", "status", "pricing_model", "created_at"]
        assert AgentSKUAdmin.column_list == expected

    def test_admin_view_searchable_fields(self):
        """AgentSKUAdmin has expected searchable fields."""
        from app.admin.views import AgentSKUAdmin
        
        assert "code" in AgentSKUAdmin.column_searchable_list
        assert "name" in AgentSKUAdmin.column_searchable_list


class TestAgentSKUMigration:
    """Tests for AgentSKU migration."""

    def test_migration_file_exists(self):
        """Migration file for agent_skus exists."""
        import os
        
        migration_path = "alembic/versions/h3i4j5k6l7m8_add_agent_skus.py"
        assert os.path.exists(migration_path)

    def test_migration_has_upgrade_and_downgrade(self):
        """Migration has both upgrade and downgrade functions."""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            "migration",
            "alembic/versions/h3i4j5k6l7m8_add_agent_skus.py"
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        
        assert hasattr(migration, 'upgrade')
        assert hasattr(migration, 'downgrade')
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)
