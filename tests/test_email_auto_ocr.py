"""Тесты для Email Auto-OCR (идемпотентность, ретраи, tenant isolation)."""

import pytest
import uuid
import base64
import os
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock, AsyncMock, patch

from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService
from cyberplat.storage_service import StorageService
from cyberplat.product.infrastructure.database import get_sessionmaker
from cyberplat.product.infrastructure.repositories_sqlalchemy import (
    ArtifactStateRepositoryImpl,
    EmailOcrJobRepositoryImpl
)
from cyberplat.product.infrastructure.idempotency import (
    compute_email_ocr_idempotency_key,
    compute_attachment_sha256
)
from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase


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
def event_service(artifact_service):
    """Создать EventService для тестов."""
    event_svc = EventService(db_path=":memory:", artifact_service=artifact_service)
    artifact_service.event_service = event_svc
    return event_svc


@pytest.fixture
def storage_service():
    """Создать StorageService для тестов."""
    import tempfile
    temp_dir = tempfile.mkdtemp()
    return StorageService(base_path=temp_dir)


@pytest.fixture
def artifact_state_repo(db_session):
    """Создать ArtifactStateRepository для тестов."""
    return ArtifactStateRepositoryImpl(session=db_session)


@pytest.fixture
def email_ocr_job_repo(db_session):
    """Создать EmailOcrJobRepository для тестов."""
    repo = EmailOcrJobRepositoryImpl(session=db_session)
    # Сохраняем ссылку на session для тестов
    repo.session = db_session
    return repo


@pytest.fixture
def mock_doc_agent():
    """Создать mock DocAgent для тестов."""
    agent = Mock()
    agent.run = AsyncMock(return_value={
        "success": True,
        "artifact_id": str(uuid.uuid4())
    })
    return agent


def create_test_pdf_bytes() -> bytes:
    """Создать тестовый PDF bytes (минимальный валидный PDF)."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Root 1 0 R >>\nstartxref\n20\n%%EOF"


class TestIdempotency:
    """Тесты идемпотентности email OCR jobs."""
    
    def test_idempotency_same_email_attachment_creates_single_job(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service,
        artifact_state_repo
    ):
        """Тест: одинаковый email attachment создаёт только один job."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf", "file_id": "file-123"},
            tenant_id=tenant_id
        )
        
        # Вычисляем idempotency_key
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to="invoices+tenant-1@yourapp.ai",
            email_subject="Invoice #123",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        # Создаём первый job
        job_id_1 = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Пытаемся создать второй job с тем же idempotency_key
        job_id_2 = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Должен вернуться тот же job_id
        assert job_id_1 == job_id_2
        
        # Проверяем, что в БД только один job
        job = email_ocr_job_repo.get_job_by_idempotency_key(idempotency_key)
        assert job is not None
        assert job["id"] == job_id_1
    
    def test_no_duplicate_invoice_on_webhook_retry(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service,
        artifact_state_repo,
        mock_doc_agent
    ):
        """Тест: повторный webhook не создаёт дубликат invoice."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf", "file_id": "file-123"},
            tenant_id=tenant_id
        )
        
        # Вычисляем idempotency_key
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to="invoices+tenant-1@yourapp.ai",
            email_subject="Invoice #123",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        # Создаём job
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Обрабатываем job первый раз
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=artifact_state_repo,
            artifact_service=artifact_service,
            doc_agent=mock_doc_agent,
            event_service=event_service
        )
        
        result1 = use_case.execute(limit=1)
        assert result1["succeeded"] == 1
        
        # Проверяем, что invoice создан
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "done"
        invoice_artifact_id_1 = job["invoice_artifact_id"]
        assert invoice_artifact_id_1 is not None
        
        # Пытаемся создать job снова (симуляция повторного webhook)
        job_id_2 = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Должен вернуться тот же job_id
        assert job_id_2 == job_id
        
        # Job уже done, не должен обрабатываться снова
        result2 = use_case.execute(limit=1)
        assert result2["processed"] == 0  # Нет jobs для обработки
        
        # Проверяем, что invoice не дублируется
        job2 = email_ocr_job_repo.get_job(job_id)
        assert job2["invoice_artifact_id"] == invoice_artifact_id_1


class TestDispatch:
    """Тесты dispatch email OCR jobs."""
    
    def test_dispatch_processes_job_and_creates_invoice(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service,
        artifact_state_repo,
        mock_doc_agent
    ):
        """Тест: dispatch обрабатывает job и создаёт invoice."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf", "file_id": "file-123"},
            tenant_id=tenant_id
        )
        
        # Создаём artifact_state
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=document_artifact_id,
            ui_status="uploaded"
        )
        
        # Создаём job
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to="invoices+tenant-1@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Обрабатываем job
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=artifact_state_repo,
            artifact_service=artifact_service,
            doc_agent=mock_doc_agent,
            event_service=event_service
        )
        
        result = use_case.execute(limit=1)
        
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert result["dead"] == 0
        
        # Проверяем, что job выполнен
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "done"
        assert job["invoice_artifact_id"] is not None
        
        # Проверяем, что states обновлены
        document_state = artifact_state_repo.get_state(
            tenant_id=tenant_id,
            artifact_id=document_artifact_id
        )
        assert document_state["ui_status"] == "extracted"
        
        invoice_state = artifact_state_repo.get_state(
            tenant_id=tenant_id,
            artifact_id=job["invoice_artifact_id"]
        )
        assert invoice_state is not None
        assert invoice_state["ui_status"] == "pending"
        assert invoice_state["source_artifact_id"] == document_artifact_id
    
    def test_dispatch_retry_on_failure_increments_attempts_and_sets_next_run_at(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service,
        artifact_state_repo,
        db_session
    ):
        """Тест: retry при ошибке увеличивает attempts и устанавливает next_run_at."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf", "file_id": "file-123"},
            tenant_id=tenant_id
        )
        
        # Создаём job
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to="invoices+tenant-1@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5
        )
        
        # Создаём mock doc_agent, который бросает исключение первые 2 раза
        call_count = [0]
        
        async def mock_run_failing(context):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError(f"OCR failed (attempt {call_count[0]})")
            return {
                "success": True,
                "artifact_id": str(uuid.uuid4())
            }
        
        mock_doc_agent = Mock()
        mock_doc_agent.run = AsyncMock(side_effect=mock_run_failing)
        
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=artifact_state_repo,
            artifact_service=artifact_service,
            doc_agent=mock_doc_agent,
            event_service=event_service,
            max_retries=5
        )
        
        # Первая попытка (должна упасть)
        result1 = use_case.execute(limit=1)
        assert result1["failed"] == 1
        
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "failed"
        assert job["attempts"] == 1
        assert job["next_run_at"] is not None
        assert job["last_error"] is not None
        
        # Вторая попытка (тоже должна упасть)
        # Симулируем прошествие времени (устанавливаем next_run_at в прошлое)
        from cyberplat.product.infrastructure.models import EmailOcrJob
        job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        job_obj.next_run_at = datetime.now().isoformat()
        email_ocr_job_repo.session.commit()
        
        result2 = use_case.execute(limit=1)
        assert result2["failed"] == 1
        
        job = email_ocr_job_repo.get_job(job_id)
        assert job["attempts"] == 2
        
        # Третья попытка (должна успешно завершиться)
        job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        job_obj.next_run_at = datetime.now().isoformat()
        email_ocr_job_repo.session.commit()
        
        result3 = use_case.execute(limit=1)
        assert result3["succeeded"] == 1
        
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "done"
        assert job["invoice_artifact_id"] is not None
    
    def test_dead_after_max_retries(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service,
        artifact_state_repo,
        db_session
    ):
        """Тест: job становится dead после max_retries."""
        tenant_id = "tenant-1"
        
        # Создаём document artifact
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf", "file_id": "file-123"},
            tenant_id=tenant_id
        )
        
        # Создаём job с max_retries=2
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from="sender@example.com",
            email_to="invoices+tenant-1@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=2  # Только 2 попытки
        )
        
        # Создаём mock doc_agent, который всегда падает
        mock_doc_agent = Mock()
        mock_doc_agent.run = AsyncMock(side_effect=RuntimeError("OCR always fails"))
        
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=artifact_state_repo,
            artifact_service=artifact_service,
            doc_agent=mock_doc_agent,
            event_service=event_service,
            max_retries=2
        )
        
        # Первая попытка
        result1 = use_case.execute(limit=1)
        assert result1["failed"] == 1
        
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "failed"
        assert job["attempts"] == 1
        
        # Вторая попытка (последняя)
        from cyberplat.product.infrastructure.models import EmailOcrJob
        job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        job_obj.next_run_at = datetime.now().isoformat()
        email_ocr_job_repo.session.commit()
        
        result2 = use_case.execute(limit=1)
        # При max_retries=2 вторая ошибка переводит job сразу в dead
        assert result2["dead"] == 1
        
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "dead"
        assert job["attempts"] == 2  # Превышен max_retries=2


class TestTenantIsolation:
    """Тесты tenant isolation для email OCR jobs."""
    
    def test_tenant_isolation_jobs(
        self,
        email_ocr_job_repo,
        artifact_service
    ):
        """Тест: jobs разных tenant изолированы."""
        tenant_1 = "tenant-1"
        tenant_2 = "tenant-2"
        
        doc_1 = str(uuid.uuid4())
        doc_2 = str(uuid.uuid4())
        
        # Создаём artifacts для разных tenant
        artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice1.pdf"},
            tenant_id=tenant_1
        )
        
        artifact_service.create_artifact(
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
            max_retries=5
        )
        
        job_id_2 = email_ocr_job_repo.create_job(
            tenant_id=tenant_2,
            document_artifact_id=doc_2,
            idempotency_key=key_2,
            max_retries=5
        )
        
        # Проверяем, что jobs созданы
        job_1 = email_ocr_job_repo.get_job(job_id_1)
        job_2 = email_ocr_job_repo.get_job(job_id_2)
        
        assert job_1["tenant_id"] == tenant_1
        assert job_2["tenant_id"] == tenant_2
        assert job_1["document_artifact_id"] == doc_1
        assert job_2["document_artifact_id"] == doc_2
        
        # Проверяем, что get_jobs_for_processing возвращает jobs для всех tenant
        # (но обработка должна быть tenant-safe)
        jobs = email_ocr_job_repo.get_jobs_for_processing(limit=10)
        assert len(jobs) >= 2
        
        # Проверяем, что jobs принадлежат правильным tenant
        tenant_1_jobs = [j for j in jobs if j["tenant_id"] == tenant_1]
        tenant_2_jobs = [j for j in jobs if j["tenant_id"] == tenant_2]
        
        assert len(tenant_1_jobs) >= 1
        assert len(tenant_2_jobs) >= 1
