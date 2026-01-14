"""Тесты для Inbox API endpoint."""

import pytest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cyberplat.artifact_service import ArtifactService
from cyberplat.product.infrastructure.repositories_sqlalchemy import EmailOcrJobRepositoryImpl
from cyberplat.product.infrastructure.idempotency import (
    compute_email_ocr_idempotency_key,
    compute_attachment_sha256
)


@pytest.fixture
def db_session():
    """Создать тестовую сессию БД (SQLite in-memory)."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    
    # Создаём таблицы
    from cyberplat.product.infrastructure.models import Base
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def artifact_service():
    """Создать ArtifactService для тестов."""
    return ArtifactService(db_path=":memory:")


@pytest.fixture
def email_ocr_job_repo(db_session):
    """Создать EmailOcrJobRepository для тестов."""
    return EmailOcrJobRepositoryImpl(session=db_session)


def create_test_pdf_bytes() -> bytes:
    """Создать тестовый PDF bytes."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Root 1 0 R >>\nstartxref\n20\n%%EOF"


class TestInboxAPI:
    """Тесты для Inbox API."""
    
    def test_inbox_lists_only_tenant_jobs(
        self,
        email_ocr_job_repo,
        artifact_service
    ):
        """Тест: inbox показывает только jobs текущего tenant."""
        tenant_1 = "tenant-1"
        tenant_2 = "tenant-2"
        
        # Создаём artifacts для разных tenant
        doc_1 = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice1.pdf"},
            tenant_id=tenant_1
        )
        
        doc_2 = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice2.pdf"},
            tenant_id=tenant_2
        )
        
        # Создаём jobs для разных tenant
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        key_1 = compute_email_ocr_idempotency_key(
            tenant_id=tenant_1,
            email_from="sender1@example.com",
            email_to=f"invoices+{tenant_1}@yourapp.ai",
            email_subject="Invoice 1",
            attachment_filename="invoice1.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        key_2 = compute_email_ocr_idempotency_key(
            tenant_id=tenant_2,
            email_from="sender2@example.com",
            email_to=f"invoices+{tenant_2}@yourapp.ai",
            email_subject="Invoice 2",
            attachment_filename="invoice2.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id_1 = email_ocr_job_repo.create_job(
            tenant_id=tenant_1,
            document_artifact_id=doc_1,
            idempotency_key=key_1,
            max_retries=5,
            email_from="sender1@example.com",
            email_to=f"invoices+{tenant_1}@yourapp.ai",
            email_subject="Invoice 1",
            attachment_filename="invoice1.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        job_id_2 = email_ocr_job_repo.create_job(
            tenant_id=tenant_2,
            document_artifact_id=doc_2,
            idempotency_key=key_2,
            max_retries=5,
            email_from="sender2@example.com",
            email_to=f"invoices+{tenant_2}@yourapp.ai",
            email_subject="Invoice 2",
            attachment_filename="invoice2.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        # Получаем inbox для tenant_1
        jobs_1 = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_1,
            limit=50
        )
        
        # Получаем inbox для tenant_2
        jobs_2 = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_2,
            limit=50
        )
        
        # Проверяем изоляцию
        assert len(jobs_1) == 1
        assert jobs_1[0]["id"] == job_id_1
        assert jobs_1[0]["tenant_id"] == tenant_1
        
        assert len(jobs_2) == 1
        assert jobs_2[0]["id"] == job_id_2
        assert jobs_2[0]["tenant_id"] == tenant_2
    
    def test_inbox_contains_document_and_invoice_links(
        self,
        email_ocr_job_repo,
        artifact_service
    ):
        """Тест: inbox содержит ссылки на document и invoice."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        doc_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf"},
            tenant_id=tenant_id
        )
        
        # Создаём invoice artifact
        invoice_id = artifact_service.create_artifact(
            kind="invoice",
            source="ocr",
            data={},
            tenant_id=tenant_id
        )
        
        # Создаём job с invoice_artifact_id
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=doc_id,
            idempotency_key=key,
            max_retries=5,
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        # Отмечаем job как done с invoice
        email_ocr_job_repo.mark_job_done(
            job_id=job_id,
            invoice_artifact_id=invoice_id
        )
        
        # Получаем inbox
        jobs = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_id,
            limit=50
        )
        
        assert len(jobs) == 1
        job = jobs[0]
        
        assert job["document_artifact_id"] == doc_id
        assert job["invoice_artifact_id"] == invoice_id
        assert job["status"] == "done"
    
    def test_inbox_shows_dead_and_failed_status(
        self,
        email_ocr_job_repo,
        artifact_service
    ):
        """Тест: inbox показывает статусы dead и failed."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        doc_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf"},
            tenant_id=tenant_id
        )
        
        # Создаём job
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=doc_id,
            idempotency_key=key,
            max_retries=2,  # Только 2 попытки
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        # Отмечаем job как failed
        next_run_at = datetime.now().isoformat()
        email_ocr_job_repo.mark_job_failed(
            job_id=job_id,
            error_message="OCR failed",
            next_run_at=next_run_at,
            attempts=1
        )
        
        # Получаем inbox
        jobs = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_id,
            limit=50
        )
        
        assert len(jobs) == 1
        job = jobs[0]
        
        assert job["status"] == "failed"
        assert job["attempts"] == 1
        assert job["last_error"] == "OCR failed"
        assert job["next_run_at"] == next_run_at
        
        # Отмечаем job как dead
        email_ocr_job_repo.mark_job_dead(
            job_id=job_id,
            error_message="Max retries exceeded"
        )
        
        # Получаем inbox снова
        jobs = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_id,
            limit=50
        )
        
        assert len(jobs) == 1
        job = jobs[0]
        
        assert job["status"] == "dead"
        assert job["last_error"] == "Max retries exceeded"
    
    def test_inbox_ordering_latest_first(
        self,
        email_ocr_job_repo,
        artifact_service
    ):
        """Тест: inbox сортирует по дате получения (последние первыми)."""
        tenant_id = "tenant-1"
        
        # Создаём несколько jobs с разными датами
        jobs_created = []
        for i in range(3):
            doc_id = artifact_service.create_artifact(
                kind="document",
                source="email",
                data={"filename": f"invoice{i}.pdf"},
                tenant_id=tenant_id
            )
            
            pdf_bytes = create_test_pdf_bytes()
            attachment_sha256 = compute_attachment_sha256(pdf_bytes)
            
            key = compute_email_ocr_idempotency_key(
                tenant_id=tenant_id,
                email_from=f"sender{i}@example.com",
                email_to=f"invoices+{tenant_id}@yourapp.ai",
                email_subject=f"Invoice {i}",
                attachment_filename=f"invoice{i}.pdf",
                attachment_size=len(pdf_bytes),
                attachment_content_sha256=attachment_sha256
            )
            
            job_id = email_ocr_job_repo.create_job(
                tenant_id=tenant_id,
                document_artifact_id=doc_id,
                idempotency_key=key,
                max_retries=5,
                email_from=f"sender{i}@example.com",
                email_to=f"invoices+{tenant_id}@yourapp.ai",
                email_subject=f"Invoice {i}",
                attachment_filename=f"invoice{i}.pdf",
                attachment_size=len(pdf_bytes)
            )
            
            jobs_created.append(job_id)
            
            # Небольшая задержка для разных created_at
            import time
            time.sleep(0.1)
        
        # Получаем inbox
        jobs = email_ocr_job_repo.list_inbox_emails(
            tenant_id=tenant_id,
            limit=50
        )
        
        # Проверяем, что последний созданный job идёт первым
        assert len(jobs) == 3
        assert jobs[0]["id"] == jobs_created[-1]  # Последний созданный
        assert jobs[1]["id"] == jobs_created[-2]
        assert jobs[2]["id"] == jobs_created[-3]
        
        # Проверяем сортировку по created_at DESC
        for i in range(len(jobs) - 1):
            assert jobs[i]["created_at"] >= jobs[i + 1]["created_at"]
