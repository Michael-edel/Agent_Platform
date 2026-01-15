"""Agent execution Prometheus metrics (lazy init, test-safe)."""

from __future__ import annotations

import os
from typing import Optional


def _metrics_enabled() -> bool:
    raw = os.getenv("METRICS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes"}


_completed_total = None
_failed_total = None
_prom_available: Optional[bool] = None


def _ensure_metrics():
    global _completed_total, _failed_total, _prom_available
    if _prom_available is not None:
        return
    if not _metrics_enabled():
        _prom_available = False
        return
    try:
        from prometheus_client import Counter, REGISTRY  # noqa: WPS433
    except Exception:
        _prom_available = False
        return

    def get_or_create_counter(name: str, doc: str, labelnames: list[str]):
        try:
            existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
            if existing is not None:
                return existing
        except Exception:
            pass
        return Counter(name, doc, labelnames)

    _completed_total = get_or_create_counter(
        "agent_execution_completed_total",
        "Total number of completed agent executions",
        ["agent_code"],
    )
    _failed_total = get_or_create_counter(
        "agent_execution_failed_total",
        "Total number of failed agent executions",
        ["agent_code", "error_code"],
    )
    _prom_available = True


def inc_completed(agent_code: str) -> None:
    _ensure_metrics()
    if not _prom_available:
        return
    _completed_total.labels(agent_code=agent_code).inc()


def inc_failed(agent_code: str, error_code: str) -> None:
    _ensure_metrics()
    if not _prom_available:
        return
    _failed_total.labels(agent_code=agent_code, error_code=error_code).inc()

