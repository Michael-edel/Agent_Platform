"""Tests for agent addon subscription event handling."""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestAgentAddonSubscriptionUpdatedEvent:
    """Tests for AgentAddonSubscriptionUpdated event."""

    def test_event_exists(self):
        """Event class exists."""
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        
        assert AgentAddonSubscriptionUpdated is not None

    def test_event_has_required_fields(self):
        """Event has required fields."""
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="sales_assistant",
            status="active",
            source="admin",
        )
        
        assert event.tenant_id == "tenant-1"
        assert event.agent_code == "sales_assistant"
        assert event.status == "active"
        assert event.source == "admin"

    def test_event_optional_fields(self):
        """Event has optional fields."""
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="sales_assistant",
            status="active",
            source="kaspi",
            external_ref="kaspi-123",
            effective_at="2026-01-15T00:00:00Z",
        )
        
        assert event.external_ref == "kaspi-123"
        assert event.effective_at == "2026-01-15T00:00:00Z"


class TestSubscriptionHandler:
    """Tests for subscription event handler."""

    def test_handler_function_exists(self):
        """Handler function exists."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        
        assert callable(handle_agent_addon_subscription_updated)

    def test_handler_returns_none_for_unknown_agent(self):
        """Handler returns None for unknown agent code."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="unknown_agent",
            status="active",
            source="admin",
        )
        
        result = handle_agent_addon_subscription_updated(session, event)
        
        assert result is None

    def test_handler_creates_subscription_when_missing(self):
        """Handler creates subscription when none exists."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        session = MagicMock()
        # First call returns SKU, second returns None (no existing subscription)
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, None]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="admin",
        )
        
        result = handle_agent_addon_subscription_updated(session, event)
        
        # Should have added a new subscription
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_handler_updates_existing_subscription(self):
        """Handler updates existing subscription."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "inactive"
        mock_sub.starts_at = "2026-01-01T00:00:00Z"
        mock_sub.ends_at = None
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="admin",
        )
        
        result = handle_agent_addon_subscription_updated(session, event)
        
        # Should have updated the subscription
        assert mock_sub.status == "active"
        session.commit.assert_called_once()


class TestStatusTransitions:
    """Tests for status transitions."""

    def test_inactive_to_active(self):
        """Transition from inactive to active."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "inactive"
        mock_sub.starts_at = None
        mock_sub.ends_at = None
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="admin",
        )
        
        handle_agent_addon_subscription_updated(session, event)
        
        assert mock_sub.status == "active"
        assert mock_sub.starts_at is not None  # Should be set

    def test_active_to_past_due(self):
        """Transition from active to past_due."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "active"
        mock_sub.starts_at = "2026-01-01T00:00:00Z"
        mock_sub.ends_at = None
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="past_due",
            source="kaspi",
        )
        
        handle_agent_addon_subscription_updated(session, event)
        
        assert mock_sub.status == "past_due"

    def test_past_due_to_active(self):
        """Transition from past_due back to active."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "past_due"
        mock_sub.starts_at = "2026-01-01T00:00:00Z"
        mock_sub.ends_at = None
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="kaspi",
        )
        
        handle_agent_addon_subscription_updated(session, event)
        
        assert mock_sub.status == "active"
        assert mock_sub.ends_at is None

    def test_active_to_canceled_sets_ends_at(self):
        """Transition to canceled sets ends_at."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "active"
        mock_sub.starts_at = "2026-01-01T00:00:00Z"
        mock_sub.ends_at = None
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="canceled",
            source="admin",
        )
        
        handle_agent_addon_subscription_updated(session, event)
        
        assert mock_sub.status == "canceled"
        assert mock_sub.ends_at is not None


class TestIdempotency:
    """Tests for handler idempotency."""

    def test_same_event_twice_is_safe(self):
        """Processing same event twice is idempotent."""
        from app.agents.subscription_handler import handle_agent_addon_subscription_updated
        from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
        from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        mock_sub = MagicMock(spec=TenantAgentSubscription)
        mock_sub.status = "active"
        mock_sub.starts_at = "2026-01-01T00:00:00Z"
        mock_sub.ends_at = None
        mock_sub.source = "admin"
        
        session = MagicMock()
        
        event = AgentAddonSubscriptionUpdated(
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="admin",
        )
        
        # First call
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        handle_agent_addon_subscription_updated(session, event)
        
        # Second call with same event
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, mock_sub]
        handle_agent_addon_subscription_updated(session, event)
        
        # Should still be active, no errors
        assert mock_sub.status == "active"


class TestServiceFunction:
    """Tests for update_agent_subscription_via_event service function."""

    def test_service_function_exists(self):
        """Service function exists."""
        from app.agents.subscription_handler import update_agent_subscription_via_event
        
        assert callable(update_agent_subscription_via_event)

    def test_service_function_creates_event(self):
        """Service function creates and processes event."""
        from app.agents.subscription_handler import update_agent_subscription_via_event
        from cyberplat.product.infrastructure.models import AgentSKU
        
        mock_sku = MagicMock(spec=AgentSKU)
        mock_sku.id = "sku-1"
        
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.side_effect = [mock_sku, None]
        
        result = update_agent_subscription_via_event(
            session=session,
            tenant_id="tenant-1",
            agent_code="test_agent",
            status="active",
            source="admin",
        )
        
        session.add.assert_called_once()
        session.commit.assert_called_once()
