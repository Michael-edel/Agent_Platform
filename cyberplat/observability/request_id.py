"""
Request ID middleware для correlation логов и трассировки.

Генерирует или принимает X-Request-ID заголовок и делает его доступным
через contextvars для использования в логах и метриках.
"""

import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Context variable для request_id
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    """
    Получить текущий request_id из context.
    
    Returns:
        Request ID или None, если не установлен
    """
    return _request_id.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware для обработки X-Request-ID заголовка.
    
    - Принимает X-Request-ID из запроса (если есть)
    - Генерирует новый UUID, если заголовок отсутствует
    - Добавляет X-Request-ID в response headers
    - Делает request_id доступным через contextvars
    """
    
    async def dispatch(self, request: Request, call_next):
        # Получаем или генерируем request_id
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Устанавливаем в context
        _request_id.set(request_id)
        
        try:
            # Обрабатываем запрос
            response = await call_next(request)
            
            # Добавляем request_id в response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
        finally:
            # Очищаем context (на всякий случай)
            _request_id.set(None)
