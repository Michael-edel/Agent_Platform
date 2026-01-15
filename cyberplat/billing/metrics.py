"""Prometheus metrics for Billing Worker and billing jobs pipeline.

This module is safe to import in tests:
- Uses idempotent collector creation to avoid duplicated timeseries on reload.
- Provides no-op stubs if prometheus_client is unavailable.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import BillingJob

try:
    from prometheus_client import Counter, Gauge, Histogram, REGISTRY

    _PROM_AVAILABLE = True
except Exception:
    Counter = Gauge = Histogram = None  # type: ignore
    REGISTRY = None  # type: ignore
    _PROM_AVAILABLE = False


def _get_or_create_collector(name: str, factory):
    if not _PROM_AVAILABLE:
        return None
    try:
        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        if existing is not None:
            return existing
    except Exception:
        pass
    return factory()


# Counters
billing_worker_iterations_total = _get_or_create_collector(
    "billing_worker_iterations_total",
    lambda: Counter(
        "billing_worker_iterations_total",
        "Total billing worker iterations",
        ["worker_id"],
    ),
)

billing_jobs_claimed_total = _get_or_create_collector(
    "billing_jobs_claimed_total",
    lambda: Counter(
        "billing_jobs_claimed_total",
        "Total claimed billing jobs",
        ["worker_id"],
    ),
)

billing_jobs_processed_total = _get_or_create_collector(
    "billing_jobs_processed_total",
    lambda: Counter(
        "billing_jobs_processed_total",
        "Total processed billing jobs by result",
        ["worker_id", "result"],  # succeeded|failed|retried|skipped|errored
    ),
)

billing_jobs_retry_scheduled_total = _get_or_create_collector(
    "billing_jobs_retry_scheduled_total",
    lambda: Counter(
        "billing_jobs_retry_scheduled_total",
        "Total retry schedules for billing jobs",
        ["worker_id"],
    ),
)

billing_jobs_provider_refresh_total = _get_or_create_collector(
    "billing_jobs_provider_refresh_total",
    lambda: Counter(
        "billing_jobs_provider_refresh_total",
        "Total provider refresh/reconcile attempts performed by processor",
        ["worker_id", "provider", "result"],  # ok|error|skipped
    ),
)

billing_worker_errors_total = _get_or_create_collector(
    "billing_worker_errors_total",
    lambda: Counter(
        "billing_worker_errors_total",
        "Total billing worker errors",
        ["worker_id", "error_type"],
    ),
)

usage_invoices_payment_status_total = _get_or_create_collector(
    "usage_invoices_payment_status_total",
    lambda: Gauge(
        "usage_invoices_payment_status_total",
        "Current count of usage_invoices by payment_status",
        ["status"],  # unpaid|processing|paid|failed
    ),
)

billing_jobs_failure_reasons_total = _get_or_create_collector(
    "billing_jobs_failure_reasons_total",
    lambda: Counter(
        "billing_jobs_failure_reasons_total",
        "Billing job failure reasons",
        ["provider", "error_code"],
    ),
)

usage_invoice_time_to_paid_seconds = _get_or_create_collector(
    "usage_invoice_time_to_paid_seconds",
    lambda: Histogram(
        "usage_invoice_time_to_paid_seconds",
        "Time from invoice.finalized_at to paid_at in seconds (observed once per invoice)",
        buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 21600),
    ),
)


# Histograms
billing_worker_iteration_duration_seconds = _get_or_create_collector(
    "billing_worker_iteration_duration_seconds",
    lambda: Histogram(
        "billing_worker_iteration_duration_seconds",
        "Billing worker iteration duration in seconds",
        ["worker_id"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    ),
)

billing_job_process_duration_seconds = _get_or_create_collector(
    "billing_job_process_duration_seconds",
    lambda: Histogram(
        "billing_job_process_duration_seconds",
        "Billing job processing duration in seconds",
        ["worker_id", "provider"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    ),
)


# Gauges
billing_jobs_queue_depth = _get_or_create_collector(
    "billing_jobs_queue_depth",
    lambda: Gauge(
        "billing_jobs_queue_depth",
        "Billing jobs queue depth by status",
        ["status"],
    ),
)

billing_jobs_next_attempt_lag_seconds = _get_or_create_collector(
    "billing_jobs_next_attempt_lag_seconds",
    lambda: Gauge(
        "billing_jobs_next_attempt_lag_seconds",
        "Max overdue lag (now - next_attempt_at) for retryable jobs, seconds",
    ),
)

billing_worker_last_success_timestamp = _get_or_create_collector(
    "billing_worker_last_success_timestamp",
    lambda: Gauge(
        "billing_worker_last_success_timestamp",
        "Unix timestamp of last successful iteration",
        ["worker_id"],
    ),
)

billing_worker_up = _get_or_create_collector(
    "billing_worker_up",
    lambda: Gauge(
        "billing_worker_up",
        "Billing worker liveness (1=up)",
        ["worker_id"],
    ),
)


def set_worker_up(worker_id: str, up: bool) -> None:
    if not _PROM_AVAILABLE:
        return
    billing_worker_up.labels(worker_id=worker_id).set(1 if up else 0)


def inc_iteration(worker_id: str) -> None:
    if not _PROM_AVAILABLE:
        return
    billing_worker_iterations_total.labels(worker_id=worker_id).inc()


def observe_iteration(worker_id: str, seconds: float) -> None:
    if not _PROM_AVAILABLE:
        return
    billing_worker_iteration_duration_seconds.labels(worker_id=worker_id).observe(max(0.0, float(seconds)))


def inc_claimed(worker_id: str, count: int) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_jobs_claimed_total.labels(worker_id=worker_id).inc(int(count))


def inc_processed(worker_id: str, result: str, count: int = 1) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_jobs_processed_total.labels(worker_id=worker_id, result=str(result)).inc(int(count))


def inc_retry_scheduled(worker_id: str, count: int) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_jobs_retry_scheduled_total.labels(worker_id=worker_id).inc(int(count))


def inc_provider_refresh(worker_id: str, provider: str, result: str, count: int = 1) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_jobs_provider_refresh_total.labels(
            worker_id=str(worker_id or "unknown"),
            provider=str(provider or "unknown"),
            result=str(result or "unknown"),
        ).inc(int(count))


def inc_worker_error(worker_id: str, error_type: str, count: int = 1) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_worker_errors_total.labels(worker_id=worker_id, error_type=str(error_type)).inc(int(count))


def set_last_success(worker_id: str, ts: Optional[float] = None) -> None:
    if not _PROM_AVAILABLE:
        return
    billing_worker_last_success_timestamp.labels(worker_id=worker_id).set(float(ts if ts is not None else time.time()))


def observe_job_duration(worker_id: str, provider: str, seconds: float) -> None:
    if not _PROM_AVAILABLE:
        return
    billing_job_process_duration_seconds.labels(worker_id=str(worker_id or "unknown"), provider=str(provider or "unknown")).observe(
        max(0.0, float(seconds))
    )


def set_invoice_funnel_counts(counts: Dict[str, int]) -> None:
    if not _PROM_AVAILABLE:
        return
    for st in ["unpaid", "processing", "paid", "failed"]:
        usage_invoices_payment_status_total.labels(status=st).set(int(counts.get(st, 0) or 0))


def observe_invoice_time_to_paid(seconds: float) -> None:
    if not _PROM_AVAILABLE:
        return
    usage_invoice_time_to_paid_seconds.observe(max(0.0, float(seconds)))


def inc_job_failure_reason(provider: str, error_code: str, count: int = 1) -> None:
    if not _PROM_AVAILABLE:
        return
    if int(count) > 0:
        billing_jobs_failure_reasons_total.labels(provider=str(provider or "unknown"), error_code=str(error_code or "unknown")).inc(
            int(count)
        )


def update_queue_gauges(*, now: Optional[datetime] = None) -> None:
    """
    Best-effort gauges update:
    - queue depth by status
    - max overdue lag for retryable jobs (pending_retry/pending) with next_attempt_at in the past
    """
    if not _PROM_AVAILABLE:
        return
    engine = get_engine()
    now_dt = now or datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    statuses = ["pending", "pending_retry", "processing", "failed", "succeeded"]
    depth: Dict[str, int] = {s: 0 for s in statuses}

    with Session(engine) as session:
        rows = session.execute(
            select(BillingJob.status, func.count()).group_by(BillingJob.status)
        ).all()
        for st, cnt in rows:
            if st is None:
                continue
            depth[str(st)] = int(cnt or 0)

        for st in statuses:
            billing_jobs_queue_depth.labels(status=st).set(depth.get(st, 0))

        # Compute max overdue lag for retryable jobs
        lag_max = 0.0
        next_rows = session.execute(
            select(BillingJob.next_attempt_at)
            .where(BillingJob.status.in_(["pending_retry", "pending"]))
            .where(BillingJob.next_attempt_at.is_not(None))
            .limit(2000)
        ).all()
        for (next_at,) in next_rows:
            try:
                dt = datetime.fromisoformat(str(next_at))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt.isoformat() <= now_iso:
                lag = (now_dt - dt).total_seconds()
                if lag > lag_max:
                    lag_max = lag

        billing_jobs_next_attempt_lag_seconds.set(max(0.0, float(lag_max)))

