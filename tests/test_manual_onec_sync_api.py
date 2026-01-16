"""Тесты для ручного sync API."""

import pytest
import tempfile
import os
from fastapi.testclient import TestClient

from app.main import app
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


def test_manual_sync_success(services):
    """Тест: ручной sync успешно создаёт job."""
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
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест", "inn": "1234567890"},
        tenant_id="tenant-123"
    )
    
    # Мокаем зависимости в app
    from app.api.cases import get_onec_settings_service, get_integration_job_service, get_idempotency_service, get_artifact_service, get_case_service
    
    app.dependency_overrides[get_onec_settings_service] = lambda: settings_service
    app.dependency_overrides[get_integration_job_service] = lambda: job_service
    app.dependency_overrides[get_idempotency_service] = lambda: idempotency_service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/cases/{case_id}/sync/onec",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "object_type": "counterparty",
                "artifact_id": artifact_id
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "отправлена" in data["message"].lower() or "queued" in data["message"].lower()
        assert data["job_id"] is not None
        
        # Проверяем, что job создан
        job = job_service.get_job(data["job_id"])
        assert job is not None
        assert job["job_type"] == "upsert_counterparty"
        
    finally:
        app.dependency_overrides.clear()


def test_manual_sync_idempotent(services):
    """Тест: повторный sync возвращает существующий job."""
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
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-123"
    )
    
    # Мокаем зависимости
    from app.api.cases import get_onec_settings_service, get_integration_job_service, get_idempotency_service, get_artifact_service, get_case_service
    
    app.dependency_overrides[get_onec_settings_service] = lambda: settings_service
    app.dependency_overrides[get_integration_job_service] = lambda: job_service
    app.dependency_overrides[get_idempotency_service] = lambda: idempotency_service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        # Первый sync
        response1 = client.post(
            f"/api/v1/cases/{case_id}/sync/onec",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "object_type": "counterparty",
                "artifact_id": artifact_id
            }
        )
        
        assert response1.status_code == 200
        job_id_1 = response1.json()["job_id"]
        
        # Второй sync (должен вернуть тот же job_id)
        response2 = client.post(
            f"/api/v1/cases/{case_id}/sync/onec",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "object_type": "counterparty",
                "artifact_id": artifact_id
            }
        )
        
        assert response2.status_code == 200
        job_id_2 = response2.json()["job_id"]
        
        assert job_id_1 == job_id_2
        
    finally:
        app.dependency_overrides.clear()


def test_manual_sync_artifact_not_in_case(services):
    """Тест: artifact не принадлежит кейсу → RU ошибка."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=True,
        base_url="https://1c.example.com/api",
        token="test-token"
    )
    
    # Создаём кейс для tenant-1
    case_id = case_service.create_case(
        tenant_id="tenant-1",
        case_type="support",
        title="Test case"
    )
    
    # Создаём артефакт для tenant-2
    artifact_id = artifact_service.create_artifact(
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-2"
    )
    
    # Мокаем зависимости
    from app.api.cases import get_onec_settings_service, get_integration_job_service, get_idempotency_service, get_artifact_service, get_case_service
    
    app.dependency_overrides[get_onec_settings_service] = lambda: settings_service
    app.dependency_overrides[get_integration_job_service] = lambda: job_service
    app.dependency_overrides[get_idempotency_service] = lambda: idempotency_service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        # Пытаемся синхронизировать артефакт из другого tenant
        response = client.post(
            f"/api/v1/cases/{case_id}/sync/onec",
            headers={"X-Tenant-ID": "tenant-1"},
            json={
                "object_type": "counterparty",
                "artifact_id": artifact_id
            }
        )
        
        assert response.status_code == 403
        assert "не принадлежит" in response.json()["detail"].lower() or "not belong" in response.json()["detail"].lower()
        
    finally:
        app.dependency_overrides.clear()


def test_manual_sync_integration_disabled(services):
    """Тест: integration отключена → RU сообщение."""
    settings_service, job_service, idempotency_service, artifact_service, case_service = services
    
    # Настраиваем отключённую интеграцию
    settings_service.upsert_settings(
        tenant_id="tenant-123",
        enabled=False,
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
        kind="counterparty",
        source="test",
        data={"name": "ООО Тест"},
        tenant_id="tenant-123"
    )
    
    # Мокаем зависимости
    from app.api.cases import get_onec_settings_service, get_integration_job_service, get_idempotency_service, get_artifact_service, get_case_service
    
    app.dependency_overrides[get_onec_settings_service] = lambda: settings_service
    app.dependency_overrides[get_integration_job_service] = lambda: job_service
    app.dependency_overrides[get_idempotency_service] = lambda: idempotency_service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/cases/{case_id}/sync/onec",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "object_type": "counterparty",
                "artifact_id": artifact_id
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "отключена" in data["message"].lower() or "disabled" in data["message"].lower()
        
    finally:
        app.dependency_overrides.clear()
