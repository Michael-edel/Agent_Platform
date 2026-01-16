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
def client_with_overrides(temp_db, monkeypatch):
    # ensure auth is off by default for this fixture unless test overrides
    monkeypatch.setenv("AUTH_ENABLED", "false")

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

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _create_payment(client: TestClient, *, tenant_id: str, headers: dict) -> str:
    r = client.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": tenant_id, **headers},
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
    return r.json()["payment_id"]


def test_auth_off_endpoints_work_as_before(client_with_overrides):
    client = client_with_overrides
    payment_id = _create_payment(client, tenant_id="tenant-1", headers={})
    r = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-1"})
    assert r.status_code == 200


def test_auth_on_without_token_is_401(client_with_overrides, monkeypatch):
    client = client_with_overrides
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("API_TOKEN", "secret-token")

    r = client.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-1", "X-Role": "accountant"},
        json={
            "amount": 1000.0,
            "beneficiary_name": "Demo Supplier",
            "beneficiary_account_iban": "KZ000000000000000000",
            "purpose": "INV-123",
            "created_by_role": "accountant",
            "currency": "KZT",
        },
    )
    assert r.status_code == 401


def test_auth_on_token_ok_missing_role_is_403(client_with_overrides, monkeypatch):
    client = client_with_overrides
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("API_TOKEN", "secret-token")

    r = client.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token"},
        json={
            "amount": 1000.0,
            "beneficiary_name": "Demo Supplier",
            "beneficiary_account_iban": "KZ000000000000000000",
            "purpose": "INV-123",
            "created_by_role": "accountant",
            "currency": "KZT",
        },
    )
    assert r.status_code == 403


def test_rbac_approve_accountant_forbidden_approver_allowed(client_with_overrides, monkeypatch):
    client = client_with_overrides
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("API_TOKEN", "secret-token")

    payment_id = _create_payment(
        client,
        tenant_id="tenant-1",
        headers={"Authorization": "Bearer secret-token", "X-Role": "accountant"},
    )

    # approve with accountant -> forbidden
    r = client.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token", "X-Role": "accountant"},
        json={"role": "director", "comment": "ok"},
    )
    assert r.status_code == 403

    # approve with approver -> ok
    r = client.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token", "X-Role": "approver"},
        json={"role": "director", "comment": "ok"},
    )
    assert r.status_code == 200, r.text


def test_audit_endpoints_content_type_and_tenant_isolation(client_with_overrides):
    client = client_with_overrides
    payment_id = _create_payment(client, tenant_id="tenant-1", headers={})

    # tenant isolation
    r = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-2"})
    assert r.status_code == 404

    # JSON audit
    r = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-1"})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/json")

    # CSV audit
    r = client.get(f"/api/v1/payments/{payment_id}/audit.csv", headers={"X-Tenant-ID": "tenant-1"})
    assert r.status_code == 200
    assert "text/csv" in (r.headers.get("content-type", "") or "")

