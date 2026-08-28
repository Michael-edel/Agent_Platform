"""Тесты для Webhooks (tenant isolation, delivery retries, signature, idempotency, admin protection)."""

import pytest
import uuid
import hmac
import hashlib
import json
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
import os

from cyberplat.event_service import EventService
from cyberplat.artifact_service import ArtifactService
from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import (
    WebhookRepositoryImpl,
    WebhookDeliveryRepositoryImpl
)
from cyberplat.product.application.dispatch_webhooks_use_case import DispatchWebhooksUseCase
from app.api.webhooks import router as webhooks_router
from app.api.admin_auth import admin_auth


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
def webhook_repo(db_session):
    """Создать WebhookRepository для тестов."""
    return WebhookRepositoryImpl(session=db_session)


@pytest.fixture
def webhook_delivery_repo(db_session):
    """Создать WebhookDeliveryRepository для тестов."""
    return WebhookDeliveryRepositoryImpl(session=db_session)


@pytest.fixture
def test_app():
    """Создать тестовое FastAPI приложение."""
    app = FastAPI()
    app.include_router(webhooks_router, prefix="/api/v1")
    return app


class TestTenantIsolation:
    """Тесты tenant isolation для webhooks."""
    
    def test_webhooks_are_tenant_isolated(
        self,
        webhook_repo
    ):
        """Тест: webhooks разных tenant изолированы."""
        tenant_1 = "tenant-1"
        tenant_2 = "tenant-2"
        
        # Создаём webhooks для разных tenant
        webhook_id_1 = webhook_repo.create_webhook(
            tenant_id=tenant_1,
            url="https://example.com/webhook1",
            events=["invoice.ready"],
            secret="secret1"
        )
        
        webhook_id_2 = webhook_repo.create_webhook(
            tenant_id=tenant_2,
            url="https://example.com/webhook2",
            events=["invoice.ready"],
            secret="secret2"
        )
        
        # Получаем webhooks для tenant_1
        webhooks_1 = webhook_repo.list_webhooks(tenant_id=tenant_1)
        
        # Получаем webhooks для tenant_2
        webhooks_2 = webhook_repo.list_webhooks(tenant_id=tenant_2)
        
        # Проверяем изоляцию
        assert len(webhooks_1) == 1
        assert webhooks_1[0]["id"] == webhook_id_1
        assert webhooks_1[0]["tenant_id"] == tenant_1
        
        assert len(webhooks_2) == 1
        assert webhooks_2[0]["id"] == webhook_id_2
        assert webhooks_2[0]["tenant_id"] == tenant_2
        
        # Проверяем, что tenant_1 не видит webhook tenant_2
        webhook = webhook_repo.get_webhook(tenant_id=tenant_1, webhook_id=webhook_id_2)
        assert webhook is None


class TestDeliveryRetries:
    """Тесты retries для webhook deliveries."""
    
    def test_delivery_retry_on_failure(
        self,
        webhook_repo,
        webhook_delivery_repo,
        event_service
    ):
        """Тест: delivery retry при ошибке увеличивает attempts и устанавливает next_run_at."""
        tenant_id = "tenant-1"
        
        # Создаём webhook
        webhook_id = webhook_repo.create_webhook(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            events=["invoice.ready"],
            secret="test-secret"
        )
        
        # Создаём delivery
        payload = {
            "event_type": "invoice.ready",
            "tenant_id": tenant_id,
            "artifact_id": "artifact-123"
        }
        
        delivery_id = webhook_delivery_repo.create_delivery(
            webhook_id=webhook_id,
            event_type="invoice.ready",
            payload=payload,
            max_retries=5
        )
        
        # Мокаем HTTP запрос чтобы он падал
        with patch("httpx.Client.post") as mock_post:
            mock_post.side_effect = Exception("Connection error")
            
            use_case = DispatchWebhooksUseCase(
                webhook_repo=webhook_repo,
                webhook_delivery_repo=webhook_delivery_repo,
                event_service=event_service
            )
            
            result = use_case.execute(limit=1)
            assert result["failed"] == 1
        
        # Проверяем, что delivery стал failed
        delivery = webhook_delivery_repo.get_delivery(delivery_id)
        assert delivery["status"] == "failed"
        assert delivery["attempts"] == 1
        assert delivery["next_run_at"] is not None
        assert delivery["last_error"] is not None


class TestSignature:
    """Тесты HMAC подписи."""
    
    def test_signature_correctness(
        self,
        webhook_repo,
        webhook_delivery_repo,
        event_service
    ):
        """Тест: HMAC подпись вычисляется корректно."""
        tenant_id = "tenant-1"
        secret = "test-secret-key"
        
        # Создаём webhook
        webhook_id = webhook_repo.create_webhook(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            events=["invoice.ready"],
            secret=secret
        )
        
        # Создаём delivery
        payload = {
            "event_type": "invoice.ready",
            "tenant_id": tenant_id,
            "artifact_id": "artifact-123"
        }
        
        delivery_id = webhook_delivery_repo.create_delivery(
            webhook_id=webhook_id,
            event_type="invoice.ready",
            payload=payload,
            max_retries=5
        )
        
        delivery = webhook_delivery_repo.get_delivery(delivery_id)
        
        # Мокаем HTTP запрос и проверяем подпись
        captured_headers = {}
        
        def capture_request(*args, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = Mock()
            response.status_code = 200
            return response
        
        with patch("httpx.Client.post", side_effect=capture_request):
            use_case = DispatchWebhooksUseCase(
                webhook_repo=webhook_repo,
                webhook_delivery_repo=webhook_delivery_repo,
                event_service=event_service
            )
            
            use_case.execute(limit=1)
        
        # Проверяем, что подпись присутствует
        assert "X-Signature" in captured_headers
        
        # Проверяем, что подпись корректна
        payload_json = json.dumps(payload, sort_keys=True)
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        assert captured_headers["X-Signature"] == expected_signature


class TestIdempotency:
    """Тесты идемпотентности."""
    
    def test_one_event_creates_one_delivery_per_webhook(
        self,
        webhook_repo,
        webhook_delivery_repo,
        event_service
    ):
        """Тест: один event создаёт один delivery для каждого webhook."""
        tenant_id = "tenant-1"
        
        # Создаём два webhook для одного tenant
        webhook_id_1 = webhook_repo.create_webhook(
            tenant_id=tenant_id,
            url="https://example.com/webhook1",
            events=["invoice.ready"],
            secret="secret1"
        )
        
        webhook_id_2 = webhook_repo.create_webhook(
            tenant_id=tenant_id,
            url="https://example.com/webhook2",
            events=["invoice.ready"],
            secret="secret2"
        )
        
        # Эмитим событие
        from cyberplat.product.infrastructure.webhook_subscriber import create_webhook_subscriber
        
        webhook_subscriber = create_webhook_subscriber(
            webhook_repo=webhook_repo,
            webhook_delivery_repo=webhook_delivery_repo
        )
        
        # Симулируем эмиссию события
        webhook_subscriber(
            event_id="event-123",
            event_type="invoice.ready",
            tenant_id=tenant_id,
            artifact_id="artifact-123",
            payload={"status": "ready"},
            created_at=datetime.now().isoformat()
        )
        
        # Проверяем, что создано 2 delivery (по одному для каждого webhook)
        pending = webhook_delivery_repo.get_pending_deliveries(limit=10)
        
        # Фильтруем по нашему событию
        invoice_ready_deliveries = [
            d for d in pending
            if d["event_type"] == "invoice.ready" and d["webhook_id"] in [webhook_id_1, webhook_id_2]
        ]
        
        assert len(invoice_ready_deliveries) == 2


class TestAdminProtection:
    """Тесты admin protection для dispatch endpoint."""
    
    def test_dispatch_requires_admin_key_when_set(self, test_app):
        """Тест: dispatch требует admin key когда ADMIN_API_KEY задан."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
            client = TestClient(test_app)
            
            # Без заголовка
            response = client.post("/api/v1/webhooks/dispatch?limit=10")
            assert response.status_code == 403
            assert "X-Admin-Key" in response.json()["detail"]
            
            # С неверным ключом
            response = client.post(
                "/api/v1/webhooks/dispatch?limit=10",
                headers={"X-Admin-Key": "wrong-key"}
            )
            assert response.status_code == 403
            assert "Invalid" in response.json()["detail"]
    
    def test_dispatch_denies_when_admin_key_is_missing(self, test_app):
        """Тест: dispatch закрыт, когда ADMIN_API_KEY не настроен."""
        with patch.dict(os.environ, {}, clear=True):
            if "ADMIN_API_KEY" in os.environ:
                del os.environ["ADMIN_API_KEY"]
            
            client = TestClient(test_app)
            
            # Мокаем зависимости
            with patch("app.api.webhooks.get_sessionmaker") as mock_session, \
                 patch("app.api.webhooks.EventService") as mock_event:
                
                response = client.post("/api/v1/webhooks/dispatch?limit=10")
                assert response.status_code == 503
                assert response.json()["detail"] == "Administrative API is not configured"


class TestSafePayload:
    """Тесты безопасного payload (без raw PDF/OCR JSON)."""
    
    def test_payload_does_not_contain_raw_data(
        self,
        webhook_repo,
        webhook_delivery_repo
    ):
        """Тест: payload не содержит raw PDF/OCR JSON."""
        tenant_id = "tenant-1"
        
        webhook_id = webhook_repo.create_webhook(
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            events=["invoice.ready"],
            secret="secret"
        )
        
        # Создаём delivery с "опасным" payload
        from cyberplat.product.infrastructure.webhook_subscriber import _create_safe_payload
        
        original_payload = {
            "raw_ocr_json": {"text": "..."},  # Не должно попасть
            "pdf_content": "base64...",  # Не должно попасть
            "status": "ready",  # Должно попасть
            "artifact_id": "artifact-123"  # Должно попасть
        }
        
        safe_payload = _create_safe_payload(
            event_type="invoice.ready",
            tenant_id=tenant_id,
            artifact_id="artifact-123",
            original_payload=original_payload,
            created_at=datetime.now().isoformat()
        )
        
        # Проверяем, что опасные поля отсутствуют
        assert "raw_ocr_json" not in safe_payload
        assert "pdf_content" not in safe_payload
        
        # Проверяем, что безопасные поля присутствуют
        assert safe_payload["event_type"] == "invoice.ready"
        assert safe_payload["tenant_id"] == tenant_id
        assert safe_payload["artifact_id"] == "artifact-123"
        assert "timestamp" in safe_payload
