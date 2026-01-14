"""
Observability модуль для Agent Platform / CyberPlat.

Включает:
- Структурированное логирование
- Request correlation (request_id)
- Prometheus метрики
- Health/Ready endpoints
"""

from cyberplat.observability.logging import setup_structured_logging
from cyberplat.observability.request_id import RequestIDMiddleware, get_request_id
from cyberplat.observability.metrics import setup_metrics, get_metrics_registry

__all__ = [
    "setup_structured_logging",
    "RequestIDMiddleware",
    "get_request_id",
    "setup_metrics",
    "get_metrics_registry",
]
