import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService


@pytest.fixture
def temp_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    try:
        os.unlink(db_path)
    except Exception:
        pass


def test_payment_lifecycle_and_timeline_api(temp_db):
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)

    from app.api.payments import get_payment_service, get_case_service

    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service

    try:
        client = TestClient(app)

        # create -> should end in PENDING_APPROVAL
        r = client.post(
            "/api/v1/payments/orders",
            headers={"X-Tenant-ID": "tenant-1"},
            json={
                "amount": 1000,
                "beneficiary_name": "Demo Supplier",
                "beneficiary_account_iban": "KZ000000000000000000",
                "purpose": "Demo payment",
                "created_by_role": "accountant",
                "currency": "KZT",
            },
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["payment_id"]

        order = payment_service.get_payment_order(payment_id, tenant_id="tenant-1")
        assert order["status"] == "pending_approval"

        # approve -> APPROVED
        r = client.post(
            f"/api/v1/payments/orders/{payment_id}/approve",
            headers={"X-Tenant-ID": "tenant-1"},
            json={"role": "director", "comment": "ok"},
        )
        assert r.status_code == 200, r.text

        order = payment_service.get_payment_order(payment_id, tenant_id="tenant-1")
        assert order["status"] == "approved"

        # timeline
        r = client.get(f"/api/v1/payments/{payment_id}/timeline", headers={"X-Tenant-ID": "tenant-1"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["payment_id"] == payment_id
        assert data["current_state"] == "APPROVED"
        events = [e["event"] for e in data["timeline"]]
        assert events[0] == "payment.created"
        assert "payment.state_changed" in events
        assert "payment.approved" in events

        # order by time ASC (best-effort monotonic)
        ats = [e["at"] for e in data["timeline"]]
        assert ats == sorted(ats)
    finally:
        app.dependency_overrides.clear()


def test_timeline_tenant_isolation(temp_db):
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)

    from app.api.payments import get_payment_service, get_case_service

    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service

    try:
        client = TestClient(app)
        r = client.post(
            "/api/v1/payments/orders",
            headers={"X-Tenant-ID": "tenant-A"},
            json={
                "amount": 1000,
                "beneficiary_name": "Demo Supplier",
                "beneficiary_account_iban": "KZ000000000000000000",
                "purpose": "Demo payment",
                "created_by_role": "accountant",
                "currency": "KZT",
            },
        )
        payment_id = r.json()["payment_id"]

        r = client.get(f"/api/v1/payments/{payment_id}/timeline", headers={"X-Tenant-ID": "tenant-B"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()

