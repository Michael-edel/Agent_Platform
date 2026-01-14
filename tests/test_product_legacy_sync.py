"""Тесты на совместимость legacy upload/ocr с Product/UI API.

Цель: зафиксировать регрессию, когда /documents/upload возвращает artifact_id,
но Product API не видит документ (list пустой / detail 404), а export не возвращает file_id.
"""

from __future__ import annotations

from typing import Dict, Any


def _fake_pdf_bytes() -> bytes:
    # Минимальный "похожий на PDF" payload (достаточно для unit-теста upload).
    return b"%PDF-1.4\n%EOF\n"


def test_documents_list_contains_uploaded_document_after_legacy_upload() -> None:
    from fastapi.testclient import TestClient

    # Импортируем app только внутри теста, чтобы autouse фикстура успела выставить env.
    from app.main import app

    tenant_id = "tenant-1"

    with TestClient(app) as client:
        r = client.post(
            "/documents/upload",
            headers={"X-Tenant-ID": tenant_id},
            files={"file": ("test.pdf", _fake_pdf_bytes(), "application/pdf")},
        )
        assert r.status_code == 200, r.text
        payload: Dict[str, Any] = r.json()
        doc_id = payload["artifact_id"]
        assert doc_id

        r2 = client.get("/api/v1/documents?limit=20", headers={"X-Tenant-ID": tenant_id})
        assert r2.status_code == 200, r2.text
        items = (r2.json() or {}).get("items") or []
        assert any(i.get("id") == doc_id for i in items), "Документ из legacy upload не появился в Product списке"


def test_document_detail_exists_for_source_artifact_id_from_invoice() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    tenant_id = "tenant-1"

    with TestClient(app) as client:
        # 1) legacy upload → document artifact_id
        r = client.post(
            "/documents/upload",
            headers={"X-Tenant-ID": tenant_id},
            files={"file": ("test.pdf", _fake_pdf_bytes(), "application/pdf")},
        )
        assert r.status_code == 200, r.text
        doc_id = r.json()["artifact_id"]

        # 2) создаём invoice артефакт (legacy sqlite) и state (product DB) со связью на doc_id
        from app.main import artifact_service
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.repositories_sqlalchemy import ArtifactStateRepositoryImpl

        invoice_id = artifact_service.create_artifact(
            kind="invoice",
            source="doc_agent",
            data={"invoice_number": "T-1"},
            tenant_id=tenant_id,
        )

        SessionLocal = get_sessionmaker()
        session = SessionLocal()
        try:
            repo = ArtifactStateRepositoryImpl(session=session)
            repo.create_or_update_state(
                tenant_id=tenant_id,
                artifact_id=invoice_id,
                ui_status="pending",
                source_artifact_id=doc_id,
            )
        finally:
            session.close()

        # 3) invoice detail → source_artifact_id должен ссылаться на существующий document
        r_inv = client.get(f"/api/v1/invoices/{invoice_id}", headers={"X-Tenant-ID": tenant_id})
        assert r_inv.status_code == 200, r_inv.text
        inv = r_inv.json()
        assert (inv.get("state") or {}).get("source_artifact_id") == doc_id

        r_doc = client.get(f"/api/v1/documents/{doc_id}", headers={"X-Tenant-ID": tenant_id})
        assert r_doc.status_code == 200, r_doc.text


def test_export_returns_file_id_and_file_download_works() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    tenant_id = "tenant-1"

    with TestClient(app) as client:
        # 1) invoice артефакт в legacy sqlite
        from app.main import artifact_service
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.repositories_sqlalchemy import ArtifactStateRepositoryImpl
        from cyberplat.product.infrastructure.models import Export

        invoice_id = artifact_service.create_artifact(
            kind="invoice",
            source="doc_agent",
            data={"invoice_number": "E-1"},
            tenant_id=tenant_id,
        )

        # 2) state в product DB обязателен для export use case
        SessionLocal = get_sessionmaker()
        session = SessionLocal()
        try:
            repo = ArtifactStateRepositoryImpl(session=session)
            repo.create_or_update_state(tenant_id=tenant_id, artifact_id=invoice_id, ui_status="draft")
        finally:
            session.close()

        # 3) export должен вернуть file_id + download_url
        r = client.post(
            f"/api/v1/invoices/{invoice_id}/export",
            headers={"X-Tenant-ID": tenant_id},
            json={"export_type": "json"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True, body
        export_id = body.get("export_id")
        assert export_id
        file_id = body.get("file_id")
        assert file_id, "export должен возвращать file_id, чтобы файл можно было скачать"
        assert body.get("download_url") == f"/files/{file_id}"

        # 4) запись в exports должна содержать file_id
        session2 = SessionLocal()
        try:
            row = session2.query(Export).filter(Export.id == export_id, Export.tenant_id == tenant_id).first()
            assert row is not None
            assert row.file_id == file_id
        finally:
            session2.close()

        # 5) /files/{file_id} должен отдавать файл (через storage_service)
        r_file = client.get(f"/files/{file_id}", headers={"X-Tenant-ID": tenant_id})
        assert r_file.status_code == 200, r_file.text
        assert r_file.content, "скачанный файл пустой"

