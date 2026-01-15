"""Tests for tenant scoping in admin views."""

import pytest
from unittest.mock import MagicMock, patch


class TestTenantScopedMixin:
    """Tests for TenantScopedMixin list_query and count_query."""

    def test_tenant_admin_count_query_has_filter(self):
        """count_query for tenant_admin includes tenant_id filter."""
        from app.admin.views import TenantSubscriptionAdmin
        
        mock_request = MagicMock()
        
        with patch("app.admin.views.get_admin_role", return_value="tenant_admin"):
            with patch("app.admin.views.get_admin_tenant_id", return_value="tenant-abc"):
                view = TenantSubscriptionAdmin()
                query = view.count_query(mock_request)
        
        # Compile query to string and check for tenant_id filter
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled.lower()
        assert "tenant-abc" in compiled

    def test_tenant_admin_list_query_has_filter(self):
        """list_query for tenant_admin includes tenant_id filter."""
        from app.admin.views import BillingOrderAdmin
        
        mock_request = MagicMock()
        
        with patch("app.admin.views.get_admin_role", return_value="tenant_admin"):
            with patch("app.admin.views.get_admin_tenant_id", return_value="tenant-xyz"):
                view = BillingOrderAdmin()
                query = view.list_query(mock_request)
        
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled.lower()
        assert "tenant-xyz" in compiled

    def test_platform_admin_count_query_no_filter(self):
        """count_query for platform_admin has no tenant_id filter."""
        from app.admin.views import TenantSubscriptionAdmin
        
        mock_request = MagicMock()
        
        with patch("app.admin.views.get_admin_role", return_value="platform_admin"):
            with patch("app.admin.views.get_admin_tenant_id", return_value=None):
                view = TenantSubscriptionAdmin()
                query = view.count_query(mock_request)
        
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        # Should not have WHERE tenant_id = 'something'
        assert "tenant_id =" not in compiled.lower()

    def test_platform_admin_list_query_no_filter(self):
        """list_query for platform_admin has no tenant_id filter."""
        from app.admin.views import BillingUsageAdmin
        
        mock_request = MagicMock()
        
        with patch("app.admin.views.get_admin_role", return_value="platform_admin"):
            with patch("app.admin.views.get_admin_tenant_id", return_value=None):
                view = BillingUsageAdmin()
                query = view.list_query(mock_request)
        
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id =" not in compiled.lower()

    def test_tenant_admin_without_tenant_id_returns_empty(self):
        """tenant_admin without tenant_id gets empty result (WHERE false)."""
        from app.admin.views import KaspiOrderAdmin
        
        mock_request = MagicMock()
        
        with patch("app.admin.views.get_admin_role", return_value="tenant_admin"):
            with patch("app.admin.views.get_admin_tenant_id", return_value=None):
                view = KaspiOrderAdmin()
                list_q = view.list_query(mock_request)
                count_q = view.count_query(mock_request)
        
        # Both queries should have false() condition
        list_compiled = str(list_q.compile(compile_kwargs={"literal_binds": True})).lower()
        count_compiled = str(count_q.compile(compile_kwargs={"literal_binds": True})).lower()
        
        # SQLAlchemy compiles false() as "false" or "0" depending on dialect
        assert "false" in list_compiled or "0 = 1" in list_compiled or "1 = 0" in list_compiled
        assert "false" in count_compiled or "0 = 1" in count_compiled or "1 = 0" in count_compiled

    def test_all_tenant_scoped_views_have_count_query(self):
        """All tenant-scoped views have count_query method."""
        from app.admin.views import (
            TenantPlanAdmin,
            WebhookAdmin,
            KaspiOrderAdmin,
            ArtifactStateAdmin,
            ExportAdmin,
            TenantSubscriptionAdmin,
            BillingWebhookEventAdmin,
            BillingOrderAdmin,
            BillingUsageAdmin,
        )
        
        tenant_scoped_views = [
            TenantPlanAdmin,
            WebhookAdmin,
            KaspiOrderAdmin,
            ArtifactStateAdmin,
            ExportAdmin,
            TenantSubscriptionAdmin,
            BillingWebhookEventAdmin,
            BillingOrderAdmin,
            BillingUsageAdmin,
        ]
        
        for view_cls in tenant_scoped_views:
            view = view_cls()
            assert hasattr(view, "count_query"), f"{view_cls.__name__} missing count_query"
            assert hasattr(view, "list_query"), f"{view_cls.__name__} missing list_query"
