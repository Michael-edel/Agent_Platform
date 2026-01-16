"""Интеграционные тесты для IntegrationWorker."""

import pytest
import tempfile
import os
import time
from unittest.mock import Mock, patch

from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.idempotency_service import IdempotencyService
from cyberplat.integrations.integration_worker import IntegrationWorker
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
    job_service = IntegrationJobService(db_path=temp_db)
    settings_service = OneCSettingsService(db_path=temp_db)
    idempotency_service = IdempotencyService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return job_service, settings_service, idempotency_service, case_service


def test_worker_processes_job_success(services):
    """Тест: worker успешно обрабатывает job."""
    job_service, settings_service, idempotency_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        auth_type="token",
        token="test-token"
    )
    
    # Создаём job
    artifact = {
        "data": {
            "name": "ООО Тест",
            "inn": "1234567890"
        }
    }
    
    job_id = job_service.enqueue(
        tenant_id="tenant-123",
        provider="onec",
        job_type="upsert_counterparty",
        payload={"artifact_id": "art-123", "artifact": artifact}
    )
    
    # Мокаем OneCClient
    with patch('cyberplat.integrations.integration_worker.OneCClient') as mock_client_class:
        mock_client = Mock()
        mock_client.create_counterparty.return_value = {"id": "onec-cp-456"}
        mock_client_class.return_value = mock_client
        
        # Создаём и запускаем worker
        worker = IntegrationWorker(
            job_service=job_service,
            settings_service=settings_service,
            idempotency_service=idempotency_service,
            case_service=case_service,
            interval_seconds=0.1
        )
        
        # Обрабатываем job вручную (без запуска потока)
        jobs = job_service.claim_for_processing()
        assert len(jobs) == 1
        
        worker._process_onec_job(jobs[0])
        
        # Проверяем результат
        job = job_service.get_job(job_id)
        assert job["status"] == "succeeded"
        
        # Проверяем идемпотентность
        remote_id = idempotency_service.get_remote_id(
            tenant_id="tenant-123",
            provider="onec",
            object_type="counterparty",
            idempotency_key="upsert_counterparty:art-123"
        )
        assert remote_id == "onec-cp-456"


def test_worker_handles_disabled_integration(services):
    """Тест: worker обрабатывает отключённую интеграцию."""
    job_service, settings_service, idempotency_service, case_service = services
    
    # Настраиваем отключённую интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=False,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    job_id = job_service.enqueue(
        tenant_id="tenant-123",
        provider="onec",
        job_type="upsert_counterparty",
        payload={"artifact_id": "art-123", "artifact": {"data": {"name": "Test"}}}
    )
    
    worker = IntegrationWorker(
        job_service=job_service,
        settings_service=settings_service,
        idempotency_service=idempotency_service,
        case_service=case_service
    )
    
    jobs = job_service.claim_for_processing()
    assert len(jobs) == 1
    
    worker._process_onec_job(jobs[0])
    
    job = job_service.get_job(job_id)
    assert job["status"] == "failed"
    assert job["last_error_code"] == "integration_disabled"
    assert "отключена" in job["last_error_ru"].lower()


def test_worker_creates_case_task_on_final_failure(services):
    """Тест: worker создаёт задачу в кейсе при финальной ошибке."""
    job_service, settings_service, idempotency_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём job с case_id
    job_id = job_service.enqueue(
        tenant_id="tenant-123",
        provider="onec",
        job_type="upsert_counterparty",
        payload={"artifact_id": "art-123", "artifact": {"data": {}}},  # Нет name - ошибка валидации
        case_id=case_id
    )
    
    worker = IntegrationWorker(
        job_service=job_service,
        settings_service=settings_service,
        idempotency_service=idempotency_service,
        case_service=case_service
    )
    
    jobs = job_service.claim_for_processing()
    assert len(jobs) == 1
    
    worker._process_onec_job(jobs[0])
    
    # Проверяем, что job failed
    job = job_service.get_job(job_id)
    assert job["status"] == "failed"
    
    # Проверяем, что создана задача в кейсе
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_tasks WHERE case_id = ?", (case_id,))
    task = cur.fetchone()
    conn.close()
    
    assert task is not None
    assert "интеграции 1С" in task["title"].lower() or "1c" in task["title"].lower()
