"""Tests for admin search view."""

import pytest
from unittest.mock import MagicMock, patch


class TestBuildSearchResults:
    """Tests for build_search_results function."""

    def test_empty_query_returns_empty(self):
        """Empty query returns empty results."""
        from app.admin.search import build_search_results
        
        results = build_search_results("", "platform_admin", None)
        
        assert results["webhook_events"] == []
        assert results["orders"] == []
        assert results["subscriptions"] == []
        assert results["error"] is None

    def test_no_role_returns_empty(self):
        """No role returns empty results (deny by default)."""
        from app.admin.search import build_search_results
        
        results = build_search_results("test", None, None)
        
        assert results["webhook_events"] == []
        assert results["orders"] == []
        assert results["subscriptions"] == []

    def test_tenant_admin_without_tenant_id_returns_empty(self):
        """tenant_admin without tenant_id returns empty (fail-closed)."""
        from app.admin.search import build_search_results
        
        results = build_search_results("test", "tenant_admin", None)
        
        assert results["webhook_events"] == []
        assert results["orders"] == []
        assert results["subscriptions"] == []

    def test_graceful_error_handling(self):
        """Database errors are handled gracefully."""
        with patch("app.admin.search.get_engine") as mock_engine:
            mock_engine.side_effect = Exception("DB connection failed")
            
            from app.admin.search import build_search_results
            results = build_search_results("test", "platform_admin", None)
        
        assert results["error"] is not None
        assert "DB connection failed" in results["error"]

    def test_error_truncated(self):
        """Long error messages are truncated."""
        long_error = "x" * 200
        
        with patch("app.admin.search.get_engine") as mock_engine:
            mock_engine.side_effect = Exception(long_error)
            
            from app.admin.search import build_search_results
            results = build_search_results("test", "platform_admin", None)
        
        assert len(results["error"]) <= 100


class TestSearchView:
    """Tests for SearchView configuration."""

    def test_has_name_and_icon(self):
        """SearchView has name and icon set."""
        from app.admin.search import SearchView
        
        assert SearchView.name == "Search"
        assert "magnifying-glass" in SearchView.icon


class TestBuildAdminDetailUrl:
    """Tests for build_admin_detail_url helper."""

    def test_basic_url(self):
        """Basic URL structure is correct."""
        from app.admin.links import build_admin_detail_url
        
        url = build_admin_detail_url("billing-order", "abc123")
        
        assert url == "/admin/billing-order/details/abc123"

    def test_special_characters_encoded(self):
        """Special characters in pk are URL encoded."""
        from app.admin.links import build_admin_detail_url
        
        url = build_admin_detail_url("billing-order", "id/with/slashes")
        
        assert "id%2Fwith%2Fslashes" in url
        assert "id/with/slashes" not in url

    def test_xss_safe(self):
        """XSS attempts are encoded."""
        from app.admin.links import build_admin_detail_url
        
        url = build_admin_detail_url("test", "<script>alert(1)</script>")
        
        assert "<script>" not in url
        assert "%3Cscript%3E" in url
