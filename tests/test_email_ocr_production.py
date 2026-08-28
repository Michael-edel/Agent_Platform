"""Тесты для production-grade features email OCR (admin auth, recovery, concurrency)."""

import pytest
import uuid
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService
from cyberplat.product.infrastructure.repositories_sqlalchemy import EmailOcrJobRepositoryImpl
from cyberplat.product.infrastructure.idempotency import (
    compute_email_ocr_idempotency_key,
    compute_attachment_sha256
)
from app.api.admin_auth import admin_auth
from app.api.ingest import router as ingest_router


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
    from cyberplat.event_service import EventService
    event_svc = EventService(db_path=":memory:", artifact_service=artifact_service)
    artifact_service.event_service = event_svc
    return event_svc


@pytest.fixture
def email_ocr_job_repo(db_session):
    """Создать EmailOcrJobRepository для тестов."""
    return EmailOcrJobRepositoryImpl(session=db_session)


@pytest.fixture
def test_app():
    """Создать тестовое FastAPI приложение."""
    app = FastAPI()
    app.include_router(ingest_router, prefix="/api/v1")
    return app


def create_test_pdf_bytes() -> bytes:
    """Создать тестовый PDF bytes."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Root 1 0 R >>\nstartxref\n20\n%%EOF"


class TestAdminAuth:
    """Тесты admin authentication."""
    
    def test_dispatch_requires_admin_key_when_set(self, test_app):
        """Тест: dispatch требует admin key когда ADMIN_API_KEY задан."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
            client = TestClient(test_app)
            
            # Без заголовка
            response = client.post("/api/v1/ingest/email/ocr/dispatch?limit=10")
            assert response.status_code == 403
            assert "X-Admin-Key" in response.json()["detail"]
            
            # С неверным ключом
            response = client.post(
                "/api/v1/ingest/email/ocr/dispatch?limit=10",
                headers={"X-Admin-Key": "wrong-key"}
            )
            assert response.status_code == 403
            assert "Invalid" in response.json()["detail"]
    
    def test_dispatch_allows_with_valid_key(self, test_app):
        """Тест: dispatch разрешает доступ с валидным ключом."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
            client = TestClient(test_app)
            
            # Мокаем зависимости endpoint
            with patch("app.api.ingest.get_sessionmaker") as mock_session, \
                 patch("app.api.ingest.ArtifactService") as mock_artifact, \
                 patch("app.api.ingest.EventService") as mock_event, \
                 patch("app.api.ingest.AgentRegistry") as mock_registry:
                
                # С валидным ключом (endpoint должен пройти auth, но упадёт на зависимостях)
                response = client.post(
                    "/api/v1/ingest/email/ocr/dispatch?limit=10",
                    headers={"X-Admin-Key": "test-secret-key"}
                )
                # Должен пройти auth (не 403), но может быть 500 из-за моков
                assert response.status_code != 403
    
    def test_dispatch_allows_without_key_in_dev_mode(self, test_app):
        """Тест: dispatch разрешает доступ без ключа в dev режиме (ADMIN_API_KEY не задан)."""
        # Удаляем ADMIN_API_KEY если был
        with patch.dict(os.environ, {}, clear=True):
            # Убеждаемся что ADMIN_API_KEY не задан
            if "ADMIN_API_KEY" in os.environ:
                del os.environ["ADMIN_API_KEY"]
            
            client = TestClient(test_app)
            
            # Мокаем зависимости
            with patch("app.api.ingest.get_sessionmaker") as mock_session, \
                 patch("app.api.ingest.ArtifactService") as mock_artifact, \
                 patch("app.api.ingest.EventService") as mock_event, \
                 patch("app.api.ingest.AgentRegistry") as mock_registry:
                
                # Без ключа (dev режим)
                response = client.post("/api/v1/ingest/email/ocr/dispatch?limit=10")
                # Должен пройти auth (не 403), но может быть 500 из-за моков
                assert response.status_code != 403


class TestEmailIngestAuth:
    """Email ingestion must fail closed until its webhook secret is configured."""

    def test_ingest_rejects_missing_or_invalid_webhook_key(self, test_app):
        with patch.dict(os.environ, {"EMAIL_INGEST_API_KEY": "email-secret"}):
            client = TestClient(test_app)

            missing = client.post("/api/v1/ingest/email", json={})
            assert missing.status_code == 403

            invalid = client.post(
                "/api/v1/ingest/email",
                headers={"X-Email-Ingest-Key": "wrong-key"},
                json={},
            )
            assert invalid.status_code == 403

    def test_ingest_allows_valid_webhook_key_past_auth(self, test_app):
        with patch.dict(os.environ, {"EMAIL_INGEST_API_KEY": "email-secret"}):
            client = TestClient(test_app)
            response = client.post(
                "/api/v1/ingest/email",
                headers={"X-Email-Ingest-Key": "email-secret"},
                json={},
            )
            assert response.status_code != 403


class TestRecoveryStuckJobs:
    """Тесты recovery stuck processing jobs."""
    
    def test_stuck_processing_job_is_recovered(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service
    ):
        """Тест: залипший processing job восстанавливается."""
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
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes),
            attachment_content_sha256=attachment_sha256
        )
        
        job_id = email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=5,
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        # Устанавливаем job в processing и старый updated_at (симуляция залипшего job)
        from cyberplat.product.infrastructure.models import EmailOcrJob
        job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        job_obj.status = "processing"
        old_updated_at = (datetime.now() - timedelta(seconds=1000)).isoformat()  # 1000 секунд назад
        job_obj.updated_at = old_updated_at
        email_ocr_job_repo.session.commit()
        
        # Проверяем, что job залипший (timeout=900 секунд)
        stuck_jobs = email_ocr_job_repo.get_stuck_processing_jobs(timeout_seconds=900)
        assert len(stuck_jobs) == 1
        assert stuck_jobs[0]["id"] == job_id
        
        # Восстанавливаем через use case
        from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase
        
        mock_doc_agent = Mock()
        mock_doc_agent.run = AsyncMock()
        
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=Mock(),
            artifact_service=artifact_service,
            doc_agent=mock_doc_agent,
            event_service=event_service,
            processing_timeout_seconds=900
        )
        
        # Вызываем recovery
        recovered = use_case._recover_stuck_jobs()
        assert recovered == 1
        
        # Проверяем, что job теперь failed
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "failed"
        assert job["attempts"] == 1
        assert "timeout" in job["last_error"].lower()
        assert job["next_run_at"] is not None
    
    def test_stuck_job_goes_dead_after_max_retries(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service
    ):
        """Тест: залипший job становится dead после max_retries."""
        tenant_id = "tenant-1"
        
        document_artifact_id = artifact_service.create_artifact(
            kind="document",
            source="email",
            data={"filename": "invoice.pdf"},
            tenant_id=tenant_id
        )
        
        pdf_bytes = create_test_pdf_bytes()
        attachment_sha256 = compute_attachment_sha256(pdf_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
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
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=2,  # Только 2 попытки
            email_from="sender@example.com",
            email_to=f"invoices+{tenant_id}@yourapp.ai",
            email_subject="Invoice",
            attachment_filename="invoice.pdf",
            attachment_size=len(pdf_bytes)
        )
        
        # Устанавливаем job в processing с attempts=2 (последняя попытка)
        from cyberplat.product.infrastructure.models import EmailOcrJob
        job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        job_obj.status = "processing"
        job_obj.attempts = 2  # Уже 2 попытки (max_retries=2)
        old_updated_at = (datetime.now() - timedelta(seconds=1000)).isoformat()
        job_obj.updated_at = old_updated_at
        email_ocr_job_repo.session.commit()
        
        # Восстанавливаем
        from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase
        
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=Mock(),
            artifact_service=artifact_service,
            doc_agent=Mock(),
            event_service=event_service,
            processing_timeout_seconds=900
        )
        
        recovered = use_case._recover_stuck_jobs()
        assert recovered == 1
        
        # Проверяем, что job стал dead
        job = email_ocr_job_repo.get_job(job_id)
        assert job["status"] == "dead"
        assert job["attempts"] == 3  # attempts увеличился до 3 (превышен max_retries=2)


class TestConcurrencyLimit:
    """Тесты concurrency limit."""
    
    def test_dispatch_respects_concurrency_limit(
        self,
        email_ocr_job_repo,
        artifact_service,
        event_service
    ):
        """Тест: dispatch соблюдает concurrency limit."""
        tenant_id = "tenant-1"
        
        # Создаём несколько jobs в processing (симуляция уже обрабатывающихся)
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
            
            # Устанавливаем в processing
            from cyberplat.product.infrastructure.models import EmailOcrJob
            job_obj = email_ocr_job_repo.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
            job_obj.status = "processing"
            email_ocr_job_repo.session.commit()
        
        # Проверяем количество processing jobs
        processing_count = email_ocr_job_repo.count_processing_jobs()
        assert processing_count == 3
        
        # Создаём use case с max_concurrent_jobs=3
        from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase
        
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=Mock(),
            artifact_service=artifact_service,
            doc_agent=Mock(),
            event_service=event_service,
            max_concurrent_jobs=3
        )
        
        # Выполняем dispatch
        result = use_case.execute(limit=10)
        
        # Должен вернуть skipped_due_to_limit=0, но processed=0 (нет доступных слотов)
        assert result["processed"] == 0
        assert result["skipped_due_to_limit"] == 0  # В текущей реализации это не используется, но поле есть
