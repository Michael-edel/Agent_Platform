"""Тесты для IdempotencyService."""

import pytest
import tempfile
import os

from cyberplat.integrations.idempotency_service import IdempotencyService


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
def idempotency_service_memory():
    """Создать IdempotencyService с in-memory БД для тестов."""
    service = IdempotencyService(db_path=":memory:")
    yield service
    service.close()


def test_get_remote_id_not_found(idempotency_service_memory):
    """Тест: get_remote_id для несуществующей записи возвращает None."""
    remote_id = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    assert remote_id is None


def test_mark_succeeded_get_remote_id(idempotency_service_memory):
    """Тест: mark_succeeded -> get_remote_id возвращает remote_id."""
    idempotency_service_memory.mark_succeeded(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123",
        remote_id="onec-cp-456"
    )
    
    remote_id = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    assert remote_id == "onec-cp-456"


def test_get_remote_id_failed_returns_none(idempotency_service_memory):
    """Тест: get_remote_id для failed записи возвращает None."""
    idempotency_service_memory.mark_failed(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    remote_id = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    assert remote_id is None


def test_mark_succeeded_idempotent(idempotency_service_memory):
    """Тест: повторный mark_succeeded обновляет remote_id."""
    # Первый раз
    idempotency_service_memory.mark_succeeded(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123",
        remote_id="onec-cp-456"
    )
    
    # Второй раз с другим remote_id
    idempotency_service_memory.mark_succeeded(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123",
        remote_id="onec-cp-789"
    )
    
    remote_id = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-123",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    assert remote_id == "onec-cp-789"


def test_tenant_isolation(idempotency_service_memory):
    """Тест: записи изолированы по tenant_id."""
    idempotency_service_memory.mark_succeeded(
        tenant_id="tenant-1",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123",
        remote_id="onec-cp-1"
    )
    
    idempotency_service_memory.mark_succeeded(
        tenant_id="tenant-2",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123",
        remote_id="onec-cp-2"
    )
    
    # Проверяем изоляцию
    remote_id_1 = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-1",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    remote_id_2 = idempotency_service_memory.get_remote_id(
        tenant_id="tenant-2",
        provider="onec",
        object_type="counterparty",
        idempotency_key="key-123"
    )
    
    assert remote_id_1 == "onec-cp-1"
    assert remote_id_2 == "onec-cp-2"
