"""Tests for BillingWebhookEventAdmin configuration."""

import pytest


class TestBillingWebhookEventAdmin:
    """Tests for webhook events admin view configuration."""

    def test_is_read_only(self):
        """View is read-only (no create/edit/delete)."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert view.can_create is False
        assert view.can_edit is False
        assert view.can_delete is False

    def test_has_search_fields(self):
        """View has searchable fields configured."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert hasattr(view, "column_searchable_list")
        assert "event_id" in view.column_searchable_list
        assert "provider" in view.column_searchable_list
        assert "status" in view.column_searchable_list

    def test_has_filters(self):
        """View has filters configured."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert hasattr(view, "column_filters")
        assert "provider" in view.column_filters
        assert "status" in view.column_filters

    def test_raw_json_excluded_from_list(self):
        """raw_json is not in column_list."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert "raw_json" not in view.column_list

    def test_raw_json_excluded_from_details(self):
        """raw_json is excluded from detail view."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert hasattr(view, "column_details_exclude_list")
        assert "raw_json" in view.column_details_exclude_list

    def test_has_sortable_columns(self):
        """View has sortable columns for time fields."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert hasattr(view, "column_sortable_list")
        assert "received_at" in view.column_sortable_list
        assert "processed_at" in view.column_sortable_list

    def test_has_default_sort(self):
        """View sorts by received_at descending by default."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert hasattr(view, "column_default_sort")
        assert view.column_default_sort == ("received_at", True)

    def test_has_tenant_id_formatter(self):
        """View has tenant_id column formatter for drill-down."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert "tenant_id" in view.column_formatters

    def test_has_error_formatter(self):
        """View has error column formatter for truncation."""
        from app.admin.views import BillingWebhookEventAdmin
        
        view = BillingWebhookEventAdmin()
        
        assert "error" in view.column_formatters


class TestTruncateError:
    """Tests for error truncation helper."""

    def test_truncates_long_text(self):
        """Long text is truncated with ellipsis."""
        from app.admin.views import truncate_error
        
        long_text = "a" * 300
        result = truncate_error(long_text, 100)
        
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_keeps_short_text(self):
        """Short text is not truncated."""
        from app.admin.views import truncate_error
        
        short_text = "Short error"
        result = truncate_error(short_text, 100)
        
        assert result == "Short error"

    def test_escapes_html(self):
        """HTML in error text is escaped."""
        from app.admin.views import truncate_error
        
        html_text = "<script>alert('xss')</script>"
        result = truncate_error(html_text)
        
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_handles_none(self):
        """None returns empty string."""
        from app.admin.views import truncate_error
        
        assert truncate_error(None) == ""
        assert truncate_error("") == ""
