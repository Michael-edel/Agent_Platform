import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService
from cyberplat.event_service import EventService


@pytest.fixture
def temp_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def services(temp_db):
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    event_service = EventService(db_path=temp_db)

    from app.api.payments import get_payment_service as get_payment_service_dep
    from app.api.payments import get_case_service as get_case_service_dep
    from app.api.integrations import get_payment_service as get_integrations_payment_dep
    from app.api.integrations import get_event_service as get_integrations_event_dep

    app.dependency_overrides[get_payment_service_dep] = lambda: payment_service
    app.dependency_overrides[get_case_service_dep] = lambda: case_service
    app.dependency_overrides[get_integrations_payment_dep] = lambda: payment_service
    app.dependency_overrides[get_integrations_event_dep] = lambda: event_service

    yield payment_service
    app.dependency_overrides.clear()


def _create_and_approve(client: TestClient, *, tenant_id: str) -> str:
    r = client.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "amount": 1000.0,
            "beneficiary_name": "Demo Supplier",
            "beneficiary_account_iban": "KZ000000000000000000",
            "purpose": "INV-123",
            "created_by_role": "accountant",
            "currency": "KZT",
        },
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["payment_id"]

    r = client.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": tenant_id},
        json={"role": "director", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return payment_id


def test_1c_summary_endpoint(services):
    with TestClient(app) as client:
        payment_id = _create_and_approve(client, tenant_id="tenant-1")

        r = client.get(
            f"/api/v1/integrations/1c/payments/{payment_id}/summary",
            headers={"X-Tenant-ID": "tenant-1"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["payment_id"] == payment_id
        assert data["tenant_id"] == "tenant-1"
        assert data["state"] == "APPROVED"
        assert data["amount"] == 1000.0
        assert data["currency"] == "KZT"
        assert "timeline" in data

        # tenant isolation
        r = client.get(
            f"/api/v1/integrations/1c/payments/{payment_id}/summary",
            headers={"X-Tenant-ID": "tenant-2"},
        )
        assert r.status_code == 404


def test_1c_confirm_confirmed_sets_sent_and_timeline(services):
    with TestClient(app) as client:
        payment_id = _create_and_approve(client, tenant_id="tenant-1")

        r = client.post(
            "/api/v1/integrations/1c/payments/confirm",
            headers={"X-Tenant-ID": "tenant-1"},
            json={
                "payment_id": payment_id,
                "external_id": "1c-12345",
                "result": "CONFIRMED",
                "confirmed_at": "2026-01-15",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_state"] == "SENT"

        order = services.get_payment_order(payment_id, tenant_id="tenant-1")
        assert order["status"] == "sent"

        tl = services.get_payment_timeline(tenant_id="tenant-1", payment_id=payment_id)
        assert [e["event"] for e in tl].count("payment.sent") == 1


def test_1c_confirm_rejected_sets_failed_and_timeline(services):
    with TestClient(app) as client:
        payment_id = _create_and_approve(client, tenant_id="tenant-1")

        r = client.post(
            "/api/v1/integrations/1c/payments/confirm",
            headers={"X-Tenant-ID": "tenant-1"},
            json={
                "payment_id": payment_id,
                "external_id": "1c-12345",
                "result": "REJECTED",
                "confirmed_at": "2026-01-15",
                "reason": "Ошибка в данных",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_state"] == "FAILED"

        order = services.get_payment_order(payment_id, tenant_id="tenant-1")
        assert order["status"] == "failed"

        tl = services.get_payment_timeline(tenant_id="tenant-1", payment_id=payment_id)
        assert [e["event"] for e in tl].count("payment.sent_failed") == 1


def test_idempotency_no_duplicate_events(services):
    with TestClient(app) as client:
        payment_id = _create_and_approve(client, tenant_id="tenant-1")

        payload = {
            "payment_id": payment_id,
            "external_id": "1c-12345",
            "result": "CONFIRMED",
            "confirmed_at": "2026-01-15",
        }
        r1 = client.post(
            "/api/v1/integrations/1c/payments/confirm",
            headers={"X-Tenant-ID": "tenant-1"},
            json=payload,
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/api/v1/integrations/1c/payments/confirm",
            headers={"X-Tenant-ID": "tenant-1"},
            json=payload,
        )
        assert r2.status_code == 200

        tl = services.get_payment_timeline(tenant_id="tenant-1", payment_id=payment_id)
        assert [e["event"] for e in tl].count("payment.sent") == 1

