"""
Структурированное логирование для production.

Поддерживает:
- JSON формат для production (структурированные логи)
- Pretty формат для development (человекочитаемый)
- Автоматическое добавление request_id, tenant_id, event_id
- Фильтрация секретов (Stripe keys, tokens, etc.)
"""

import json
import logging
import os
import re
import sys
from typing import Any, Dict, Optional

from cyberplat.observability.request_id import get_request_id


class StructuredFormatter(logging.Formatter):
    """JSON formatter для структурированных логов."""
    
    # Паттерны для секретов (не логируем)
    SECRET_PATTERNS = [
        (re.compile(r'sk_live_[a-zA-Z0-9]{24,}'), 'sk_live_***'),
        (re.compile(r'sk_test_[a-zA-Z0-9]{24,}'), 'sk_test_***'),
        (re.compile(r'whsec_[a-zA-Z0-9]{24,}'), 'whsec_***'),
        (re.compile(r'pk_live_[a-zA-Z0-9]{24,}'), 'pk_live_***'),
        (re.compile(r'pk_test_[a-zA-Z0-9]{24,}'), 'pk_test_***'),
        (re.compile(r'AKIA[0-9A-Z]{16}'), 'AKIA***'),
        (re.compile(r'Bearer [a-zA-Z0-9\-_]{20,}'), 'Bearer ***'),
        (re.compile(r'api[_-]?key["\s:=]+([a-zA-Z0-9\-_]{20,})', re.IGNORECASE), 'api_key=***'),
        (re.compile(r'secret["\s:=]+([a-zA-Z0-9\-_]{20,})', re.IGNORECASE), 'secret=***'),
        (re.compile(r'token["\s:=]+([a-zA-Z0-9\-_]{20,})', re.IGNORECASE), 'token=***'),
        (re.compile(r'password["\s:=]+([^\s"\']{8,})', re.IGNORECASE), 'password=***'),
    ]
    
    def _sanitize_message(self, message: str) -> str:
        """Удаляет секреты из сообщения."""
        sanitized = message
        for pattern, replacement in self.SECRET_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized
    
    def format(self, record: logging.LogRecord) -> str:
        """Форматирует запись лога в JSON."""
        # Базовые поля
        log_data: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": self._sanitize_message(record.getMessage()),
        }
        
        # Request ID (если доступен)
        request_id = get_request_id()
        if request_id:
            log_data["request_id"] = request_id
        
        # Tenant ID (если есть в extra)
        if hasattr(record, "tenant_id") and record.tenant_id:
            log_data["tenant_id"] = record.tenant_id
        
        # Event ID (если есть в extra)
        if hasattr(record, "event_id") and record.event_id:
            log_data["event_id"] = record.event_id
        
        # Exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Дополнительные поля из extra
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info",
                "tenant_id", "event_id",  # уже обработаны
            }:
                # Пропускаем внутренние поля logging
                if not key.startswith("_"):
                    log_data[key] = value
        
        return json.dumps(log_data, ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    """Человекочитаемый formatter для development."""
    
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s [%(levelname)8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    def format(self, record: logging.LogRecord) -> str:
        """Форматирует запись с дополнительными полями."""
        # Добавляем request_id в сообщение, если есть
        request_id = get_request_id()
        if request_id:
            record.msg = f"[req:{request_id[:8]}] {record.msg}"
        
        # Добавляем tenant_id, если есть
        if hasattr(record, "tenant_id") and record.tenant_id:
            record.msg = f"[tenant:{record.tenant_id}] {record.msg}"
        
        return super().format(record)


def setup_structured_logging(
    level: str = "INFO",
    format_type: str = "json",
    log_file: Optional[str] = None
) -> None:
    """
    Настраивает структурированное логирование.
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Формат логов ("json" или "pretty")
        log_file: Опциональный файл для логов
    """
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)
    
    # Выбираем formatter
    if format_type.lower() == "json":
        formatter = StructuredFormatter()
    else:
        formatter = PrettyFormatter()
    
    # Настраиваем handlers
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    
    # Применяем formatter ко всем handlers
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
    
    # Настраиваем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = handlers


def get_logger(name: str) -> logging.Logger:
    """
    Получить logger с поддержкой структурированного логирования.
    
    Args:
        name: Имя logger (обычно __name__)
    
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
