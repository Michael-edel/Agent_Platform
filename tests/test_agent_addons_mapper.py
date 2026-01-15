"""Tests for agent addons webhook mapper."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestNormalizedBillingSignal:
    """Tests for NormalizedBillingSignal dataclass."""

    def test_signal_exists(self):
        """Signal dataclass exists."""
        from cyberplat.billing.application.agent_addons_mapper import NormalizedBillingSignal
        
        assert NormalizedBillingSignal is not None

    def test_signal_has_required_fields(self):
        """Signal has required fields."""
        from cyberplat.billing.application.agent_addons_mapper import NormalizedBillingSignal
        
        signal = NormalizedBillingSignal(
            source="stripe",
            event_type="customer.subscription.updated",
            tenant_id="tenant-1",
            agent_code="sales_assistant",
            status="active",
        )
        
        assert signal.source == "stripe"
        assert signal.tenant_id == "tenant-1"
        assert signal.agent_code == "sales_assistant"
        assert signal.status == "active"


class TestStripeMapper:
    """Tests for Stripe webhook mapper."""

    def test_non_subscription_event_returns_none(self):
        """Non-subscription events return None."""
        from cyberplat.billing.application.agent_addons_mapper import map_stripe_to_agent_addon_signal
        
        payload = {"type": "charge.succeeded", "data": {}}
        result = map_stripe_to_agent_addon_signal(payload)
        
        assert result is None

    def test_non_agent_addon_returns_none(self):
        """Subscription without addon_type=agent returns None."""
        from cyberplat.billing.application.agent_addons_mapper import map_stripe_to_agent_addon_signal
        
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "active",
                    "metadata": {"tenant_id": "t1"}  # No addon_type
                }
            }
        }
        result = map_stripe_to_agent_addon_signal(payload)
        
        assert result is None

    def test_valid_agent_addon_event(self):
        """Valid agent addon event returns signal."""
        from cyberplat.billing.application.agent_addons_mapper import map_stripe_to_agent_addon_signal
        
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "active",
                    "metadata": {
                        "tenant_id": "tenant-1",
                        "agent_code": "sales_assistant",
                        "addon_type": "agent"
                    }
                }
            }
        }
        result = map_stripe_to_agent_addon_signal(payload)
        
        assert result is not None
        assert result.source == "stripe"
        assert result.tenant_id == "tenant-1"
        assert result.agent_code == "sales_assistant"
        assert result.status == "active"
        assert result.external_ref == "sub_123"

    def test_past_due_status_mapping(self):
        """Stripe past_due maps correctly."""
        from cyberplat.billing.application.agent_addons_mapper import map_stripe_to_agent_addon_signal
        
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "past_due",
                    "metadata": {
                        "tenant_id": "tenant-1",
                        "agent_code": "sales_assistant",
                        "addon_type": "agent"
                    }
                }
            }
        }
        result = map_stripe_to_agent_addon_signal(payload)
        
        assert result.status == "past_due"

    def test_deleted_event_sets_canceled(self):
        """Subscription deleted event sets status to canceled."""
        from cyberplat.billing.application.agent_addons_mapper import map_stripe_to_agent_addon_signal
        
        payload = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "canceled",
                    "metadata": {
                        "tenant_id": "tenant-1",
                        "agent_code": "sales_assistant",
                        "addon_type": "agent"
                    }
                }
            }
        }
        result = map_stripe_to_agent_addon_signal(payload)
        
        assert result.status == "canceled"


class TestKaspiMapper:
    """Tests for Kaspi webhook mapper."""

    def test_non_subscription_event_returns_none(self):
        """Non-subscription events return None."""
        from cyberplat.billing.application.agent_addons_mapper import map_kaspi_to_agent_addon_signal
        
        payload = {"event_type": "PAYMENT_COMPLETED"}
        result = map_kaspi_to_agent_addon_signal(payload)
        
        assert result is None

    def test_non_agent_addon_returns_none(self):
        """Subscription without addon_type=agent returns None."""
        from cyberplat.billing.application.agent_addons_mapper import map_kaspi_to_agent_addon_signal
        
        payload = {
            "event_type": "SUBSCRIPTION_STATUS_CHANGED",
            "tenant_id": "t1",
            "status": "ACTIVE"  # No addon_type
        }
        result = map_kaspi_to_agent_addon_signal(payload)
        
        assert result is None

    def test_valid_agent_addon_event(self):
        """Valid agent addon event returns signal."""
        from cyberplat.billing.application.agent_addons_mapper import map_kaspi_to_agent_addon_signal
        
        payload = {
            "event_type": "SUBSCRIPTION_STATUS_CHANGED",
            "subscription_id": "kaspi-sub-123",
            "status": "ACTIVE",
            "tenant_id": "tenant-1",
            "agent_code": "sales_assistant",
            "addon_type": "agent"
        }
        result = map_kaspi_to_agent_addon_signal(payload)
        
        assert result is not None
        assert result.source == "kaspi"
        assert result.tenant_id == "tenant-1"
        assert result.agent_code == "sales_assistant"
        assert result.status == "active"
        assert result.external_ref == "kaspi-sub-123"

    def test_past_due_status_mapping(self):
        """Kaspi PAST_DUE maps correctly."""
        from cyberplat.billing.application.agent_addons_mapper import map_kaspi_to_agent_addon_signal
        
        payload = {
            "event_type": "SUBSCRIPTION_STATUS_CHANGED",
            "subscription_id": "kaspi-sub-123",
            "status": "PAST_DUE",
            "tenant_id": "tenant-1",
            "agent_code": "sales_assistant",
            "addon_type": "agent"
        }
        result = map_kaspi_to_agent_addon_signal(payload)
        
        assert result.status == "past_due"


class TestDispatcher:
    """Tests for dispatch_agent_addon_subscription_update."""

    def test_dry_run_does_not_call_handler(self):
        """Dry run mode does not call handler."""
        from cyberplat.billing.application.agent_addons_mapper import (
            dispatch_agent_addon_subscription_update,
            NormalizedBillingSignal,
        )
        
        signal = NormalizedBillingSignal(
            source="stripe",
            event_type="customer.subscription.updated",
            tenant_id="tenant-1",
            agent_code="sales_assistant",
            status="active",
        )
        
        session = MagicMock()
        
        with patch('cyberplat.billing.application.agent_addons_mapper.handle_agent_addon_subscription_updated') as mock_handler:
            result = dispatch_agent_addon_subscription_update(signal, session, dry_run=True)
            
            assert result is True
            mock_handler.assert_not_called()

    def test_non_dry_run_calls_handler(self):
        """Non dry-run mode calls handler."""
        from cyberplat.billing.application.agent_addons_mapper import (
            dispatch_agent_addon_subscription_update,
            NormalizedBillingSignal,
        )
        
        signal = NormalizedBillingSignal(
            source="stripe",
            event_type="customer.subscription.updated",
            tenant_id="tenant-1",
            agent_code="sales_assistant",
            status="active",
        )
        
        session = MagicMock()
        
        with patch('cyberplat.billing.application.agent_addons_mapper.handle_agent_addon_subscription_updated') as mock_handler:
            mock_handler.return_value = MagicMock()  # Successful result
            
            result = dispatch_agent_addon_subscription_update(signal, session, dry_run=False)
            
            assert result is True
            mock_handler.assert_called_once()


class TestDryRunConfig:
    """Tests for dry-run configuration."""

    def test_is_dry_run_enabled_default_false(self, monkeypatch):
        """Dry-run is disabled by default."""
        monkeypatch.delenv("BILLING_WEBHOOK_DRY_RUN", raising=False)
        
        from cyberplat.billing.application.agent_addons_mapper import is_dry_run_enabled
        
        assert is_dry_run_enabled() is False

    def test_is_dry_run_enabled_when_set(self, monkeypatch):
        """Dry-run enabled when ENV set to true."""
        monkeypatch.setenv("BILLING_WEBHOOK_DRY_RUN", "true")
        
        from cyberplat.billing.application.agent_addons_mapper import is_dry_run_enabled
        
        assert is_dry_run_enabled() is True


class TestProcessWebhook:
    """Tests for process_webhook_for_agent_addons."""

    def test_unknown_source_returns_true(self):
        """Unknown source returns True (not an error)."""
        from cyberplat.billing.application.agent_addons_mapper import process_webhook_for_agent_addons
        
        session = MagicMock()
        result = process_webhook_for_agent_addons("unknown", {}, session)
        
        assert result is True

    def test_non_agent_addon_returns_true(self):
        """Non agent addon payload returns True."""
        from cyberplat.billing.application.agent_addons_mapper import process_webhook_for_agent_addons
        
        session = MagicMock()
        payload = {"type": "charge.succeeded", "data": {}}
        
        result = process_webhook_for_agent_addons("stripe", payload, session)
        
        assert result is True

    def test_agent_addon_event_dispatched(self):
        """Agent addon event is dispatched."""
        from cyberplat.billing.application.agent_addons_mapper import process_webhook_for_agent_addons
        
        session = MagicMock()
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "active",
                    "metadata": {
                        "tenant_id": "tenant-1",
                        "agent_code": "sales_assistant",
                        "addon_type": "agent"
                    }
                }
            }
        }
        
        with patch('cyberplat.billing.application.agent_addons_mapper.dispatch_agent_addon_subscription_update') as mock_dispatch:
            mock_dispatch.return_value = True
            
            result = process_webhook_for_agent_addons("stripe", payload, session, dry_run=True)
            
            assert result is True
            mock_dispatch.assert_called_once()
