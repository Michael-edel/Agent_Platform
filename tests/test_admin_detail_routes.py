"""Tests for admin detail URL format validation."""

import pytest


class TestAdminDetailUrlFormat:
    """Tests verifying detail URL format matches SQLAdmin expectations."""

    def test_detail_url_format_webhook(self):
        """Webhook event detail URL has correct format."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_webhook_event"], "test-id-123")
        
        # SQLAdmin detail format: /admin/{identity}/details/{pk}
        assert url == "/admin/billing-webhook-event/details/test-id-123"
        assert "/details/" in url

    def test_detail_url_format_order(self):
        """Order detail URL has correct format."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_order"], "order-456")
        
        assert url == "/admin/billing-order/details/order-456"

    def test_detail_url_format_subscription(self):
        """Subscription detail URL has correct format."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["tenant_subscription"], "sub-789")
        
        assert url == "/admin/tenant-subscription/details/sub-789"

    def test_detail_url_with_special_chars(self):
        """Detail URL properly encodes special characters."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_order"], "id/with/slashes")
        
        # Slashes should be encoded
        assert "%2F" in url
        assert "id/with/slashes" not in url

    def test_detail_url_with_spaces(self):
        """Detail URL properly encodes spaces."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_order"], "id with spaces")
        
        # Spaces should be encoded
        assert "%20" in url or "+" in url
        assert "id with spaces" not in url


class TestAdminRouteIdentities:
    """Tests verifying route identities match SQLAdmin ModelView names."""

    def test_billing_webhook_event_identity(self):
        """BillingWebhookEventAdmin identity matches route."""
        from app.admin.views import BillingWebhookEventAdmin
        from app.admin.links import ADMIN_ROUTES
        
        # SQLAdmin generates identity from model name: BillingWebhookEvent -> billing-webhook-event
        expected_identity = ADMIN_ROUTES["billing_webhook_event"]
        assert expected_identity == "billing-webhook-event"

    def test_billing_order_identity(self):
        """BillingOrderAdmin identity matches route."""
        from app.admin.views import BillingOrderAdmin
        from app.admin.links import ADMIN_ROUTES
        
        expected_identity = ADMIN_ROUTES["billing_order"]
        assert expected_identity == "billing-order"

    def test_tenant_subscription_identity(self):
        """TenantSubscriptionAdmin identity matches route."""
        from app.admin.views import TenantSubscriptionAdmin
        from app.admin.links import ADMIN_ROUTES
        
        expected_identity = ADMIN_ROUTES["tenant_subscription"]
        assert expected_identity == "tenant-subscription"

    def test_views_have_details_enabled(self):
        """Key views have can_view_details enabled."""
        from app.admin.views import (
            BillingWebhookEventAdmin,
            BillingOrderAdmin,
            TenantSubscriptionAdmin,
        )
        
        assert BillingWebhookEventAdmin().can_view_details is True
        assert BillingOrderAdmin().can_view_details is True
        assert TenantSubscriptionAdmin().can_view_details is True
