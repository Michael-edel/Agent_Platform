"""
Prometheus метрики для observability.

Метрики:
- HTTP requests (total, duration)
- Billing webhook events
- Recurring billing runs

METRICS_ENABLED=false отключает метрики (для тестов без prometheus_client).
"""

import os
from typing import Optional

def _metrics_enabled_env() -> bool:
    raw = os.getenv("METRICS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes"}


# Check if metrics are enabled (default: true)
METRICS_ENABLED = _metrics_enabled_env()

# Conditional import of prometheus_client
# Сначала пытаемся импортировать внешний prometheus_client,
# если не получается - используем встроенный shim из prometheus_client/
if METRICS_ENABLED:
    try:
        from prometheus_client import (
            Counter,
            Histogram,
            Gauge,
            REGISTRY,
            generate_latest,
            CONTENT_TYPE_LATEST,
        )
        _prometheus_available = True
    except ImportError:
        # Используем встроенный shim
        try:
            import importlib.util
            import sys
            from pathlib import Path
            
            # Путь к нашему shim-модулю
            shim_path = Path(__file__).parent.parent.parent / "prometheus_client" / "__init__.py"
            if shim_path.exists():
                spec = importlib.util.spec_from_file_location("prometheus_client", shim_path)
                prometheus_module = importlib.util.module_from_spec(spec)
                prometheus_module._is_shim = True  # Маркер, что это shim
                sys.modules["prometheus_client"] = prometheus_module
                spec.loader.exec_module(prometheus_module)
                
                from prometheus_client import (
                    Counter,
                    Histogram,
                    Gauge,
                    REGISTRY,
                    generate_latest,
                    CONTENT_TYPE_LATEST,
                )
                _prometheus_available = True
            else:
                _prometheus_available = False
                METRICS_ENABLED = False
        except Exception:
            _prometheus_available = False
            METRICS_ENABLED = False
else:
    _prometheus_available = False

from starlette.responses import Response

# Registry для метрик
_metrics_registry = REGISTRY if _prometheus_available else None


# HTTP метрики (only if prometheus available)
if _prometheus_available:
    def _get_or_create_collector(name: str, factory):
        try:
            existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
            if existing is not None:
                return existing
        except Exception:
            pass
        return factory()

    http_requests_total = _get_or_create_collector(
        "http_requests_total",
        lambda: Counter(
            "http_requests_total",
            "Total number of HTTP requests",
            ["method", "path", "status"],
        ),
    )

    http_request_duration_seconds = _get_or_create_collector(
        "http_request_duration_seconds",
        lambda: Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path", "status"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        ),
    )

    # Billing метрики
    billing_webhook_events_total = _get_or_create_collector(
        "billing_webhook_events_total",
        lambda: Counter(
            "billing_webhook_events_total",
            "Total number of billing webhook events",
            ["provider", "event_type", "status"],  # status: ok, invalid, duplicate, error
        ),
    )

    # Recurring billing метрики
    recurring_runs_total = _get_or_create_collector(
        "recurring_runs_total",
        lambda: Counter(
            "recurring_runs_total",
            "Total number of recurring billing runs",
            ["provider", "status"],  # status: success, failed, skipped
        ),
    )

    # Integration метрики
    onec_job_outcomes_total = _get_or_create_collector(
        "onec_job_outcomes_total",
        lambda: Counter(
            "onec_job_outcomes_total",
            "Total number of 1C integration job outcomes",
            ["status", "job_type"],  # status: succeeded, failed, retried
        ),
    )

    onec_job_latency_seconds = _get_or_create_collector(
        "onec_job_latency_seconds",
        lambda: Histogram(
            "onec_job_latency_seconds",
            "1C integration job processing latency in seconds",
            ["job_type"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
        ),
    )

    onec_failures_total = _get_or_create_collector(
        "onec_failures_total",
        lambda: Counter(
            "onec_failures_total",
            "Total number of 1C integration failures",
            ["error_code"],
        ),
    )

    onec_auto_jobs_total = _get_or_create_collector(
        "onec_auto_jobs_total",
        lambda: Counter(
            "onec_auto_jobs_total",
            "Total number of auto-created 1C jobs from artifacts",
            ["object_type"],
        ),
    )

    onec_manual_jobs_total = _get_or_create_collector(
        "onec_manual_jobs_total",
        lambda: Counter(
            "onec_manual_jobs_total",
            "Total number of manually created 1C jobs",
            ["object_type"],
        ),
    )

    onec_artifact_hook_errors_total = _get_or_create_collector(
        "onec_artifact_hook_errors_total",
        lambda: Counter(
            "onec_artifact_hook_errors_total",
            "Total number of errors in 1C artifact hook",
            ["error_code"],
        ),
    )

    # Payment метрики
    payment_orders_total = _get_or_create_collector(
        "payment_orders_total",
        lambda: Counter(
            "payment_orders_total",
            "Total number of payment orders",
            ["status"],
        ),
    )

    payment_approval_latency_seconds = _get_or_create_collector(
        "payment_approval_latency_seconds",
        lambda: Histogram(
            "payment_approval_latency_seconds",
            "Payment approval latency in seconds",
            [],
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0, 3600.0),
        ),
    )

    payment_export_total = _get_or_create_collector(
        "payment_export_total",
        lambda: Counter(
            "payment_export_total",
            "Total number of payment exports",
            ["format", "status"],  # status: succeeded, failed
        ),
    )

    # Reconciliation метрики
    reconciliation_transactions_total = _get_or_create_collector(
        "reconciliation_transactions_total",
        lambda: Counter(
            "reconciliation_transactions_total",
            "Total number of bank transactions",
            ["matched"],  # matched: true, false
        ),
    )

    reconciliation_auto_match_rate = _get_or_create_collector(
        "reconciliation_auto_match_rate",
        lambda: Histogram(
            "reconciliation_auto_match_rate",
            "Auto match rate (0..1)",
            [],
            buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
        ),
    )

    reconciliation_latency_seconds = _get_or_create_collector(
        "reconciliation_latency_seconds",
        lambda: Histogram(
            "reconciliation_latency_seconds",
            "Reconciliation processing latency in seconds",
            [],
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0),
        ),
    )

    reconciliation_failures_total = _get_or_create_collector(
        "reconciliation_failures_total",
        lambda: Counter(
            "reconciliation_failures_total",
            "Total number of reconciliation failures",
            ["error_code"],
        ),
    )

    # Money Ops backlog метрики
    money_ops_queue_backlog = _get_or_create_collector(
        "money_ops_queue_backlog",
        lambda: Gauge(
            "money_ops_queue_backlog",
            "Backlog depth for money ops queues",
            ["queue"],  # queue: onec_jobs, payment_approvals, reconciliation
        ),
    )

    payment_orders_backlog = _get_or_create_collector(
        "payment_orders_backlog",
        lambda: Gauge(
            "payment_orders_backlog",
            "Number of payment orders in backlog",
            ["status"],  # status: pending_approval, etc.
        ),
    )

    reconciliation_unmatched_total = _get_or_create_collector(
        "reconciliation_unmatched_total",
        lambda: Gauge(
            "reconciliation_unmatched_total",
            "Number of unmatched reconciliation transactions",
            [],
        ),
    )

    # Circuit breaker метрики
    onec_circuit_open_total = _get_or_create_collector(
        "onec_circuit_open_total",
        lambda: Counter(
            "onec_circuit_open_total",
            "Total number of times 1C circuit breaker opened",
            [],
        ),
    )

    recurring_duration_seconds = _get_or_create_collector(
        "recurring_duration_seconds",
        lambda: Histogram(
            "recurring_duration_seconds",
            "Recurring billing run duration in seconds",
            ["provider"],
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
        ),
    )
else:
    # Stub objects when metrics disabled
    http_requests_total = None
    http_request_duration_seconds = None
    billing_webhook_events_total = None
    recurring_runs_total = None
    recurring_duration_seconds = None
    onec_job_outcomes_total = None
    onec_job_latency_seconds = None
    onec_failures_total = None
    onec_auto_jobs_total = None
    onec_manual_jobs_total = None
    onec_artifact_hook_errors_total = None
    payment_orders_total = None
    payment_approval_latency_seconds = None
    payment_export_total = None
    reconciliation_transactions_total = None
    reconciliation_auto_match_rate = None
    reconciliation_latency_seconds = None
    reconciliation_failures_total = None
    money_ops_queue_backlog = None
    payment_orders_backlog = None
    reconciliation_unmatched_total = None
    onec_circuit_open_total = None


def setup_metrics(enabled: bool = True) -> bool:
    """
    Настраивает метрики.
    
    Args:
        enabled: Включены ли метрики
    
    Returns:
        True если метрики включены и доступны, False иначе
    """
    return enabled and _prometheus_available


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
    if not _prometheus_available:
        return
        
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
    if not _prometheus_available:
        return
        
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
    if not _prometheus_available:
        return
        
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
    if not _prometheus_available:
        return Response(
            content="# Metrics disabled\n",
            media_type="text/plain",
        )
        
    return Response(
        content=generate_latest(_metrics_registry),
        media_type=CONTENT_TYPE_LATEST,
    )
