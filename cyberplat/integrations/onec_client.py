"""HTTP-клиент для интеграции с 1С (production-ready)."""

import logging
import time
import random
import uuid
from typing import Dict, Any, Optional
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

logger = logging.getLogger(__name__)


class OneCAuthError(Exception):
    """Ошибка аутентификации в 1С."""
    pass


class OneCTransportError(Exception):
    """Ошибка транспорта (сеть, таймаут)."""
    pass


class OneCResponseError(Exception):
    """Ошибка ответа 1С (non-2xx)."""
    def __init__(self, status_code: int, message: str, response_text: Optional[str] = None):
        self.status_code = status_code
        self.message = message
        self.response_text = response_text
        super().__init__(f"1C API error {status_code}: {message}")


class OneCClient:
    """Клиент для работы с API 1С."""
    
    def __init__(
        self,
        base_url: str,
        auth_type: str = "token",
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 10,
        max_retries: int = 3
    ):
        """
        Инициализировать клиент 1С.
        
        Args:
            base_url: Базовый URL API 1С
            auth_type: Тип аутентификации ('token' или 'basic')
            token: Токен для аутентификации (если auth_type='token')
            username: Имя пользователя (если auth_type='basic')
            password: Пароль (если auth_type='basic')
            timeout: Таймаут запросов в секундах
            max_retries: Максимальное количество повторов при ошибках
        """
        self.base_url = (base_url or "").rstrip("/")
        self.auth_type = auth_type
        self.token = token
        self.username = username
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries
    
    def _headers(self, idempotency_key: Optional[str] = None, correlation_id: Optional[str] = None) -> Dict[str, str]:
        """Сформировать заголовки запроса."""
        h = {"Content-Type": "application/json"}
        
        # Аутентификация
        if self.auth_type == "token" and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        elif self.auth_type == "basic" and self.username and self.password:
            import base64
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            h["Authorization"] = f"Basic {credentials}"
        
        # Idempotency
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        
        # Correlation ID
        if correlation_id:
            h["X-Correlation-Id"] = correlation_id
        else:
            h["X-Correlation-Id"] = str(uuid.uuid4())
        
        return h
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Рассчитать время задержки для retry (exponential backoff + jitter)."""
        base = 1.0
        max_delay = 10.0
        delay = min(base * (2 ** attempt), max_delay)
        # Jitter ±20%
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)
    
    def request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Выполнить HTTP запрос к API 1С с retry и обработкой ошибок.
        
        Args:
            method: HTTP метод ('GET', 'POST', 'PUT', 'DELETE')
            path: Путь относительно base_url
            json_data: Тело запроса (JSON)
            idempotency_key: Ключ идемпотентности
            correlation_id: ID для корреляции запросов
            
        Returns:
            Ответ API (JSON)
            
        Raises:
            OneCAuthError: Ошибка аутентификации (401)
            OneCTransportError: Ошибка транспорта (сеть, таймаут)
            OneCResponseError: Ошибка ответа (non-2xx)
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._headers(idempotency_key=idempotency_key, correlation_id=correlation_id)
        
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    json=json_data,
                    headers=headers,
                    timeout=self.timeout
                )
                
                # Успешный ответ
                if 200 <= response.status_code < 300:
                    return response.json() if response.content else {}
                
                # Ошибка аутентификации
                if response.status_code == 401:
                    raise OneCAuthError("Ошибка аутентификации в 1С (401)")
                
                # Другие ошибки ответа
                error_msg = f"1C API вернул статус {response.status_code}"
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict) and "message" in error_data:
                        error_msg = error_data["message"]
                except Exception:
                    pass
                
                # Если это последняя попытка, выбрасываем ошибку
                if attempt == self.max_retries:
                    raise OneCResponseError(
                        status_code=response.status_code,
                        message=error_msg,
                        response_text=response.text[:500]  # Ограничиваем длину
                    )
                
                # Retry для 5xx ошибок
                if 500 <= response.status_code < 600:
                    backoff = self._calculate_backoff(attempt)
                    logger.warning(f"1C API вернул {response.status_code}, retry через {backoff:.2f}s (попытка {attempt + 1}/{self.max_retries + 1})")
                    time.sleep(backoff)
                    continue
                
                # Для других ошибок не делаем retry
                raise OneCResponseError(
                    status_code=response.status_code,
                    message=error_msg,
                    response_text=response.text[:500]
                )
                
            except (Timeout, ConnectionError) as e:
                last_exception = e
                if attempt == self.max_retries:
                    raise OneCTransportError(f"Ошибка транспорта при запросе к 1С: {str(e)}")
                
                backoff = self._calculate_backoff(attempt)
                logger.warning(f"Ошибка транспорта, retry через {backoff:.2f}s (попытка {attempt + 1}/{self.max_retries + 1}): {e}")
                time.sleep(backoff)
                
            except (OneCAuthError, OneCResponseError):
                # Эти ошибки не требуют retry
                raise
            
            except RequestException as e:
                last_exception = e
                if attempt == self.max_retries:
                    raise OneCTransportError(f"Ошибка запроса к 1С: {str(e)}")
                
                backoff = self._calculate_backoff(attempt)
                logger.warning(f"Ошибка запроса, retry через {backoff:.2f}s (попытка {attempt + 1}/{self.max_retries + 1}): {e}")
                time.sleep(backoff)
        
        # Не должно доходить сюда, но на всякий случай
        if last_exception:
            raise OneCTransportError(f"Ошибка транспорта после {self.max_retries + 1} попыток: {str(last_exception)}")
        raise OneCTransportError("Неизвестная ошибка при запросе к 1С")
    
    def ping(self) -> bool:
        """
        Проверить доступность API 1С.
        
        Returns:
            True если API доступен
        """
        try:
            self.request("GET", "/ping")
            return True
        except Exception as e:
            logger.warning(f"1C ping failed: {e}")
            return False
    
    def create_counterparty(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Создать контрагента в 1С."""
        return self.request("POST", "/counterparties", json_data=payload, idempotency_key=idempotency_key)
    
    def create_contract(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Создать договор в 1С."""
        return self.request("POST", "/contracts", json_data=payload, idempotency_key=idempotency_key)
    
    def create_invoice(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Создать счёт в 1С."""
        return self.request("POST", "/invoices", json_data=payload, idempotency_key=idempotency_key)
