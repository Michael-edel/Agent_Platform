"""Tests for admin drill-down links."""

import pytest


class TestLinkHelpers:
    """Tests for centralized link helper functions."""

    def test_safe_text_escapes_html(self):
        """safe_text escapes HTML characters."""
        from app.admin.links import safe_text
        
        result = safe_text("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_safe_path_url_encodes(self):
        """safe_path URL-encodes special characters."""
        from app.admin.links import safe_path
        
        result = safe_path("tenant with spaces&special=chars")
        assert " " not in result
        assert "&" not in result
        assert "=" not in result
        assert "%20" in result or "+" in result  # space encoding

    def test_build_admin_list_url_basic(self):
        """build_admin_list_url creates correct base URL."""
        from app.admin.links import build_admin_list_url
        
        result = build_admin_list_url("tenant-subscription")
        assert result == "/admin/tenant-subscription/list"

    def test_build_admin_list_url_with_query(self):
        """build_admin_list_url adds query parameters."""
        from app.admin.links import build_admin_list_url
        
        result = build_admin_list_url("billing-order", {"tenant_id": "t123", "status": "paid"})
        assert "/admin/billing-order/list?" in result
        assert "tenant_id=t123" in result
        assert "status=paid" in result

    def test_build_admin_list_url_escapes_query_values(self):
        """build_admin_list_url escapes special chars in query values."""
        from app.admin.links import build_admin_list_url
        
        result = build_admin_list_url("test", {"id": "a&b=c"})
        assert "a&b=c" not in result  # Should be encoded


class TestDrillDownLinks:
    """Tests for drill-down link generation."""

    def test_tenant_drill_links_contains_routes(self):
        """Drill-down links contain correct admin routes."""
        from app.admin.links import tenant_drill_links
        
        result = tenant_drill_links("tenant-123")
        html = str(result)
        
        assert "tenant-subscription/list" in html
        assert "billing-order/list" in html
        assert "billing-webhook-event/list" in html
        assert "tenant_id=tenant-123" in html
        assert "tenant-123" in html

    def test_tenant_drill_links_empty_for_none(self):
        """Empty string returned for None tenant_id."""
        from app.admin.links import tenant_drill_links
        
        assert str(tenant_drill_links(None)) == ""
        assert str(tenant_drill_links("")) == ""

    def test_tenant_drill_links_escaped_properly(self):
        """Tenant ID with special chars is handled safely."""
        from app.admin.links import tenant_drill_links
        
        result = tenant_drill_links("tenant<script>alert(1)</script>")
        html = str(result)
        
        # Display text should be escaped
        assert "&lt;script&gt;" in html
        # URL should be encoded (no raw <script>)
        assert "<script>" not in html


class TestViewFormatters:
    """Tests for column formatters in views."""

    def test_tenant_plan_admin_has_formatter(self):
        """TenantPlanAdmin has tenant_id column formatter."""
        from app.admin.views import TenantPlanAdmin
        assert "tenant_id" in TenantPlanAdmin().column_formatters

    def test_tenant_subscription_admin_has_formatter(self):
        """TenantSubscriptionAdmin has tenant_id column formatter."""
        from app.admin.views import TenantSubscriptionAdmin
        assert "tenant_id" in TenantSubscriptionAdmin().column_formatters

    def test_billing_order_admin_has_formatter(self):
        """BillingOrderAdmin has tenant_id column formatter."""
        from app.admin.views import BillingOrderAdmin
        assert "tenant_id" in BillingOrderAdmin().column_formatters

    def test_billing_webhook_event_admin_has_formatter(self):
        """BillingWebhookEventAdmin has tenant_id column formatter."""
        from app.admin.views import BillingWebhookEventAdmin
        assert "tenant_id" in BillingWebhookEventAdmin().column_formatters

    def test_billing_usage_admin_has_formatter(self):
        """BillingUsageAdmin has tenant_id column formatter."""
        from app.admin.views import BillingUsageAdmin
        assert "tenant_id" in BillingUsageAdmin().column_formatters
