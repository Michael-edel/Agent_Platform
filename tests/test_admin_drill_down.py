"""Tests for admin drill-down links."""

import pytest


class TestDrillDownLinks:
    """Tests for drill-down link generation."""

    def test_make_tenant_drill_links_contains_routes(self):
        """Drill-down links contain correct admin routes."""
        from app.admin.views import make_tenant_drill_links
        
        result = make_tenant_drill_links("tenant-123")
        html = str(result)
        
        # Should contain links to related entities
        assert "tenant-subscription/list" in html
        assert "billing-order/list" in html
        assert "billing-webhook-event/list" in html
        
        # Should contain tenant_id parameter
        assert "tenant_id=tenant-123" in html
        
        # Should display tenant_id
        assert "tenant-123" in html

    def test_make_tenant_drill_links_empty_for_none(self):
        """Empty string returned for None tenant_id."""
        from app.admin.views import make_tenant_drill_links
        
        result = make_tenant_drill_links(None)
        assert str(result) == ""
        
        result = make_tenant_drill_links("")
        assert str(result) == ""

    def test_drill_links_escaped_properly(self):
        """Tenant ID with special chars is handled safely."""
        from app.admin.views import make_tenant_drill_links
        
        # Tenant ID with chars that could be XSS
        result = make_tenant_drill_links("tenant<script>alert(1)</script>")
        html = str(result)
        
        # The tenant_id appears in URL (should be URL-safe context)
        # and also as text. Markupsafe should handle escaping.
        assert "<script>" not in html or "&lt;script&gt;" in html

    def test_tenant_plan_admin_has_formatter(self):
        """TenantPlanAdmin has tenant_id column formatter."""
        from app.admin.views import TenantPlanAdmin
        
        view = TenantPlanAdmin()
        assert "tenant_id" in view.column_formatters

    def test_tenant_subscription_admin_has_formatter(self):
        """TenantSubscriptionAdmin has tenant_id column formatter."""
        from app.admin.views import TenantSubscriptionAdmin
        
        view = TenantSubscriptionAdmin()
        assert "tenant_id" in view.column_formatters

    def test_billing_order_admin_has_formatter(self):
        """BillingOrderAdmin has tenant_id column formatter."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        assert "tenant_id" in view.column_formatters
