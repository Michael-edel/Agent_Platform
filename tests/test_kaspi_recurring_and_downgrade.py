"""Kaspi recurring subscription tests: renew, past_due, cancel+downgrade, enforcement."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient

from cyberplat.billing_entitlements import EntitlementService
from cyberplat.billing_service import BillingService


@pytest.fixture
def temp_db():
    import time

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        # Windows: allow some time for DB connections to close
        for attempt in range(10):
            try:
                os.unlink(path)
                break
            except PermissionError:
                time.sleep(0.1)


@pytest.fixture
def app_with_services(temp_db):
    os.environ["PLATFORM_DB_PATH"] = temp_db
    os.environ["DATABASE_URL"] = f"sqlite:///{temp_db}"
    os.environ["BILLING_ENABLED"] = "1"
    os.environ["KASPI_ENABLED"] = "1"
    os.environ["KASPI_WEBHOOK_SECRET"] = "test-secret"
    os.environ["BILLING_PERIOD_DAYS"] = "30"
    os.environ["RENEW_WINDOW_DAYS"] = "2"
    os.environ["SUBSCRIPTION_MAX_FAILED_CHARGES"] = "3"
    os.environ["BILLING_ENFORCEMENT_ENABLED"] = "1"
    os.environ["BILLING_ENFORCEMENT_MODE"] = "block"

    bs = BillingService(db_path=temp_db)
    bs.close()
    entitlement = EntitlementService(db_path=temp_db)
    entitlement.ensure_schema()
    entitlement.seed_default_plans_if_empty()

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
        for p in [
            ("trial", "Trial", None, None, {"document_upload": 20, "invoice_extracted": 10, "page_processed": 50}),
            ("pro", "Pro", 2900, "KZT", {"document_upload": 100, "invoice_extracted": 50, "page_processed": 500}),
            ("enterprise", "Enterprise", 9900, "KZT", {"document_upload": None, "invoice_extracted": None, "page_processed": None}),
        ]:
            pid, name, price, cur, quotas = p
            if not session.query(Plan).filter(Plan.id == pid).first():
                session.add(
                    Plan(
                        id=pid,
                        name=name,
                        description=f"{name} plan",
                        quotas=json.dumps(quotas),
                        price_minor=price,
                        currency=cur,
                        active=True,
                        created_at=datetime.now().isoformat(),
                    )
                )
        session.commit()
    finally:
        session.close()

    from app.main import app
    import app.main as main_module

    app.state.billing_service = BillingService(db_path=temp_db)
    app.state.entitlement_service = entitlement
    app.state.event_service = Mock()
    # upload endpoint reads module-level globals (not app.state)
    main_module.artifact_service = Mock()
    main_module.storage_service = Mock()

    yield app

    try:
        app.state.billing_service.close()
    except Exception:
        pass
    try:
        entitlement.close()
    except Exception:
        pass
    # Dispose SQLAlchemy engine to release SQLite file lock (Windows)
    try:
        from cyberplat.product.infrastructure import database as product_db

        if getattr(product_db, "_engine", None) is not None:
            product_db._engine.dispose()
        product_db._engine = None
        product_db._SessionLocal = None
    except Exception:
        pass


def _upgrade_to_paid_via_webhook(client: TestClient, tenant_id: str, plan_id: str, order_id: str):
    # create checkout row
    with patch("app.main.kaspi_create_checkout") as mock_checkout:
        mock_checkout.return_value = {"checkout_url": "https://kaspi.test/checkout", "external_order_id": order_id}
        r = client.post("/api/v1/billing/checkout/kaspi", headers={"X-Tenant-ID": tenant_id}, json={"plan_id": plan_id})
        assert r.status_code == 200, r.text

    payload = {"id": "evt_paid_1", "type": "payment.paid", "external_order_id": order_id}
    with patch("cyberplat.billing.infrastructure.kaspi_provider.kaspi_verify_signature", return_value=True):
        r = client.post("/api/v1/billing/webhook/kaspi", data=json.dumps(payload).encode("utf-8"), headers={"X-Kaspi-Signature": "sig"})
        assert r.status_code == 200, r.text


def test_upgrade_sets_expires_at_and_active_status(app_with_services):
    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-1", plan_id="pro", order_id="order_1")

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        tp = TenantPlanRepositoryImpl(session=session).get_tenant_plan("tenant-1")
        assert tp["plan_id"] == "pro"
        assert tp["subscription_status"] == "active"
        assert tp["failed_charges"] == 0
        assert tp["expires_at"] is not None
    finally:
        session.close()


def test_recurring_success_extends_expires_at_once(app_with_services):
    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-2", plan_id="pro", order_id="order_2")

    # set kaspi token
    app_with_services.state.billing_service.upsert_kaspi_profile("tenant-2", "tok_2")

    # force expires_at in the past so it's due
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        tp = repo.get_tenant_plan("tenant-2")
        old_expires = (datetime.now() - timedelta(days=1)).isoformat()
        repo.update_subscription_state("tenant-2", expires_at=old_expires, subscription_status="active", failed_charges=0)
    finally:
        session.close()

    with patch("cyberplat.kaspi_client.charge_token", return_value={"status": "success", "external_order_id": "rec_1"}):
        r1 = client.post("/api/v1/billing/cron/charge-kaspi")
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["charged"] == 1

        # second run should be idempotent (not due anymore)
        r2 = client.post("/api/v1/billing/cron/charge-kaspi")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["charged"] == 0


def test_recurring_failure_sets_past_due_and_increments_failed(app_with_services):
    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-3", plan_id="pro", order_id="order_3")
    app_with_services.state.billing_service.upsert_kaspi_profile("tenant-3", "tok_3")

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    # force due
    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        repo.update_subscription_state(
            "tenant-3",
            expires_at=(datetime.now() - timedelta(days=1)).isoformat(),
            subscription_status="active",
            failed_charges=0,
        )
    finally:
        session.close()

    with patch("cyberplat.kaspi_client.charge_token", return_value={"status": "failed"}):
        r = client.post("/api/v1/billing/cron/charge-kaspi")
        assert r.status_code == 200

    session = get_sessionmaker()()
    try:
        tp = TenantPlanRepositoryImpl(session=session).get_tenant_plan("tenant-3")
        assert tp["subscription_status"] == "past_due"
        assert tp["failed_charges"] == 1
    finally:
        session.close()


def test_downgrade_after_max_failures_sets_canceled_and_blocks(app_with_services):
    os.environ["SUBSCRIPTION_MAX_FAILED_CHARGES"] = "2"

    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-4", plan_id="pro", order_id="order_4")
    app_with_services.state.billing_service.upsert_kaspi_profile("tenant-4", "tok_4")

    # force due
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        repo.update_subscription_state(
            "tenant-4",
            expires_at=(datetime.now() - timedelta(days=1)).isoformat(),
            subscription_status="active",
            failed_charges=0,
        )
    finally:
        session.close()

    with patch("cyberplat.kaspi_client.charge_token", return_value={"status": "failed"}):
        client.post("/api/v1/billing/cron/charge-kaspi")
        client.post("/api/v1/billing/cron/charge-kaspi")

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        tp = TenantPlanRepositoryImpl(session=session).get_tenant_plan("tenant-4")
        assert tp["plan_id"] == "trial"
        assert tp["subscription_status"] == "canceled"
        assert tp["failed_charges"] >= 2
    finally:
        session.close()

    # Enforcement: canceled blocks paid operation (upload)
    files = {"file": ("t.pdf", b"%PDF-1.4\n%", "application/pdf")}
    r = client.post("/documents/upload", headers={"X-Tenant-ID": "tenant-4"}, files=files)
    assert r.status_code == 402
    assert r.json()["detail"]["subscription_status"] == "canceled"


def test_enforcement_blocks_when_past_due(app_with_services):
    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-5", plan_id="pro", order_id="order_5")

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        tp = repo.get_tenant_plan("tenant-5")
        repo.update_subscription_state("tenant-5", expires_at=tp["expires_at"], subscription_status="past_due", failed_charges=1)
    finally:
        session.close()

    files = {"file": ("t.pdf", b"%PDF-1.4\n%", "application/pdf")}
    r = client.post("/documents/upload", headers={"X-Tenant-ID": "tenant-5"}, files=files)
    assert r.status_code == 402
    assert r.json()["detail"]["subscription_status"] == "past_due"


def test_webhooks_emitted_for_renew_past_due_canceled(app_with_services):
    os.environ["SUBSCRIPTION_MAX_FAILED_CHARGES"] = "1"

    client = TestClient(app_with_services)
    _upgrade_to_paid_via_webhook(client, tenant_id="tenant-6", plan_id="pro", order_id="order_6")
    app_with_services.state.billing_service.upsert_kaspi_profile("tenant-6", "tok_6")

    # force due for renew
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl

    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        repo.update_subscription_state(
            "tenant-6",
            expires_at=(datetime.now() - timedelta(days=1)).isoformat(),
            subscription_status="active",
            failed_charges=0,
        )
    finally:
        session.close()

    # success renew
    with patch("cyberplat.kaspi_client.charge_token", return_value={"status": "success", "external_order_id": "rec_ok"}):
        client.post("/api/v1/billing/cron/charge-kaspi")

    # force due again + fail -> canceled (max_failed=1)
    session = get_sessionmaker()()
    try:
        repo = TenantPlanRepositoryImpl(session=session)
        repo.update_subscription_state(
            "tenant-6",
            expires_at=(datetime.now() - timedelta(days=1)).isoformat(),
            subscription_status="active",
            failed_charges=0,
        )
    finally:
        session.close()

    with patch("cyberplat.kaspi_client.charge_token", return_value={"status": "failed"}):
        client.post("/api/v1/billing/cron/charge-kaspi")

    emitted = [c.kwargs.get("event_type") for c in app_with_services.state.event_service.emit.call_args_list]
    assert "billing.subscription.renewed" in emitted
    assert "billing.subscription.canceled" in emitted or "billing.subscription.past_due" in emitted

