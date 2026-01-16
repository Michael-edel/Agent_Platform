"""Тесты для OneCClient."""

import pytest
from unittest.mock import Mock, patch
import requests
from requests.exceptions import Timeout, ConnectionError

from cyberplat.integrations.onec_client import (
    OneCClient,
    OneCAuthError,
    OneCTransportError,
    OneCResponseError
)


@pytest.fixture
def client():
    """Создать OneCClient для тестов."""
    return OneCClient(
        base_url="https://1c.example.com/api",
        auth_type="token",
        token="test-token",
        timeout=5,
        max_retries=2
    )


def test_request_success(client):
    """Тест: успешный запрос возвращает JSON."""
    with patch('requests.request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123", "name": "Test"}
        mock_response.content = b'{"id":"123","name":"Test"}'
        mock_request.return_value = mock_response
        
        result = client.request("POST", "/counterparties", json_data={"name": "Test"})
        
        assert result == {"id": "123", "name": "Test"}
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert "X-Correlation-Id" in call_kwargs["headers"]


def test_request_with_idempotency_key(client):
    """Тест: запрос с idempotency_key добавляет заголовок."""
    with patch('requests.request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.content = b'{}'
        mock_request.return_value = mock_response
        
        client.request("POST", "/counterparties", json_data={}, idempotency_key="key-123")
        
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["headers"]["X-Idempotency-Key"] == "key-123"


def test_request_auth_error_401(client):
    """Тест: 401 ошибка выбрасывает OneCAuthError."""
    with patch('requests.request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_request.return_value = mock_response
        
        with pytest.raises(OneCAuthError) as exc_info:
            client.request("POST", "/counterparties", json_data={})
        
        assert "аутентификации" in str(exc_info.value).lower() or "401" in str(exc_info.value)


def test_request_response_error_400(client):
    """Тест: 400 ошибка выбрасывает OneCResponseError без retry."""
    with patch('requests.request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.json.return_value = {"message": "Invalid data"}
        mock_request.return_value = mock_response
        
        with pytest.raises(OneCResponseError) as exc_info:
            client.request("POST", "/counterparties", json_data={})
        
        assert exc_info.value.status_code == 400
        # Проверяем, что не было retry (только один вызов)
        assert mock_request.call_count == 1


def test_request_500_retry(client):
    """Тест: 500 ошибка вызывает retry."""
    with patch('requests.request') as mock_request:
        with patch('time.sleep'):  # Мокаем sleep для скорости
            # Первые два вызова возвращают 500, третий - успех
            mock_response_500 = Mock()
            mock_response_500.status_code = 500
            mock_response_500.text = "Internal Server Error"
            
            mock_response_200 = Mock()
            mock_response_200.status_code = 200
            mock_response_200.json.return_value = {"id": "123"}
            mock_response_200.content = b'{"id":"123"}'
            
            mock_request.side_effect = [mock_response_500, mock_response_500, mock_response_200]
            
            result = client.request("POST", "/counterparties", json_data={})
            
            assert result == {"id": "123"}
            # Должно быть 3 вызова (2 retry + 1 успешный)
            assert mock_request.call_count == 3


def test_request_timeout_retry(client):
    """Тест: Timeout вызывает retry."""
    with patch('requests.request') as mock_request:
        with patch('time.sleep'):  # Мокаем sleep
            # Первый вызов - timeout, второй - успех
            mock_request.side_effect = [Timeout("Connection timeout"), Mock(status_code=200, json=lambda: {}, content=b'{}')]
            
            result = client.request("POST", "/counterparties", json_data={})
            
            assert mock_request.call_count == 2


def test_request_timeout_max_retries(client):
    """Тест: Timeout после max_retries выбрасывает OneCTransportError."""
    with patch('requests.request') as mock_request:
        with patch('time.sleep'):  # Мокаем sleep
            mock_request.side_effect = Timeout("Connection timeout")
            
            with pytest.raises(OneCTransportError) as exc_info:
                client.request("POST", "/counterparties", json_data={})
            
            assert "транспорта" in str(exc_info.value).lower() or "transport" in str(exc_info.value).lower()
            # Должно быть max_retries + 1 попыток
            assert mock_request.call_count == client.max_retries + 1


def test_basic_auth(client):
    """Тест: basic auth использует правильный заголовок."""
    basic_client = OneCClient(
        base_url="https://1c.example.com/api",
        auth_type="basic",
        username="user1",
        password="pass123"
    )
    
    with patch('requests.request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.content = b'{}'
        mock_request.return_value = mock_response
        
        basic_client.request("GET", "/ping")
        
        call_kwargs = mock_request.call_args[1]
        auth_header = call_kwargs["headers"]["Authorization"]
        assert auth_header.startswith("Basic ")


def test_ping_success(client):
    """Тест: ping возвращает True при успехе."""
    with patch.object(client, 'request') as mock_request:
        mock_request.return_value = {}
        assert client.ping() is True


def test_ping_failure(client):
    """Тест: ping возвращает False при ошибке."""
    with patch.object(client, 'request') as mock_request:
        mock_request.side_effect = OneCTransportError("Connection failed")
        assert client.ping() is False


def test_create_counterparty(client):
    """Тест: create_counterparty вызывает правильный endpoint."""
    with patch.object(client, 'request') as mock_request:
        mock_request.return_value = {"id": "123"}
        
        result = client.create_counterparty({"name": "Test"}, idempotency_key="key-123")
        
        assert result == {"id": "123"}
        mock_request.assert_called_once_with(
            "POST",
            "/counterparties",
            json_data={"name": "Test"},
            idempotency_key="key-123",
            correlation_id=None
        )
