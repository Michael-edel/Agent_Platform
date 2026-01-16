import os
import time
from pathlib import Path

import httpx
import pytest


def _repo_root() -> Path:
    # tests/smoke/test_demo_e2e.py -> repo root
    return Path(__file__).resolve().parents[2]


def _demo_pdf_path() -> Path:
    raw = os.getenv("E2E_DEMO_PDF_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _repo_root() / "demo" / "demo-invoice.pdf"


def _base_url() -> str:
    return os.getenv("E2E_BASE_URL", "http://localhost:8000").strip().rstrip("/")


def test_demo_smoke_e2e():
    if os.getenv("E2E_SMOKE_ENABLED", "0").strip() != "1":
        pytest.skip("E2E smoke test disabled (set E2E_SMOKE_ENABLED=1 to run)")

    base_url = _base_url()
    tenant_id = os.getenv("E2E_TENANT_ID", "demo-tenant").strip()
    pdf_path = _demo_pdf_path()
    assert pdf_path.exists(), f"demo PDF not found: {pdf_path}"

    headers = {"X-Tenant-ID": tenant_id}

    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        # /health (wait a bit on cold start)
        last_err = None
        for _ in range(30):
            try:
                r = client.get("/health")
                if r.status_code == 200:
                    break
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            time.sleep(1)
        else:
            raise AssertionError(f"/health did not become ready: {last_err}")

        # upload demo pdf
        with pdf_path.open("rb") as f:
            files = {"file": (pdf_path.name, f, "application/pdf")}
            r = client.post("/documents/upload", headers=headers, files=files)
        assert 200 <= r.status_code < 300, r.text
        payload = r.json()
        document_id = payload.get("artifact_id") or payload.get("document_id") or payload.get("id")
        assert document_id, payload

        # optional: document detail should be visible
        r = client.get(f"/api/v1/documents/{document_id}", headers=headers)
        assert r.status_code in {200, 404}, r.text

        # create payment order (contract)
        r = client.post(
            "/api/v1/payments/orders",
            headers=headers,
            json={
                "amount": 1000,
                "beneficiary_name": "Demo Supplier",
                "beneficiary_account_iban": "KZ000000000000000000",
                "purpose": "Demo payment",
                "created_by_role": "accountant",
                "currency": "KZT",
            },
        )
        assert r.status_code in {200, 201}, r.text
        created = r.json()
        payment_id = created.get("payment_id")
        assert payment_id, created

        # best-effort: submit
        r = client.post(f"/api/v1/payments/orders/{payment_id}/submit", headers=headers)
        if r.status_code not in {404, 405}:
            assert 200 <= r.status_code < 300, r.text

        # best-effort: approve/reject
        approve_path = f"/api/v1/payments/orders/{payment_id}/approve"
        r = client.post(approve_path, headers=headers, json={"role": "director", "comment": "smoke"})
        if r.status_code in {404, 405}:
            # Resilient mode: endpoint may be absent.
            return
        if r.status_code == 400 and "Нет шагов согласования" in r.text:
            # Auto-approve path: policy may create zero approval steps.
            return
        assert 200 <= r.status_code < 300, r.text

