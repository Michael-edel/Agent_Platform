"""Тесты для OneCArtifactHook."""

import pytest
import tempfile
import os

from cyberplat.integrations.onec_artifact_hook import OneCArtifactHook
from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.integrations.idempotency_service import IdempotencyService
from cyberplat.artifact_service import ArtifactService
from cyberplat.case_service import CaseService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(db_path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                else:
                    import logging
                    logging.warning(f"Не удалось удалить временный файл {db_path} после {max_retries} попыток")


@pytest.fixture
def services(temp_db):
    """Создать сервисы для тестов."""
    settings_service = OneCSettingsService(db_path=temp_db)
    job_service = IntegrationJobService(db_path=temp_db)
    idempotency_service = IdempotencyService(db_path=temp_db)
    artifact_service = ArtifactService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return settings_service, job_service, idempotency_service, artifact_service, case_service


@pytest.fixture
def hook(services):
    """Создать OneCArtifactHook для тестов."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    return OneCArtifactHook(
        settings_service=settings_service,
        job_service=job_service,
        idempotency_service=idempotency_service,
        artifact_service=artifact_service,
        case_service=case_service
    )


def test_artifact_finalized_creates_job(hook, services):
    """Тест: artifact finalized → job создан."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём артефакт
    artifact_id = artifact_service.create_artifact(
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест", "inn": "1234567890"},
        tenant_id="tenant-123"
    )
    
    # Обрабатываем событие
    hook(
        event_id="event-123",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:00:00"
    )
    
    # Проверяем, что job создан
    jobs = job_service.list_jobs(tenant_id="tenant-123", provider="onec")
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "upsert_counterparty"
    assert jobs[0]["status"] == "pending"


def test_integration_disabled_no_job(hook, services):
    """Тест: integration disabled → job НЕ создаётся."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем отключённую интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=False,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём артефакт
    artifact_id = artifact_service.create_artifact(
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-123"
    )
    
    # Обрабатываем событие
    hook(
        event_id="event-123",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:00:00"
    )
    
    # Проверяем, что job НЕ создан
    jobs = job_service.list_jobs(tenant_id="tenant-123", provider="onec")
    assert len(jobs) == 0


def test_integration_not_configured_no_job(hook, services):
    """Тест: integration не настроена → job НЕ создаётся."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # НЕ настраиваем интеграцию
    
    # Создаём артефакт
    artifact_id = artifact_service.create_artifact(
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-123"
    )
    
    # Обрабатываем событие
    hook(
        event_id="event-123",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:00:00"
    )
    
    # Проверяем, что job НЕ создан
    jobs = job_service.list_jobs(tenant_id="tenant-123", provider="onec")
    assert len(jobs) == 0


def test_duplicate_event_no_duplicate_job(hook, services):
    """Тест: повторное событие → job не дублируется."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём артефакт
    artifact_id = artifact_service.create_artifact(
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-123"
    )
    
    # Первое событие
    hook(
        event_id="event-1",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:00:00"
    )
    
    # Второе событие (тот же артефакт)
    hook(
        event_id="event-2",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:01:00"
    )
    
    # Проверяем, что создан только один job
    jobs = job_service.list_jobs(tenant_id="tenant-123", provider="onec")
    assert len(jobs) == 1


def test_unsupported_artifact_kind_no_job(hook, services):
    """Тест: неподдерживаемый artifact.kind → job НЕ создаётся."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём артефакт с неподдерживаемым kind
    artifact_id = artifact_service.create_artifact(
        kind="document",  # Не counterparty/contract/invoice
        source="test",
        data={"name": "Test"},
        tenant_id="tenant-123"
    )
    
    # Обрабатываем событие
    hook(
        event_id="event-123",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={},
        created_at="2025-01-15T10:00:00"
    )
    
    # Проверяем, что job НЕ создан
    jobs = job_service.list_jobs(tenant_id="tenant-123", provider="onec")
    assert len(jobs) == 0


def test_artifact_with_case_id_creates_audit_event(hook, services):
    """Тест: artifact с case_id → audit event в кейсе."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём артефакт
    artifact_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data={"number": "СЧ-001", "amount": 1000},
        tenant_id="tenant-123"
    )
    
    # Обрабатываем событие с case_id
    hook(
        event_id="event-123",
        event_type="artifact.created",
        tenant_id="tenant-123",
        artifact_id=artifact_id,
        payload={"case_id": case_id},
        created_at="2025-01-15T10:00:00"
    )
    
    # Проверяем audit event в кейсе
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "integration_job_queued"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None
