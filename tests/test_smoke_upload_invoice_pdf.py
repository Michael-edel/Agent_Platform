from __future__ import annotations

from typing import Any, Dict


def _fake_pdf_bytes() -> bytes:
    # Минимальный "похожий на PDF" payload (достаточно для smoke-теста upload).
    return b"%PDF-1.4\n%EOF\n"


def test_smoke_upload_invoice_pdf_creates_document_record(tmp_path, monkeypatch) -> None:
    # SQLite-only: изолированная БД
    db_path = tmp_path / "smoke_upload.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Без внешних сервисов / OCR
    monkeypatch.setenv("OPENAI_API_KEY", "")

    # Сбрасываем кэш engine, чтобы он пересоздался с новыми env
    import cyberplat.product.infrastructure.database as db_module

    db_module._engine = None
    db_module._engine_url = None
    db_module._SessionLocal = None

    from fastapi.testclient import TestClient

    from app.main import app

    tenant_id = "tenant-smoke"
    pdf_bytes = _fake_pdf_bytes()

    with TestClient(app) as client:
        r = client.post(
            "/documents/upload",
            headers={"X-Tenant-ID": tenant_id},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert r.status_code in (200, 201), r.text
        payload: Dict[str, Any] = r.json()
        doc_id = payload.get("artifact_id") or payload.get("document_id") or payload.get("id")
        assert doc_id, payload

        r_list = client.get("/api/v1/documents?limit=20", headers={"X-Tenant-ID": tenant_id})
        assert r_list.status_code == 200, r_list.text
        items = (r_list.json() or {}).get("items") or []
        assert any(i.get("id") == doc_id for i in items), "Загруженный документ не появился в списке документов"

        r_detail = client.get(f"/api/v1/documents/{doc_id}", headers={"X-Tenant-ID": tenant_id})
        assert r_detail.status_code == 200, r_detail.text
        detail = r_detail.json() or {}
        assert detail.get("id") == doc_id

