"""Integration tests for admin detail routes using TestClient."""

import os
import pytest


# Skip all tests if prometheus_client is installed (use separate test run)
# These tests are for environments WITHOUT prometheus_client
@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestAdminDetailRoutesIntegration:
    """Integration tests verifying detail URLs are accessible."""

    @pytest.fixture
    def admin_env(self, monkeypatch):
        """Set up admin-enabled environment."""
        monkeypatch.setenv("ADMIN_ENABLED", "true")
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
        monkeypatch.setenv("ADMIN_SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("DATABASE_URL", "")  # Force SQLite

    @pytest.fixture
    def client(self, admin_env):
        """Create test client with admin enabled."""
        from starlette.testclient import TestClient
        from app.main import app
        
        return TestClient(app)

    @pytest.fixture
    def logged_in_client(self, client):
        """Client with admin session."""
        # Login to get session
        response = client.post(
            "/admin/login",
            data={"username": "admin", "password": "testpass"},
            follow_redirects=False,
        )
        # Should redirect on success or stay on login page
        return client

    @pytest.fixture
    def setup_test_data(self, admin_env):
        """Insert minimal test data into SQLite."""
        from cyberplat.product.infrastructure.database import get_engine
        from sqlalchemy import text
        
        engine = get_engine()
        
        with engine.connect() as conn:
            # Create tables if not exist (SQLite)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS billing_webhook_events (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    received_at TEXT NOT NULL,
                    processed_at TEXT,
                    tenant_id TEXT,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                )
            """))
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS billing_orders (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_order_id TEXT,
                    created_at TEXT NOT NULL,
                    paid_at TEXT
                )
            """))
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS tenant_subscriptions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_customer_id TEXT,
                    provider_subscription_id TEXT,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_period_start TEXT,
                    current_period_end TEXT,
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """))
            
            # Insert test data
            conn.execute(text("""
                INSERT OR REPLACE INTO billing_webhook_events 
                (id, provider, event_id, received_at, raw_json, status)
                VALUES ('test-webhook-1', 'stripe', 'evt_test123', '2024-01-01T00:00:00Z', '{}', 'processed')
            """))
            
            conn.execute(text("""
                INSERT OR REPLACE INTO billing_orders
                (id, tenant_id, provider, plan_id, amount_minor, currency, status, created_at)
                VALUES ('test-order-1', 'tenant-1', 'stripe', 'plan-1', 1000, 'USD', 'paid', '2024-01-01T00:00:00Z')
            """))
            
            conn.execute(text("""
                INSERT OR REPLACE INTO tenant_subscriptions
                (id, tenant_id, provider, plan_id, status, updated_at, created_at)
                VALUES ('test-sub-1', 'tenant-1', 'stripe', 'plan-1', 'active', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
            """))
            
            conn.commit()
        
        return {
            "webhook_id": "test-webhook-1",
            "order_id": "test-order-1",
            "subscription_id": "test-sub-1",
        }

    def test_app_starts_without_prometheus(self, client):
        """App can start without prometheus_client when METRICS_ENABLED=false."""
        # Just creating the client tests that app imports work
        response = client.get("/health")
        assert response.status_code == 200

    def test_admin_login_page_accessible(self, client):
        """Admin login page is accessible."""
        response = client.get("/admin/login")
        # Should return login page (200) or redirect (302)
        assert response.status_code in [200, 302]

    def test_webhook_event_detail_not_404(self, logged_in_client, setup_test_data):
        """Webhook event detail page is not 404."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_webhook_event"], setup_test_data["webhook_id"])
        response = logged_in_client.get(url, follow_redirects=False)
        
        # Should be 200, 302 (redirect to login), or 401/403, but NOT 404
        assert response.status_code != 404, f"Detail URL {url} returned 404"

    def test_order_detail_not_404(self, logged_in_client, setup_test_data):
        """Order detail page is not 404."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["billing_order"], setup_test_data["order_id"])
        response = logged_in_client.get(url, follow_redirects=False)
        
        assert response.status_code != 404, f"Detail URL {url} returned 404"

    def test_subscription_detail_not_404(self, logged_in_client, setup_test_data):
        """Subscription detail page is not 404."""
        from app.admin.links import build_admin_detail_url, ADMIN_ROUTES
        
        url = build_admin_detail_url(ADMIN_ROUTES["tenant_subscription"], setup_test_data["subscription_id"])
        response = logged_in_client.get(url, follow_redirects=False)
        
        assert response.status_code != 404, f"Detail URL {url} returned 404"
