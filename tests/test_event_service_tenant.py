"""Тесты для строгой валидации tenant_id в EventService."""

import pytest
import tempfile
import os
from pathlib import Path

from cyberplat.event_service import EventService, TenantValidationError
from cyberplat.artifact_service import ArtifactService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        # Retry логика для Windows (файл может быть временно заблокирован)
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
def artifact_service(temp_db):
    """Создать ArtifactService для тестов."""
    event_service = EventService(db_path=temp_db)
    artifact_service = ArtifactService(db_path=temp_db, event_service=event_service)
    event_service.artifact_service = artifact_service
    return artifact_service


@pytest.fixture
def event_service(temp_db, artifact_service):
    """Создать EventService для тестов."""
    event_service = EventService(db_path=temp_db, artifact_service=artifact_service)
    return event_service


def test_emit_without_tenant_id_and_artifact_id_raises(event_service):
    """Тест: emit без tenant_id и без artifact_id должен падать."""
    with pytest.raises(TenantValidationError, match="tenant_id обязателен"):
        event_service.emit(
            event_type="test.event",
            tenant_id=None,
            artifact_id=None
        )


def test_emit_with_string_tenant_id_raises(event_service):
    """Тест: emit с tenant_id='string' должен падать."""
    with pytest.raises(TenantValidationError, match="не может быть строкой 'string'"):
        event_service.emit(
            event_type="test.event",
            tenant_id="string",
            artifact_id=None
        )


def test_emit_with_empty_tenant_id_raises(event_service):
    """Тест: emit с пустым tenant_id должен падать."""
    with pytest.raises(TenantValidationError, match="не может быть пустой строкой"):
        event_service.emit(
            event_type="test.event",
            tenant_id="   ",
            artifact_id=None
        )


def test_emit_with_valid_tenant_id_succeeds(event_service):
    """Тест: emit с валидным tenant_id должен работать."""
    event_id = event_service.emit(
        event_type="test.event",
        tenant_id="tenant-123",
        artifact_id=None
    )
    assert event_id is not None


def test_emit_with_artifact_id_gets_tenant_from_artifact(event_service, artifact_service):
    """Тест: emit с artifact_id должен получать tenant_id из артефакта."""
    # Создаем артефакт
    artifact_id = artifact_service.create_artifact(
        kind="test",
        source="test",
        data={"test": "data"},
        tenant_id="tenant-456"
    )
    
    # Эмитим событие без tenant_id, но с artifact_id
    event_id = event_service.emit(
        event_type="test.event",
        tenant_id=None,
        artifact_id=artifact_id
    )
    assert event_id is not None
    
    # Проверяем, что событие создано с правильным tenant_id
    conn = event_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT tenant_id FROM events WHERE id = ?", (event_id,))
    row = cur.fetchone()
    conn.close()
    
    assert row is not None
    assert row["tenant_id"] == "tenant-456"


def test_emit_with_tenant_id_mismatch_raises(event_service, artifact_service):
    """Тест: emit с tenant_id != artifact.tenant_id должен падать."""
    # Создаем артефакт
    artifact_id = artifact_service.create_artifact(
        kind="test",
        source="test",
        data={"test": "data"},
        tenant_id="tenant-456"
    )
    
    # Пытаемся эмитить событие с другим tenant_id
    with pytest.raises(TenantValidationError, match="не совпадает с tenant_id артефакта"):
        event_service.emit(
            event_type="test.event",
            tenant_id="tenant-999",  # Другой tenant_id
            artifact_id=artifact_id
        )


def test_emit_with_tenant_id_match_succeeds(event_service, artifact_service):
    """Тест: emit с tenant_id == artifact.tenant_id должен работать."""
    # Создаем артефакт
    artifact_id = artifact_service.create_artifact(
        kind="test",
        source="test",
        data={"test": "data"},
        tenant_id="tenant-456"
    )
    
    # Эмитим событие с совпадающим tenant_id
    event_id = event_service.emit(
        event_type="test.event",
        tenant_id="tenant-456",
        artifact_id=artifact_id
    )
    assert event_id is not None


def test_emit_with_artifact_without_tenant_id_raises(event_service, artifact_service):
    """Тест: emit с артефактом без tenant_id должен падать, если tenant_id не передан явно."""
    # Создаем артефакт без tenant_id (через прямой SQL, чтобы обойти валидацию)
    import sqlite3
    conn = artifact_service._get_connection()
    cur = conn.cursor()
    artifact_id = "test-artifact-123"
    cur.execute("""
        INSERT INTO artifacts (id, kind, source, tenant_id, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        artifact_id,
        "test",
        "test",
        None,  # Нет tenant_id
        '{"test": "data"}',
        "2024-01-01T00:00:00"
    ))
    conn.commit()
    conn.close()
    
    # Пытаемся эмитить событие без tenant_id
    with pytest.raises(TenantValidationError, match="не имеет tenant_id"):
        event_service.emit(
            event_type="test.event",
            tenant_id=None,
            artifact_id=artifact_id
        )
