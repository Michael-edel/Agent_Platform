"""Тесты для S3 экспорта."""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile

from cyberplat.s3_exporter import S3Exporter, BOTO3_AVAILABLE
from cyberplat.s3_export_subscriber import S3ExportSubscriber
from cyberplat.artifact_service import ArtifactService
from cyberplat.storage_service import StorageService


@pytest.fixture
def mock_s3_client():
    """Создать mock S3 клиент."""
    with patch("cyberplat.s3_exporter.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_client


@pytest.fixture
def s3_exporter(mock_s3_client):
    """Создать S3Exporter для тестов."""
    if not BOTO3_AVAILABLE:
        pytest.skip("boto3 не установлен")
    
    # КРИТИЧНО: Передаем mock_s3_client напрямую в конструктор
    # Это гарантирует, что mock используется вместо создания реального клиента
    exporter = S3Exporter(
        endpoint_url="http://127.0.0.1:9000",
        access_key="test_key",
        secret_key="test_secret",
        bucket="test-bucket",
        region="us-east-1",
        prefix="test-prefix",
        s3_client=mock_s3_client
    )
    return exporter


def test_s3_exporter_build_key(s3_exporter, mock_s3_client):
    """Тест: построение S3 ключа."""
    # Без префикса
    exporter_no_prefix = S3Exporter(
        endpoint_url="http://127.0.0.1:9000",
        access_key="test_key",
        secret_key="test_secret",
        bucket="test-bucket",
        prefix="",
        s3_client=mock_s3_client
    )
    
    key1 = exporter_no_prefix._build_key("tenant-123", "documents", "file.pdf")
    assert key1 == "tenant-123/documents/file.pdf"
    
    # С префиксом
    key2 = s3_exporter._build_key("tenant-123", "documents", "file.pdf")
    assert key2 == "test-prefix/tenant-123/documents/file.pdf"
    
    # С несколькими частями
    key3 = s3_exporter._build_key("tenant-123", "invoices", "artifact-id.json")
    assert key3 == "test-prefix/tenant-123/invoices/artifact-id.json"


def test_s3_exporter_put_bytes(s3_exporter, mock_s3_client):
    """Тест: загрузка байтов в S3."""
    data = b"test data"
    key = "test-prefix/tenant-123/test.txt"
    
    result = s3_exporter.put_bytes(key, data, content_type="text/plain")
    
    assert result is True
    mock_s3_client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key=key,
        Body=data,
        ContentType="text/plain"
    )


def test_s3_exporter_put_json(s3_exporter, mock_s3_client):
    """Тест: загрузка JSON в S3."""
    obj = {"key": "value", "number": 123}
    key = "test-prefix/tenant-123/test.json"
    
    result = s3_exporter.put_json(key, obj)
    
    assert result is True
    call_args = mock_s3_client.put_object.call_args
    assert call_args[1]["Bucket"] == "test-bucket"
    assert call_args[1]["Key"] == key
    assert call_args[1]["ContentType"] == "application/json"
    # Проверяем, что данные - это JSON
    body_data = call_args[1]["Body"]
    import json
    assert json.loads(body_data) == obj


def test_s3_exporter_put_file(s3_exporter, mock_s3_client):
    """Тест: загрузка файла в S3."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False) as f:
        f.write(b"PDF content")
        temp_path = f.name
    
    try:
        key = "test-prefix/tenant-123/test.pdf"
        result = s3_exporter.put_file(key, temp_path)
        
        assert result is True
        call_args = mock_s3_client.put_object.call_args
        assert call_args[1]["Bucket"] == "test-bucket"
        assert call_args[1]["Key"] == key
        assert call_args[1]["ContentType"] == "application/pdf"
        assert call_args[1]["Body"] == b"PDF content"
    finally:
        os.unlink(temp_path)


def test_s3_exporter_enabled(s3_exporter):
    """Тест: проверка enabled."""
    assert s3_exporter.enabled() is True


@pytest.fixture
def services():
    """Создать сервисы для тестов."""
    artifact_service = ArtifactService()
    storage_service = StorageService()
    return artifact_service, storage_service


def test_s3_export_subscriber_disabled(services):
    """Тест: подписчик ничего не делает, если экспорт отключен."""
    artifact_service, storage_service = services
    
    # Создаем exporter с enabled=False (через mock)
    mock_exporter = Mock()
    mock_exporter.enabled.return_value = False
    
    subscriber = S3ExportSubscriber(
        s3_exporter=mock_exporter,
        artifact_service=artifact_service,
        storage_service=storage_service
    )
    
    # Вызываем подписчика
    subscriber("event-id", "artifact.created", "tenant-123", "artifact-id", {}, "2024-01-01T00:00:00")
    
    # Проверяем, что методы экспорта не вызывались
    assert not hasattr(mock_exporter, "put_bytes") or not mock_exporter.put_bytes.called
    assert not hasattr(mock_exporter, "put_json") or not mock_exporter.put_json.called


def test_s3_export_subscriber_artifact_created(services, mock_s3_client):
    """Тест: экспорт PDF при artifact.created для kind=document."""
    artifact_service, storage_service = services
    
    # Создаем mock exporter с передачей mock_s3_client в конструктор
    exporter = S3Exporter(
        endpoint_url="http://127.0.0.1:9000",
        access_key="test_key",
        secret_key="test_secret",
        bucket="test-bucket",
        prefix="test",
        s3_client=mock_s3_client
    )
    
    subscriber = S3ExportSubscriber(
        s3_exporter=exporter,
        artifact_service=artifact_service,
        storage_service=storage_service
    )
    
    # Создаем временный файл
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False) as f:
        f.write(b"PDF content")
        temp_path = f.name
        temp_file_id = Path(temp_path).name
    
    try:
        # Создаем document artifact
        artifact_id = artifact_service.create_artifact(
            kind="document",
            source="test",
            data={"filename": "test.pdf", "file_id": temp_file_id},
            tenant_id="tenant-123"
        )
        
        # Сохраняем файл в storage
        storage_dir = Path(storage_service.base_path)
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_file = storage_dir / temp_file_id
        storage_file.write_bytes(b"PDF content")
        
        # Вызываем подписчика
        subscriber(
            "event-id",
            "artifact.created",
            "tenant-123",
            artifact_id,
            {"kind": "document"},
            "2024-01-01T00:00:00"
        )
        
        # Проверяем, что put_file был вызван
        assert mock_s3_client.put_object.called
        call_args = mock_s3_client.put_object.call_args
        assert "test/tenant-123/documents" in call_args[1]["Key"]
        assert call_args[1]["ContentType"] == "application/pdf"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        if storage_file.exists():
            storage_file.unlink()


def test_s3_export_subscriber_document_extracted(services, mock_s3_client):
    """Тест: экспорт invoice.json при document.extracted."""
    artifact_service, storage_service = services
    
    exporter = S3Exporter(
        endpoint_url="http://127.0.0.1:9000",
        access_key="test_key",
        secret_key="test_secret",
        bucket="test-bucket",
        prefix="test"
    )
    # Используем параметр s3_client в конструкторе вместо присваивания после создания
    # (уже исправлено в fixture s3_exporter)
    
    subscriber = S3ExportSubscriber(
        s3_exporter=exporter,
        artifact_service=artifact_service,
        storage_service=storage_service
    )
    
    # Создаем invoice artifact
    invoice_data = {"total": "1000", "supplier": {"name": "Test"}}
    artifact_id = artifact_service.create_artifact(
        kind="invoice",
        source="doc_agent",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Вызываем подписчика
    subscriber(
        "event-id",
        "document.extracted",
        "tenant-123",
        artifact_id,
        {},
        "2024-01-01T00:00:00"
    )
    
    # Проверяем, что put_object был вызван для JSON
    assert mock_s3_client.put_object.called
    call_args = mock_s3_client.put_object.call_args
    assert "test/tenant-123/invoices" in call_args[1]["Key"]
    assert call_args[1]["ContentType"] == "application/json"


def test_s3_export_subscriber_payment_ready(services, mock_s3_client):
    """Тест: экспорт payment.json при payment.ready."""
    artifact_service, storage_service = services
    
    exporter = S3Exporter(
        endpoint_url="http://127.0.0.1:9000",
        access_key="test_key",
        secret_key="test_secret",
        bucket="test-bucket",
        prefix="test"
    )
    # Используем параметр s3_client в конструкторе вместо присваивания после создания
    # (уже исправлено в fixture s3_exporter)
    
    subscriber = S3ExportSubscriber(
        s3_exporter=exporter,
        artifact_service=artifact_service,
        storage_service=storage_service
    )
    
    # Создаем payment artifact
    payment_data = {"amount_minor": 100000, "currency": "RUB"}
    artifact_id = artifact_service.create_artifact(
        kind="payment",
        source="payment_agent",
        data=payment_data,
        tenant_id="tenant-123"
    )
    
    # Вызываем подписчика
    subscriber(
        "event-id",
        "payment.ready",
        "tenant-123",
        artifact_id,
        {},
        "2024-01-01T00:00:00"
    )
    
    # Проверяем, что put_object был вызван для JSON
    assert mock_s3_client.put_object.called
    call_args = mock_s3_client.put_object.call_args
    assert "test/tenant-123/payments" in call_args[1]["Key"]
    assert call_args[1]["ContentType"] == "application/json"
