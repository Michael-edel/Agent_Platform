import os
import tempfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService
from cyberplat.reconciliation.reconciliation_service import ReconciliationService
from cyberplat.artifact_service import ArtifactService


@pytest.fixture
def temp_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    try:
        os.unlink(db_path)
    except Exception:
        pass


def _override_services(*, db_path: str):
    payment_service = PaymentService(db_path=db_path)
    case_service = CaseService(db_path=db_path)
    reconciliation_service = ReconciliationService(db_path=db_path)
    artifact_service = ArtifactService(db_path=db_path)

    from app.api.payments import get_payment_service as get_payment_service_dep
    from app.api.payments import get_case_service as get_case_service_dep
    from app.api.reconciliation import get_reconciliation_service as get_reconciliation_service_dep
    from app.api.reconciliation import get_payment_service as get_reconciliation_payment_service_dep
    from app.api.reconciliation import get_artifact_service as get_artifact_service_dep

    app.dependency_overrides[get_payment_service_dep] = lambda: payment_service
    app.dependency_overrides[get_case_service_dep] = lambda: case_service
    app.dependency_overrides[get_reconciliation_service_dep] = lambda: reconciliation_service
    app.dependency_overrides[get_reconciliation_payment_service_dep] = lambda: payment_service
    app.dependency_overrides[get_artifact_service_dep] = lambda: artifact_service

    return payment_service, artifact_service


def test_manual_reconcile_marks_payment_reconciled_and_timeline_contains_event(temp_db):
    payment_service, _ = _override_services(db_path=temp_db)
    try:
        with TestClient(app) as client:
            # create
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

            # approve
            r = client.post(
                f"/api/v1/payments/orders/{payment_id}/approve",
                headers={"X-Tenant-ID": "tenant-1"},
                json={"role": "director", "comment": "ok"},
            )
            assert r.status_code == 200, r.text

            # manual reconcile
            today = date.today().isoformat()
            r = client.post(
                f"/api/v1/payments/{payment_id}/reconcile/manual",
                headers={"X-Tenant-ID": "tenant-1"},
                json={"paid_at": today, "source": "manual", "note": "проверено"},
            )
            assert r.status_code == 200, r.text

            order = payment_service.get_payment_order(payment_id, tenant_id="tenant-1")
            assert order["status"] == "reconciled"

            # timeline contains reconciled
            r = client.get(f"/api/v1/payments/{payment_id}/timeline", headers={"X-Tenant-ID": "tenant-1"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["current_state"] == "RECONCILED"
            events = [e["event"] for e in data["timeline"]]
            assert "payment.reconciled" in events
    finally:
        app.dependency_overrides.clear()


def test_bank_statement_import_creates_artifacts_and_is_tenant_scoped(temp_db):
    _, artifact_service = _override_services(db_path=temp_db)
    try:
        with TestClient(app) as client:
            csv_body = "date,amount,description\n2026-01-15,1000.00,Payment INV-123\n"
            r = client.post(
                "/api/v1/reconciliation/bank-statements/import",
                headers={"X-Tenant-ID": "tenant-A"},
                files={"file": ("stmt.csv", csv_body.encode("utf-8"), "text/csv")},
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            assert payload["imported_count"] == 1
            assert payload["skipped_count"] == 0

            # artifacts exist for tenant-A
            arts_a = artifact_service.list_artifacts(kind="bank.statement.line", tenant_id="tenant-A")
            assert len(arts_a) == 1

            # tenant-B sees none
            arts_b = artifact_service.list_artifacts(kind="bank.statement.line", tenant_id="tenant-B")
            assert len(arts_b) == 0
    finally:
        app.dependency_overrides.clear()


def test_suggestions_returns_matching_line_by_amount_and_date_window(temp_db):
    payment_service, _ = _override_services(db_path=temp_db)
    try:
        with TestClient(app) as client:
            # create + approve
            r = client.post(
                "/api/v1/payments/orders",
                headers={"X-Tenant-ID": "tenant-1"},
                json={
                    "amount": 1000.0,
                    "beneficiary_name": "Demo Supplier",
                    "beneficiary_account_iban": "KZ000000000000000000",
                    "purpose": "INV-123",
                    "created_by_role": "accountant",
                    "currency": "KZT",
                },
            )
            payment_id = r.json()["payment_id"]
            client.post(
                f"/api/v1/payments/orders/{payment_id}/approve",
                headers={"X-Tenant-ID": "tenant-1"},
                json={"role": "director", "comment": "ok"},
            )

            # import CSV line that matches amount and today date (within window)
            today = date.today().isoformat()
            csv_body = f"date,amount,description\n{today},1000.00,Payment INV-123\n"
            r = client.post(
                "/api/v1/reconciliation/bank-statements/import",
                headers={"X-Tenant-ID": "tenant-1"},
                files={"file": ("stmt.csv", csv_body.encode("utf-8"), "text/csv")},
            )
            assert r.status_code == 201, r.text

            # suggestions
            r = client.get(
                f"/api/v1/reconciliation/suggestions?payment_id={payment_id}&days=3",
                headers={"X-Tenant-ID": "tenant-1"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["payment_id"] == payment_id
            assert len(data["suggestions"]) >= 1
            assert data["suggestions"][0]["amount"] == 1000.0
    finally:
        app.dependency_overrides.clear()

