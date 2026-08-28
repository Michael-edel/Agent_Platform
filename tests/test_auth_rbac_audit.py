import json
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


def _enable_scoped_auth(monkeypatch, identities=None):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv(
        "API_TOKEN_CONFIG",
        json.dumps(identities or {
            "secret-token": {
                "subject": "pilot-accountant",
                "role": "accountant",
                "tenants": ["tenant-1"],
            },
            "approver-token": {
                "subject": "pilot-approver",
                "role": "approver",
                "tenants": ["tenant-1"],
            },
            "director-token": {
                "subject": "pilot-director",
                "role": "director",
                "tenants": ["tenant-1"],
            },
        }),
    )


def _create_payment(client: TestClient, *, tenant_id: str, headers: dict) -> str:
    response = client.post(
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
    assert response.status_code == 201, response.text
    return response.json()["payment_id"]


def test_auth_off_endpoints_work_as_before(client_with_overrides):
    client = client_with_overrides
    payment_id = _create_payment(client, tenant_id="tenant-1", headers={})
    response = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-1"})
    assert response.status_code == 200


def test_auth_on_without_token_is_401(client_with_overrides, monkeypatch):
    _enable_scoped_auth(monkeypatch)
    response = client_with_overrides.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-1"},
        json={"amount": 1000.0, "beneficiary_name": "Demo Supplier", "beneficiary_account_iban": "KZ000000000000000000", "purpose": "INV-123", "created_by_role": "accountant", "currency": "KZT"},
    )
    assert response.status_code == 401


def test_auth_defaults_to_enabled_and_fails_closed_without_config(client_with_overrides, monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_TOKEN_CONFIG", raising=False)

    response = client_with_overrides.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-1"},
        json={"amount": 1000.0, "beneficiary_name": "Demo Supplier", "beneficiary_account_iban": "KZ000000000000000000", "purpose": "INV-123", "created_by_role": "accountant", "currency": "KZT"},
    )

    assert response.status_code == 500
    assert "API_TOKEN_CONFIG" in response.json()["detail"]


def test_auth_on_ignores_request_role_and_uses_token_role(client_with_overrides, monkeypatch):
    _enable_scoped_auth(monkeypatch)
    payment_id = _create_payment(
        client_with_overrides,
        tenant_id="tenant-1",
        headers={"Authorization": "Bearer secret-token", "X-Role": "approver"},
    )
    response = client_with_overrides.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token", "X-Role": "approver"},
        json={"role": "director", "comment": "ok"},
    )
    assert response.status_code == 403


def test_create_payment_uses_token_role_not_request_body(client_with_overrides, monkeypatch):
    _enable_scoped_auth(monkeypatch)
    response = client_with_overrides.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token"},
        json={"amount": 1000.0, "beneficiary_name": "Demo Supplier", "beneficiary_account_iban": "KZ000000000000000000", "purpose": "INV-123", "created_by_role": "system", "currency": "KZT"},
    )
    assert response.status_code == 201, response.text

    payment_id = response.json()["payment_id"]
    response = client_with_overrides.get(
        f"/api/v1/payments/orders/{payment_id}",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_by_role"] == "accountant"


def test_approval_uses_token_role_and_subject_not_request_body(client_with_overrides, monkeypatch):
    policy = client_with_overrides.put(
        "/api/v1/payments/policy?tenant_id=tenant-1",
        headers={"X-Tenant-ID": "tenant-1"},
        json={"enabled": True, "thresholds": [{"max": 10000, "roles": ["director"]}]},
    )
    assert policy.status_code == 200, policy.text

    _enable_scoped_auth(monkeypatch)
    payment_id = _create_payment(
        client_with_overrides,
        tenant_id="tenant-1",
        headers={"Authorization": "Bearer secret-token"},
    )
    submitted = client_with_overrides.post(
        f"/api/v1/payments/orders/{payment_id}/submit",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer secret-token"},
    )
    assert submitted.status_code == 200, submitted.text

    forged = client_with_overrides.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer approver-token"},
        json={"role": "director", "decided_by": "forged-director", "comment": "ok"},
    )
    assert forged.status_code == 400, forged.text

    approved = client_with_overrides.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer director-token"},
        json={"role": "approver", "decided_by": "forged-approver", "comment": "ok"},
    )
    assert approved.status_code == 200, approved.text


def test_token_cannot_access_another_tenant(client_with_overrides, monkeypatch):
    _enable_scoped_auth(monkeypatch)
    response = client_with_overrides.post(
        "/api/v1/payments/orders",
        headers={"X-Tenant-ID": "tenant-2", "Authorization": "Bearer secret-token"},
        json={"amount": 1000.0, "beneficiary_name": "Demo Supplier", "beneficiary_account_iban": "KZ000000000000000000", "purpose": "INV-123", "created_by_role": "accountant", "currency": "KZT"},
    )
    assert response.status_code == 403


def test_rbac_approve_requires_approver_token(client_with_overrides, monkeypatch):
    _enable_scoped_auth(monkeypatch)
    payment_id = _create_payment(
        client_with_overrides,
        tenant_id="tenant-1",
        headers={"Authorization": "Bearer secret-token"},
    )
    response = client_with_overrides.post(
        f"/api/v1/payments/orders/{payment_id}/approve",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer approver-token"},
        json={"role": "director", "comment": "ok"},
    )
    assert response.status_code == 200, response.text


def test_audit_endpoints_content_type_and_tenant_isolation(client_with_overrides):
    client = client_with_overrides
    payment_id = _create_payment(client, tenant_id="tenant-1", headers={})
    response = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-2"})
    assert response.status_code == 404
    response = client.get(f"/api/v1/payments/{payment_id}/audit", headers={"X-Tenant-ID": "tenant-1"})
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")
    response = client.get(f"/api/v1/payments/{payment_id}/audit.csv", headers={"X-Tenant-ID": "tenant-1"})
    assert response.status_code == 200
    assert "text/csv" in (response.headers.get("content-type", "") or "")
