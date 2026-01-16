"""Тесты для OneCSettingsService."""

import pytest
import tempfile
import os
from datetime import datetime

from cyberplat.integrations.onec_settings_service import OneCSettingsService


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
def settings_service(temp_db):
    """Создать OneCSettingsService для тестов."""
    return OneCSettingsService(db_path=temp_db)


@pytest.fixture
def settings_service_memory():
    """Создать OneCSettingsService с in-memory БД для тестов."""
    service = OneCSettingsService(db_path=":memory:")
    yield service
    service.close()


def test_get_settings_not_found(settings_service_memory):
    """Тест: get_settings для несуществующего tenant возвращает None."""
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings is None


def test_upsert_settings_create(settings_service_memory):
    """Тест: upsert_settings создаёт новые настройки."""
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        auth_type="token",
        token="secret-token-123",
        timeout_seconds=15
    )
    
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings is not None
    assert settings["tenant_id"] == "tenant-123"
    assert settings["enabled"] is True
    assert settings["base_url"] == "https://1c.example.com/api"
    assert settings["auth_type"] == "token"
    assert settings["token"] == "secret-token-123"
    assert settings["timeout_seconds"] == 15


def test_upsert_settings_update(settings_service_memory):
    """Тест: upsert_settings обновляет существующие настройки."""
    # Создаём
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="old-token"
    )
    
    # Обновляем
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=False,
        base_url="https://1c-new.example.com/api",
        token="new-token",
        timeout_seconds=20
    )
    
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings["enabled"] is False
    assert settings["base_url"] == "https://1c-new.example.com/api"
    assert settings["token"] == "new-token"
    assert settings["timeout_seconds"] == 20


def test_upsert_settings_basic_auth(settings_service_memory):
    """Тест: upsert_settings с basic auth."""
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        auth_type="basic",
        username="user1",
        password="pass123"
    )
    
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings["auth_type"] == "basic"
    assert settings["username"] == "user1"
    assert settings["password"] == "pass123"
    assert settings["token"] is None


def test_disable(settings_service_memory):
    """Тест: disable отключает интеграцию."""
    # Создаём включённую интеграцию
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="token"
    )
    
    # Отключаем
    settings_service_memory.disable("tenant-123")
    
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings["enabled"] is False


def test_default_timeout(settings_service_memory):
    """Тест: timeout_seconds по умолчанию = 10."""
    settings_service_memory.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="token"
    )
    
    settings = settings_service_memory.get_settings("tenant-123")
    assert settings["timeout_seconds"] == 10
