"""Тесты для IntegrationJobService."""

import pytest
import tempfile
import os
import time

from cyberplat.integrations.integration_job_service import IntegrationJobService


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
def job_service_memory():
    """Создать IntegrationJobService с in-memory БД для тестов."""
    service = IntegrationJobService(db_path=":memory:")
    yield service
    service.close()


def test_enqueue(job_service_memory):
    """Тест: enqueue создаёт job."""
    job_id = job_service_memory.enqueue(
        tenant_id="tenant-123",
        provider="onec",
        job_type="upsert_counterparty",
        payload={"artifact_id": "art-123", "artifact": {"data": {"name": "Test"}}}
    )
    
    assert job_id is not None
    
    job = job_service_memory.get_job(job_id)
    assert job is not None
    assert job["tenant_id"] == "tenant-123"
    assert job["provider"] == "onec"
    assert job["job_type"] == "upsert_counterparty"
    assert job["status"] == "pending"
    assert job["attempt"] == 0


def test_enqueue_with_case_id(job_service_memory):
    """Тест: enqueue с case_id."""
    job_id = job_service_memory.enqueue(
        tenant_id="tenant-123",
        provider="onec",
        job_type="upsert_invoice",
        payload={"artifact_id": "art-123"},
        case_id="case-456"
    )
    
    job = job_service_memory.get_job(job_id)
    assert job["case_id"] == "case-456"


def test_claim_for_processing(job_service_memory):
    """Тест: claim_for_processing забирает pending jobs."""
    # Создаём несколько jobs
    job1_id = job_service_memory.enqueue("tenant-1", "onec", "upsert_counterparty", {"artifact_id": "art-1"})
    job2_id = job_service_memory.enqueue("tenant-2", "onec", "upsert_counterparty", {"artifact_id": "art-2"})
    
    # Забираем jobs
    jobs = job_service_memory.claim_for_processing(limit=10)
    
    assert len(jobs) == 2
    job_ids = [j["id"] for j in jobs]
    assert job1_id in job_ids
    assert job2_id in job_ids
    
    # Проверяем, что статус изменился
    job1 = job_service_memory.get_job(job1_id)
    assert job1["status"] == "processing"


def test_mark_succeeded(job_service_memory):
    """Тест: mark_succeeded меняет статус на succeeded."""
    job_id = job_service_memory.enqueue("tenant-123", "onec", "upsert_counterparty", {"artifact_id": "art-123"})
    
    # Забираем job
    jobs = job_service_memory.claim_for_processing()
    assert len(jobs) == 1
    
    # Отмечаем успех
    job_service_memory.mark_succeeded(job_id)
    
    job = job_service_memory.get_job(job_id)
    assert job["status"] == "succeeded"


def test_mark_failed_with_retry(job_service_memory):
    """Тест: mark_failed с schedule_retry планирует повтор."""
    job_id = job_service_memory.enqueue("tenant-123", "onec", "upsert_counterparty", {"artifact_id": "art-123"})
    
    jobs = job_service_memory.claim_for_processing()
    assert len(jobs) == 1
    
    # Отмечаем ошибку с retry
    job_service_memory.mark_failed(
        job_id,
        error_ru="Временная ошибка",
        error_code="temporary_error",
        schedule_retry=True
    )
    
    job = job_service_memory.get_job(job_id)
    assert job["status"] == "pending_retry"
    assert job["attempt"] == 1
    assert job["last_error_ru"] == "Временная ошибка"
    assert job["last_error_code"] == "temporary_error"
    assert job["next_attempt_at"] is not None


def test_mark_failed_no_retry(job_service_memory):
    """Тест: mark_failed без retry отмечает как failed."""
    job_id = job_service_memory.enqueue("tenant-123", "onec", "upsert_counterparty", {"artifact_id": "art-123"})
    
    jobs = job_service_memory.claim_for_processing()
    assert len(jobs) == 1
    
    # Отмечаем ошибку без retry
    job_service_memory.mark_failed(
        job_id,
        error_ru="Критическая ошибка",
        error_code="critical_error",
        schedule_retry=False
    )
    
    job = job_service_memory.get_job(job_id)
    assert job["status"] == "failed"
    assert job["attempt"] == 1


def test_mark_failed_max_attempts(job_service_memory):
    """Тест: mark_failed после max_attempts отмечает как failed."""
    job_id = job_service_memory.enqueue(
        "tenant-123",
        "onec",
        "upsert_counterparty",
        {"artifact_id": "art-123"},
        max_attempts=2
    )
    
    # Симулируем несколько попыток
    for _ in range(2):
        jobs = job_service_memory.claim_for_processing()
        if jobs:
            job_service_memory.mark_failed(
                job_id,
                error_ru="Ошибка",
                error_code="error",
                schedule_retry=True
            )
    
    job = job_service_memory.get_job(job_id)
    assert job["status"] == "failed"
    assert job["attempt"] == 2


def test_list_jobs_filter(job_service_memory):
    """Тест: list_jobs с фильтрацией."""
    job1_id = job_service_memory.enqueue("tenant-1", "onec", "upsert_counterparty", {"artifact_id": "art-1"})
    job2_id = job_service_memory.enqueue("tenant-2", "onec", "upsert_counterparty", {"artifact_id": "art-2"})
    
    # Фильтр по tenant_id
    jobs = job_service_memory.list_jobs(tenant_id="tenant-1")
    assert len(jobs) == 1
    assert jobs[0]["id"] == job1_id
    
    # Фильтр по provider
    jobs = job_service_memory.list_jobs(provider="onec")
    assert len(jobs) == 2
    
    # Фильтр по status
    job_service_memory.mark_succeeded(job1_id)
    jobs = job_service_memory.list_jobs(status="succeeded")
    assert len(jobs) == 1
    assert jobs[0]["id"] == job1_id
