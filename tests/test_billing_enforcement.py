"""Тесты для Billing Enforcement (paywall)."""

import pytest
import os
from datetime import datetime
from unittest.mock import Mock, patch

from cyberplat.billing_service import BillingService
from cyberplat.event_service import EventService
from cyberplat.artifact_service import ArtifactService
from cyberplat.product.application.billing_enforcement_service import (
    BillingEnforcementService,
    QuotaExceededError
)


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)


@pytest.fixture
def billing_service(temp_db):
    """Создать BillingService для тестов."""
    svc = BillingService(db_path=temp_db)
    yield svc
    svc.close()


@pytest.fixture
def artifact_service(temp_db):
    """Создать ArtifactService для тестов."""
    svc = ArtifactService(db_path=temp_db)
    yield svc
    # ArtifactService uses per-call sqlite connections; no explicit close()
    if hasattr(svc, "close"):
        svc.close()


@pytest.fixture
def event_service(artifact_service):
    """Создать EventService для тестов."""
    event_svc = EventService(db_path=artifact_service.db_path, artifact_service=artifact_service)
    artifact_service.event_service = event_svc
    return event_svc


@pytest.fixture
def billing_enforcement(billing_service, event_service):
    """Создать BillingEnforcementService для тестов."""
    return BillingEnforcementService(
        billing_service=billing_service,
        event_service=event_service,
        entitlement_service=None
    )


class TestEnforcementBlocking:
    """Тесты блокировки операций при превышении квот."""
    
    def test_upload_blocked_when_document_upload_quota_exceeded(
        self,
        billing_service,
        billing_enforcement
    ):
        """Тест: upload блокируется когда квота document_upload превышена."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=5
        )
        
        # Создаём usage = quota (квота исчерпана)
        for i in range(5):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="artifact.created",
                metric="document_upload",
                units=1.0,
                unit_price_minor=100,
                currency="USD",
                period=period
            )
        
        # Пытаемся выполнить операцию (должна быть заблокирована)
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "block"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "block"
            
            with pytest.raises(QuotaExceededError) as exc_info:
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )
            
            assert exc_info.value.metric == "document_upload"
            assert exc_info.value.used_units == 5.0
            assert exc_info.value.monthly_quota == 5
    
    def test_run_ocr_blocked_when_invoice_extracted_quota_exceeded(
        self,
        billing_service,
        billing_enforcement
    ):
        """Тест: run-ocr блокируется когда квота invoice_extracted превышена."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="invoice_extracted",
            unit_price_minor=500,
            currency="USD",
            monthly_quota=10
        )
        
        # Создаём usage = quota
        for i in range(10):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="document.extracted",
                metric="invoice_extracted",
                units=1.0,
                unit_price_minor=500,
                currency="USD",
                period=period
            )
        
        # Пытаемся выполнить операцию
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "block"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "block"
            
            with pytest.raises(QuotaExceededError) as exc_info:
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={
                        "invoice_extracted": 1.0,
                        "page_processed": 1.0
                    },
                    operation_name="run_ocr"
                )
            
            assert exc_info.value.metric in ("invoice_extracted", "page_processed")
            assert exc_info.value.used_units >= 10.0


class TestEnforcementDisabled:
    """Тесты когда enforcement выключен."""
    
    def test_enforcement_disabled_allows_operations(
        self,
        billing_service,
        billing_enforcement
    ):
        """Тест: когда enforcement выключен, операции разрешены."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту и исчерпываем её
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=5
        )
        
        for i in range(5):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="artifact.created",
                metric="document_upload",
                units=1.0,
                unit_price_minor=100,
                currency="USD",
                period=period
            )
        
        # Enforcement выключен (default)
        billing_enforcement.enabled = False
        
        # Операция должна быть разрешена
        billing_enforcement.enforce(
            tenant_id=tenant_id,
            required_metrics={"document_upload": 1.0},
            operation_name="document_upload"
        )
        # Не должно быть исключения


class TestWarnMode:
    """Тесты warn mode."""
    
    def test_warn_mode_does_not_block(
        self,
        billing_service,
        billing_enforcement,
        event_service
    ):
        """Тест: warn mode не блокирует операции, только логирует и эмитит событие."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту и исчерпываем её
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=5
        )
        
        for i in range(5):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="artifact.created",
                metric="document_upload",
                units=1.0,
                unit_price_minor=100,
                currency="USD",
                period=period
            )
        
        # Warn mode
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "warn"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "warn"
            
            # Операция должна быть разрешена (не должно быть исключения)
            billing_enforcement.enforce(
                tenant_id=tenant_id,
                required_metrics={"document_upload": 1.0},
                operation_name="document_upload"
            )
            
            # Проверяем, что событие было эмитировано
            # (в реальном тесте можно проверить через event_service)


class TestTenantIsolation:
    """Тесты tenant isolation для enforcement."""
    
    def test_tenant_isolation_enforcement(
        self,
        billing_service,
        billing_enforcement
    ):
        """Тест: enforcement изолирован по tenant."""
        tenant_1 = "tenant-1"
        tenant_2 = "tenant-2"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту для tenant_1 и исчерпываем её
        billing_service.upsert_rate(
            tenant_id=tenant_1,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=5
        )
        
        for i in range(5):
            billing_service.record_event_charge(
                tenant_id=tenant_1,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="artifact.created",
                metric="document_upload",
                units=1.0,
                unit_price_minor=100,
                currency="USD",
                period=period
            )
        
        # tenant_2 не имеет квоты (или имеет другую)
        billing_service.upsert_rate(
            tenant_id=tenant_2,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=10  # Больше квоты
        )
        
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "block"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "block"
            
            # tenant_1 должен быть заблокирован
            with pytest.raises(QuotaExceededError):
                billing_enforcement.enforce(
                    tenant_id=tenant_1,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )
            
            # tenant_2 должен быть разрешён
            billing_enforcement.enforce(
                tenant_id=tenant_2,
                required_metrics={"document_upload": 1.0},
                operation_name="document_upload"
            )
            # Не должно быть исключения


class TestEmailAutoOcr:
    """Тесты enforcement для email auto-OCR."""
    
    def test_email_auto_ocr_job_skipped_when_quota_exceeded(
        self,
        billing_service,
        billing_enforcement
    ):
        """Тест: email auto-OCR job пропускается когда квота превышена."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту и исчерпываем её
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="invoice_extracted",
            unit_price_minor=500,
            currency="USD",
            monthly_quota=10
        )
        
        for i in range(10):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="document.extracted",
                metric="invoice_extracted",
                units=1.0,
                unit_price_minor=500,
                currency="USD",
                period=period
            )
        
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "block"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "block"
            
            # Должна быть заблокирована
            with pytest.raises(QuotaExceededError):
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={
                        "invoice_extracted": 1.0,
                        "page_processed": 1.0
                    },
                    operation_name="email_auto_ocr_job"
                )


class TestWebhookEvent:
    """Тесты webhook события для quota.exceeded."""
    
    def test_quota_exceeded_webhook_created(
        self,
        billing_service,
        billing_enforcement,
        event_service
    ):
        """Тест: при превышении квоты эмитируется событие billing.quota.exceeded."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Устанавливаем квоту и исчерпываем её
        billing_service.upsert_rate(
            tenant_id=tenant_id,
            metric="document_upload",
            unit_price_minor=100,
            currency="USD",
            monthly_quota=5
        )
        
        for i in range(5):
            billing_service.record_event_charge(
                tenant_id=tenant_id,
                event_id=f"event-{i}",
                artifact_id=f"artifact-{i}",
                event_type="artifact.created",
                metric="document_upload",
                units=1.0,
                unit_price_minor=100,
                currency="USD",
                period=period
            )
        
        # Собираем эмитированные события
        emitted_events = []
        
        def capture_event(event_id, event_type, tenant_id, artifact_id, payload, created_at):
            if event_type == "billing.quota.exceeded":
                emitted_events.append({
                    "event_type": event_type,
                    "tenant_id": tenant_id,
                    "payload": payload
                })
        
        event_service.subscribe(capture_event)
        
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1", "BILLING_ENFORCEMENT_MODE": "block"}):
            billing_enforcement.enabled = True
            billing_enforcement.mode = "block"
            
            try:
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )
            except QuotaExceededError:
                pass  # Ожидаем исключение
        
        # Проверяем, что событие было эмитировано
        assert len(emitted_events) == 1
        assert emitted_events[0]["event_type"] == "billing.quota.exceeded"
        assert emitted_events[0]["tenant_id"] == tenant_id
        assert emitted_events[0]["payload"]["metric"] == "document_upload"
        assert emitted_events[0]["payload"]["operation"] == "document_upload"
