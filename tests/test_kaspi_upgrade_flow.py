"""Kaspi upgrade flow tests: checkout -> webhook paid -> tenant_plans updated (idempotent)."""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient

from cyberplat.billing_entitlements import EntitlementService
from cyberplat.billing_service import BillingService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)


@pytest.fixture
def app_with_services(temp_db):
    """
    FastAPI app with billing/entitlement wired to a temp DB.
    Also creates SQLAlchemy tables for plans/tenant_plans/kaspi_orders.
    """
    os.environ["PLATFORM_DB_PATH"] = temp_db
    os.environ["DATABASE_URL"] = f"sqlite:///{temp_db}"
    os.environ["KASPI_ENABLED"] = "1"
    os.environ["KASPI_WEBHOOK_SECRET"] = "test-secret"
    os.environ["BILLING_PERIOD_DAYS"] = "30"

    # legacy schemas
    bs = BillingService(db_path=temp_db)
    bs.close()
    entitlement = EntitlementService(db_path=temp_db)
    entitlement.ensure_schema()
    entitlement.seed_default_plans_if_empty()  # legacy billing_plans (plan_pro, ...)

    # SQLAlchemy schemas
    from cyberplat.product.infrastructure import database as product_db
    product_db._engine = None
    product_db._SessionLocal = None
    engine = product_db.get_engine()

    from cyberplat.product.infrastructure.models import Base, Plan
    Base.metadata.create_all(bind=engine)

    # seed plans table (new)
    SessionLocal = product_db.get_sessionmaker()
    session = SessionLocal()
    try:
        if not session.query(Plan).filter(Plan.id == "pro").first():
            session.add(
                Plan(
                    id="pro",
                    name="Pro",
                    description="Pro plan",
                    quotas=json.dumps({"document_upload": 100, "invoice_extracted": 50, "page_processed": 500}),
                    price_minor=2900,
                    currency="KZT",
                    active=True,
                    created_at=datetime.now().isoformat(),
                )
            )
        if not session.query(Plan).filter(Plan.id == "enterprise").first():
            session.add(
                Plan(
                    id="enterprise",
                    name="Enterprise",
                    description="Enterprise plan",
                    quotas=json.dumps({"document_upload": None, "invoice_extracted": None, "page_processed": None}),
                    price_minor=9900,
                    currency="KZT",
                    active=True,
                    created_at=datetime.now().isoformat(),
                )
            )
        if not session.query(Plan).filter(Plan.id == "trial").first():
            session.add(
                Plan(
                    id="trial",
                    name="Trial",
                    description="Trial plan",
                    quotas=json.dumps({"document_upload": 20, "invoice_extracted": 10, "page_processed": 50}),
                    price_minor=None,
                    currency=None,
                    active=True,
                    created_at=datetime.now().isoformat(),
                )
            )
        session.commit()
    finally:
        session.close()

    from app.main import app
    app.state.billing_service = BillingService(db_path=temp_db)
    app.state.entitlement_service = entitlement
    app.state.event_service = Mock()  # capture emits

    yield app

    # cleanup
    try:
        app.state.billing_service.close()
    except Exception:
        pass
    try:
        entitlement.close()
    except Exception:
        pass


def test_checkout_creates_kaspi_order_row(app_with_services):
    client = TestClient(app_with_services)

    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.return_value = {
            "checkout_url": "https://kaspi.test/checkout",
            "external_order_id": "kaspi_ext_1",
        }

        resp = client.post(
            "/api/v1/billing/checkout/kaspi",
            headers={"X-Tenant-ID": "tenant-1"},
            json={"plan_id": "pro"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["checkout_url"] == "https://kaspi.test/checkout"
        assert body["kaspi_order_id"] == "kaspi_ext_1"

    # verify kaspi_orders row
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.kaspi_order_repository_sqlalchemy import KaspiOrderRepositoryImpl

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        repo = KaspiOrderRepositoryImpl(session=session)
        row = repo.get_by_kaspi_order_id("kaspi_ext_1")
        assert row is not None
        assert row["tenant_id"] == "tenant-1"
        assert row["plan_id"] == "pro"
        assert row["status"] == "created"
    finally:
        session.close()


def test_webhook_paid_applies_plan_and_is_idempotent(app_with_services):
    client = TestClient(app_with_services)

    # create checkout first
    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.return_value = {
            "checkout_url": "https://kaspi.test/checkout",
            "external_order_id": "kaspi_ext_2",
        }
        resp = client.post(
            "/api/v1/billing/checkout/kaspi",
            headers={"X-Tenant-ID": "tenant-2"},
            json={"plan_id": "pro"},
        )
        assert resp.status_code == 200

    # webhook paid (signature verification patched)
    payload = {"id": "evt_paid_1", "type": "payment.paid", "external_order_id": "kaspi_ext_2"}
    body_bytes = json.dumps(payload).encode("utf-8")

    with patch("cyberplat.billing.infrastructure.kaspi_provider.kaspi_verify_signature", return_value=True):
        r1 = client.post(
            "/api/v1/billing/webhook/kaspi",
            data=body_bytes,
            headers={"X-Kaspi-Signature": "sig"},
        )
        assert r1.status_code == 200

        # Verify tenant_plans switched to pro
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

        SessionLocal = get_sessionmaker()
        session = SessionLocal()
        try:
            tp_repo = TenantPlanRepositoryImpl(session=session)
            tp = tp_repo.get_tenant_plan("tenant-2")
            assert tp is not None
            assert tp["plan_id"] == "pro"
            assert tp.get("subscription_status") == "active"
            assert tp.get("failed_charges") == 0
            assert tp.get("expires_at") is not None
            started_at_1 = tp["started_at"]
        finally:
            session.close()

        # Replay same webhook (idempotent)
        r2 = client.post(
            "/api/v1/billing/webhook/kaspi",
            data=body_bytes,
            headers={"X-Kaspi-Signature": "sig"},
        )
        assert r2.status_code == 200

        SessionLocal = get_sessionmaker()
        session = SessionLocal()
        try:
            tp_repo = TenantPlanRepositoryImpl(session=session)
            tp = tp_repo.get_tenant_plan("tenant-2")
            assert tp["plan_id"] == "pro"
            assert tp["started_at"] == started_at_1, "Idempotent replay must not re-assign plan"
        finally:
            session.close()


def test_webhook_failed_marks_order_failed(app_with_services):
    client = TestClient(app_with_services)

    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.return_value = {
            "checkout_url": "https://kaspi.test/checkout",
            "external_order_id": "kaspi_ext_3",
        }
        resp = client.post(
            "/api/v1/billing/checkout/kaspi",
            headers={"X-Tenant-ID": "tenant-3"},
            json={"plan_id": "enterprise"},
        )
        assert resp.status_code == 200

    payload = {"id": "evt_failed_1", "type": "payment.failed", "external_order_id": "kaspi_ext_3"}
    body_bytes = json.dumps(payload).encode("utf-8")

    with patch("cyberplat.billing.infrastructure.kaspi_provider.kaspi_verify_signature", return_value=True):
        r = client.post(
            "/api/v1/billing/webhook/kaspi",
            data=body_bytes,
            headers={"X-Kaspi-Signature": "sig"},
        )
        assert r.status_code == 200

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.kaspi_order_repository_sqlalchemy import KaspiOrderRepositoryImpl

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        repo = KaspiOrderRepositoryImpl(session=session)
        row = repo.get_by_kaspi_order_id("kaspi_ext_3")
        assert row is not None
        assert row["status"] in ("failed", "canceled")
    finally:
        session.close()


def test_tenant_isolation_kaspi_orders(app_with_services):
    client = TestClient(app_with_services)

    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.side_effect = [
            {"checkout_url": "https://kaspi.test/1", "external_order_id": "kaspi_t1"},
            {"checkout_url": "https://kaspi.test/2", "external_order_id": "kaspi_t2"},
        ]
        client.post("/api/v1/billing/checkout/kaspi", headers={"X-Tenant-ID": "t1"}, json={"plan_id": "pro"})
        client.post("/api/v1/billing/checkout/kaspi", headers={"X-Tenant-ID": "t2"}, json={"plan_id": "enterprise"})

    payload = {"id": "evt_paid_t1", "type": "payment.paid", "external_order_id": "kaspi_t1"}
    body_bytes = json.dumps(payload).encode("utf-8")

    with patch("cyberplat.billing.infrastructure.kaspi_provider.kaspi_verify_signature", return_value=True):
        client.post("/api/v1/billing/webhook/kaspi", data=body_bytes, headers={"X-Kaspi-Signature": "sig"})

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        tp_repo = TenantPlanRepositoryImpl(session=session)
        assert tp_repo.get_tenant_plan("t1")["plan_id"] == "pro"
        # t2 should NOT be upgraded by t1 webhook
        assert tp_repo.get_tenant_plan("t2")["plan_id"] != "pro"
    finally:
        session.close()


def test_portal_reflects_upgraded_plan(app_with_services):
    client = TestClient(app_with_services)

    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.return_value = {"checkout_url": "https://kaspi.test/checkout", "external_order_id": "kaspi_ext_4"}
        client.post("/api/v1/billing/checkout/kaspi", headers={"X-Tenant-ID": "tenant-4"}, json={"plan_id": "pro"})

    payload = {"id": "evt_paid_4", "type": "payment.paid", "external_order_id": "kaspi_ext_4"}
    body_bytes = json.dumps(payload).encode("utf-8")
    with patch("cyberplat.billing.infrastructure.kaspi_provider.kaspi_verify_signature", return_value=True):
        client.post("/api/v1/billing/webhook/kaspi", data=body_bytes, headers={"X-Kaspi-Signature": "sig"})

    resp = client.get("/api/v1/billing/portal", headers={"X-Tenant-ID": "tenant-4"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan"]["id"] in ("pro", "enterprise")
    assert data["subscription"]["status"] == "active"

