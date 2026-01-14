"""
Prometheus метрики для observability.

Метрики:
- HTTP requests (total, duration)
- Billing webhook events
- Recurring billing runs
"""

import os
from typing import Optional

from prometheus_client import (
    Counter,
    Histogram,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.responses import Response

# Registry для метрик
_metrics_registry = REGISTRY


# HTTP метрики
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Billing метрики
billing_webhook_events_total = Counter(
    "billing_webhook_events_total",
    "Total number of billing webhook events",
    ["provider", "event_type", "status"],  # status: ok, invalid, duplicate, error
)

# Recurring billing метрики
recurring_runs_total = Counter(
    "recurring_runs_total",
    "Total number of recurring billing runs",
    ["provider", "status"],  # status: success, failed, skipped
)

recurring_duration_seconds = Histogram(
    "recurring_duration_seconds",
    "Recurring billing run duration in seconds",
    ["provider"],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)


def setup_metrics(enabled: bool = True) -> bool:
    """
    Настраивает метрики.
    
    Args:
        enabled: Включены ли метрики
    
    Returns:
        True если метрики включены, False иначе
    """
    return enabled


def get_metrics_registry():
    """Получить Prometheus registry."""
    return _metrics_registry


def record_http_request(method: str, path: str, status_code: int, duration: float):
    """
    Записать метрику HTTP запроса.
    
    Args:
        method: HTTP метод (GET, POST, etc.)
        path: Путь запроса (нормализованный)
        status_code: HTTP статус код
        duration: Длительность запроса в секундах
    """
    # Нормализуем path (убираем tenant_id и другие динамические части)
    normalized_path = _normalize_path(path)
    
    status = str(status_code)
    http_requests_total.labels(method=method, path=normalized_path, status=status).inc()
    http_request_duration_seconds.labels(
        method=method, path=normalized_path, status=status
    ).observe(duration)


def record_webhook_event(provider: str, event_type: str, status: str):
    """
    Записать метрику webhook события.
    
    Args:
        provider: Провайдер (stripe, kaspi)
        event_type: Тип события (checkout.completed, invoice.paid, etc.)
        status: Статус обработки (ok, invalid, duplicate, error)
    """
    billing_webhook_events_total.labels(
        provider=provider, event_type=event_type, status=status
    ).inc()


def record_recurring_run(provider: str, status: str, duration: Optional[float] = None):
    """
    Записать метрику recurring billing run.
    
    Args:
        provider: Провайдер (stripe, kaspi)
        status: Статус (success, failed, skipped)
        duration: Длительность в секундах (опционально)
    """
    recurring_runs_total.labels(provider=provider, status=status).inc()
    if duration is not None:
        recurring_duration_seconds.labels(provider=provider).observe(duration)


def _normalize_path(path: str) -> str:
    """
    Нормализует путь для метрик (убирает динамические части).
    
    Примеры:
        /api/v1/artifacts/123 -> /api/v1/artifacts/{id}
        /api/v1/billing/webhook/stripe -> /api/v1/billing/webhook/stripe
    """
    # Убираем UUID и длинные hex строки
    import re
    
    # UUID pattern
    path = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '{id}',
        path,
        flags=re.IGNORECASE,
    )
    
    # Long hex strings (32+ chars)
    path = re.sub(r'[0-9a-f]{32,}', '{id}', path, flags=re.IGNORECASE)
    
    # Numbers at the end (likely IDs)
    path = re.sub(r'/\d+$', '/{id}', path)
    
    return path


def metrics_endpoint() -> Response:
    """
    Endpoint для Prometheus метрик.
    
    Returns:
        Response с метриками в Prometheus exposition format
    """
    return Response(
        content=generate_latest(_metrics_registry),
        media_type=CONTENT_TYPE_LATEST,
    )
