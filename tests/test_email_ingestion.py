"""Тесты для Email Ingestion."""

import pytest
import uuid
import base64
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService
from cyberplat.storage_service import StorageService
from cyberplat.product.infrastructure.database import get_sessionmaker
from cyberplat.product.infrastructure.repositories_sqlalchemy import ArtifactStateRepositoryImpl
from cyberplat.product.infrastructure.email_parser import EmailPayloadParser
from cyberplat.product.application.email_ingest_use_case import EmailIngestUseCase


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
def email_parser():
    """Создать EmailPayloadParser для тестов."""
    return EmailPayloadParser()


@pytest.fixture
def email_ingest_use_case(artifact_service, event_service, storage_service, artifact_state_repo, email_parser):
    """Создать EmailIngestUseCase для тестов."""
    return EmailIngestUseCase(
        artifact_service=artifact_service,
        event_service=event_service,
        storage_service=storage_service,
        artifact_state_repo=artifact_state_repo,
        email_parser=email_parser,
        max_attachment_size_mb=10
    )


def create_test_pdf_base64() -> str:
    """Создать тестовый PDF в base64 (минимальный валидный PDF)."""
    # Минимальный валидный PDF
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Root 1 0 R >>\nstartxref\n20\n%%EOF"
    return base64.b64encode(pdf_bytes).decode('utf-8')


class TestEmailIngestSuccess:
    """Тесты успешного email ingestion."""
    
    def test_email_ingest_success_single_pdf(
        self,
        email_ingest_use_case,
        artifact_service,
        event_service
    ):
        """Тест: успешная обработка email с одним PDF вложением."""
        pdf_content = create_test_pdf_base64()
        
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-1@yourapp.ai",
            "subject": "Invoice #123",
            "attachments": [
                {
                    "filename": "invoice.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is True
        assert result["tenant_id"] == "tenant-1"
        assert result["processed_attachments"] == 1
        assert len(result["created_artifacts"]) == 1
        assert len(result["errors"]) == 0
        
        # Проверяем, что artifact создан
        artifact_id = result["created_artifacts"][0]
        artifact = artifact_service.get_artifact(artifact_id)
        assert artifact is not None
        assert artifact["kind"] == "document"
        assert artifact["source"] == "email"
        assert artifact["tenant_id"] == "tenant-1"
        assert artifact["data"]["filename"] == "invoice.pdf"
        assert artifact["data"]["email_from"] == "sender@example.com"
    
    def test_email_ingest_multiple_pdfs(
        self,
        email_ingest_use_case,
        artifact_service
    ):
        """Тест: обработка email с несколькими PDF вложениями."""
        pdf_content = create_test_pdf_base64()
        
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-2@yourapp.ai",
            "subject": "Multiple Invoices",
            "attachments": [
                {
                    "filename": "invoice1.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                },
                {
                    "filename": "invoice2.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is True
        assert result["tenant_id"] == "tenant-2"
        assert result["processed_attachments"] == 2
        assert len(result["created_artifacts"]) == 2
        assert len(result["errors"]) == 0
        
        # Проверяем, что оба artifact созданы
        for artifact_id in result["created_artifacts"]:
            artifact = artifact_service.get_artifact(artifact_id)
            assert artifact is not None
            assert artifact["kind"] == "document"
            assert artifact["source"] == "email"
    
    def test_email_ingest_creates_artifact_and_state(
        self,
        email_ingest_use_case,
        artifact_service,
        artifact_state_repo
    ):
        """Тест: создание artifact и artifact_state."""
        pdf_content = create_test_pdf_base64()
        
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-3@yourapp.ai",
            "subject": "Test Invoice",
            "attachments": [
                {
                    "filename": "test.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is True
        artifact_id = result["created_artifacts"][0]
        
        # Проверяем artifact
        artifact = artifact_service.get_artifact(artifact_id)
        assert artifact is not None
        
        # Проверяем artifact_state
        state = artifact_state_repo.get_state(
            tenant_id="tenant-3",
            artifact_id=artifact_id
        )
        assert state is not None
        assert state["ui_status"] == "uploaded"


class TestEmailIngestValidation:
    """Тесты валидации email ingestion."""
    
    def test_email_ingest_invalid_tenant(
        self,
        email_ingest_use_case,
        event_service
    ):
        """Тест: email с невалидным tenant_id."""
        pdf_content = create_test_pdf_base64()
        
        payload = {
            "from": "sender@example.com",
            "to": "invoices@yourapp.ai",  # Нет tenant_id
            "subject": "Invoice",
            "attachments": [
                {
                    "filename": "invoice.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is False
        assert result["tenant_id"] is None
        assert len(result["created_artifacts"]) == 0
        assert len(result["errors"]) > 0
        
        # Проверяем, что событие email.ingest.failed эмитировано
        # (проверяем через event_service напрямую)
        conn = event_service._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM events
            WHERE event_type = 'email.ingest.failed'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        event = cur.fetchone()
        conn.close()
        
        assert event is not None
    
    def test_email_ingest_non_pdf_attachment(
        self,
        email_ingest_use_case
    ):
        """Тест: email с не-PDF вложением."""
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-4@yourapp.ai",
            "subject": "Document",
            "attachments": [
                {
                    "filename": "document.txt",
                    "content_type": "text/plain",
                    "content": base64.b64encode(b"Hello, World!").decode('utf-8'),
                    "size": 13
                }
            ]
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is False
        assert result["tenant_id"] == "tenant-4"
        assert len(result["created_artifacts"]) == 0
        assert len(result["errors"]) > 0
        assert any("PDF" in error for error in result["errors"])
    
    def test_email_ingest_no_attachments(
        self,
        email_ingest_use_case
    ):
        """Тест: email без вложений."""
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-5@yourapp.ai",
            "subject": "No attachments",
            "attachments": []
        }
        
        result = email_ingest_use_case.execute(payload)
        
        assert result["success"] is False
        assert result["tenant_id"] == "tenant-5"
        assert len(result["created_artifacts"]) == 0
        assert len(result["errors"]) > 0


class TestTenantIsolation:
    """Тесты tenant isolation для email ingestion."""
    
    def test_tenant_isolation_email_ingest(
        self,
        email_ingest_use_case,
        artifact_service,
        artifact_state_repo
    ):
        """Тест: tenant isolation при email ingestion."""
        pdf_content = create_test_pdf_base64()
        
        # Создаём artifacts для разных tenant
        payload1 = {
            "from": "sender1@example.com",
            "to": "invoices+tenant-a@yourapp.ai",
            "subject": "Invoice A",
            "attachments": [
                {
                    "filename": "invoice_a.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        payload2 = {
            "from": "sender2@example.com",
            "to": "invoices+tenant-b@yourapp.ai",
            "subject": "Invoice B",
            "attachments": [
                {
                    "filename": "invoice_b.pdf",
                    "content_type": "application/pdf",
                    "content": pdf_content,
                    "size": len(base64.b64decode(pdf_content))
                }
            ]
        }
        
        result1 = email_ingest_use_case.execute(payload1)
        result2 = email_ingest_use_case.execute(payload2)
        
        assert result1["success"] is True
        assert result2["success"] is True
        assert result1["tenant_id"] == "tenant-a"
        assert result2["tenant_id"] == "tenant-b"
        
        artifact_id_a = result1["created_artifacts"][0]
        artifact_id_b = result2["created_artifacts"][0]
        
        # Проверяем, что artifacts принадлежат правильным tenant
        artifact_a = artifact_service.get_artifact(artifact_id_a)
        artifact_b = artifact_service.get_artifact(artifact_id_b)
        
        assert artifact_a["tenant_id"] == "tenant-a"
        assert artifact_b["tenant_id"] == "tenant-b"
        
        # Проверяем, что tenant-a не может получить state tenant-b
        state_a = artifact_state_repo.get_state(
            tenant_id="tenant-a",
            artifact_id=artifact_id_a
        )
        assert state_a is not None
        
        state_b_from_a = artifact_state_repo.get_state(
            tenant_id="tenant-a",
            artifact_id=artifact_id_b
        )
        assert state_b_from_a is None  # tenant-a не видит artifact tenant-b


class TestEmailParser:
    """Тесты EmailPayloadParser."""
    
    def test_parse_sendgrid_format(self, email_parser):
        """Тест: парсинг SendGrid формата."""
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-1@yourapp.ai",
            "subject": "Invoice",
            "attachments": [
                {
                    "filename": "invoice.pdf",
                    "type": "application/pdf",
                    "content": "base64_content",
                    "size": 12345
                }
            ]
        }
        
        parsed = email_parser.parse(payload)
        
        assert parsed["from"] == "sender@example.com"
        assert parsed["to"] == "invoices+tenant-1@yourapp.ai"
        assert parsed["subject"] == "Invoice"
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["filename"] == "invoice.pdf"
    
    def test_parse_mailgun_format(self, email_parser):
        """Тест: парсинг Mailgun формата."""
        payload = {
            "sender": "sender@example.com",
            "recipient": "invoices+tenant-1@yourapp.ai",
            "subject": "Invoice",
            "attachments": [
                {
                    "filename": "invoice.pdf",
                    "content-type": "application/pdf",
                    "body": "base64_content",
                    "size": 12345
                }
            ]
        }
        
        parsed = email_parser.parse(payload)
        
        assert parsed["from"] == "sender@example.com"
        assert parsed["to"] == "invoices+tenant-1@yourapp.ai"
        assert parsed["subject"] == "Invoice"
        assert len(parsed["attachments"]) == 1
    
    def test_parse_generic_format(self, email_parser):
        """Тест: парсинг generic формата."""
        payload = {
            "from": "sender@example.com",
            "to": "invoices+tenant-1@yourapp.ai",
            "subject": "Invoice",
            "attachments": [
                {
                    "filename": "invoice.pdf",
                    "content_type": "application/pdf",
                    "content": "base64_content",
                    "size": 12345
                }
            ]
        }
        
        parsed = email_parser.parse(payload)
        
        assert parsed["from"] == "sender@example.com"
        assert parsed["to"] == "invoices+tenant-1@yourapp.ai"
        assert parsed["subject"] == "Invoice"
        assert len(parsed["attachments"]) == 1
