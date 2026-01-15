"""Tests for BillingOrderAdmin configuration."""

import pytest


class TestBillingOrderAdmin:
    """Tests for billing orders admin view configuration."""

    def test_is_read_only(self):
        """View is read-only (no create/edit/delete)."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert view.can_create is False
        assert view.can_edit is False
        assert view.can_delete is False

    def test_has_details_view(self):
        """View allows detail view."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert view.can_view_details is True

    def test_has_search_fields(self):
        """View has searchable fields configured."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert hasattr(view, "column_searchable_list")
        assert "provider" in view.column_searchable_list
        assert "status" in view.column_searchable_list
        assert "external_order_id" in view.column_searchable_list

    def test_has_filters(self):
        """View has filters configured."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert hasattr(view, "column_filters")
        assert "provider" in view.column_filters
        assert "status" in view.column_filters
        assert "tenant_id" in view.column_filters

    def test_has_sortable_columns(self):
        """View has sortable columns."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert hasattr(view, "column_sortable_list")
        assert "created_at" in view.column_sortable_list
        assert "paid_at" in view.column_sortable_list

    def test_has_default_sort(self):
        """View sorts by created_at descending by default."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert hasattr(view, "column_default_sort")
        assert view.column_default_sort == ("created_at", True)

    def test_has_tenant_id_formatter(self):
        """View has tenant_id column formatter for drill-down."""
        from app.admin.views import BillingOrderAdmin
        
        view = BillingOrderAdmin()
        
        assert "tenant_id" in view.column_formatters
