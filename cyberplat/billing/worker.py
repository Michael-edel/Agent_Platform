"""Dedicated billing worker (separate process).

Runs polling loop calling billing jobs processor and exits gracefully on SIGTERM/SIGINT.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from threading import Event
from typing import Any, Dict, Optional, Tuple

from cyberplat.billing.jobs import process_due_billing_jobs_stats
from cyberplat.product.infrastructure.database import get_engine
from cyberplat.billing import metrics as billing_metrics

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _worker_id() -> str:
    return os.getenv("BILLING_WORKER_ID", "").strip() or os.getenv("HOSTNAME", "").strip() or socket.gethostname()


def _log_json(level: str, payload: Dict[str, Any]) -> None:
    msg = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)


def single_iteration(*, batch_size: int, worker_id: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Runs one polling iteration. Never raises; returns (ok, stats/error_payload).
    """
    t0 = time.monotonic()
    billing_metrics.inc_iteration(worker_id)
    try:
        stats = process_due_billing_jobs_stats(limit=batch_size, worker_id=worker_id)
        duration_ms = int((time.monotonic() - t0) * 1000)
        out: Dict[str, Any] = {
            "processed_count": int(stats.get("processed_count", 0) or 0),
            "succeeded_count": int(stats.get("succeeded_count", 0) or 0),
            "failed_count": int(stats.get("failed_count", 0) or 0),
            "retried_count": int(stats.get("retried_count", 0) or 0),
            "skipped_count": int(stats.get("skipped_count", 0) or 0),
            "duration_ms": duration_ms,
        }
        billing_metrics.observe_iteration(worker_id, duration_ms / 1000.0)
        billing_metrics.inc_claimed(worker_id, out["processed_count"])
        billing_metrics.inc_processed(worker_id, "succeeded", out["succeeded_count"])
        billing_metrics.inc_processed(worker_id, "failed", out["failed_count"])
        billing_metrics.inc_processed(worker_id, "retried", out["retried_count"])
        billing_metrics.inc_retry_scheduled(worker_id, out["retried_count"])
        billing_metrics.inc_processed(worker_id, "skipped", out["skipped_count"])
        billing_metrics.set_last_success(worker_id)
        return True, out
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        billing_metrics.observe_iteration(worker_id, duration_ms / 1000.0)
        billing_metrics.inc_processed(worker_id, "errored", 1)
        billing_metrics.inc_worker_error(worker_id, type(e).__name__, 1)
        return (
            False,
            {
                "error_type": type(e).__name__,
                "error_message": str(e)[:200],
                "duration_ms": duration_ms,
            },
        )


def _preflight_or_exit() -> None:
    """
    Ensure DB is usable. Exit code 1 on critical misconfig.
    """
    engine = get_engine()
    database_url_raw = os.getenv("DATABASE_URL", "").strip()
    if engine.dialect.name != "sqlite" and not database_url_raw:
        _log_json(
            "error",
            {
                "event": "billing_worker_error",
                "error_type": "RuntimeError",
                "error_message": "DATABASE_URL is missing for non-sqlite engine",
            },
        )
        raise SystemExit(1)


def run_loop(*, stop_event: Event, interval_seconds: float, batch_size: int, worker_id: str, jitter_seconds: float) -> None:
    """
    Main polling loop. Stops quickly when stop_event is set.
    """
    iter_n = 0
    queue_every = max(1, _int_env("BILLING_WORKER_QUEUE_METRICS_EVERY_N_ITERATIONS", 12))

    while not stop_event.is_set():
        iter_n += 1
        ok, payload = single_iteration(batch_size=batch_size, worker_id=worker_id)
        if ok:
            # Optional small jitter to avoid thundering herd (additive, simple and deterministic).
            sleep_s = max(0.0, float(interval_seconds)) + (float(jitter_seconds) if jitter_seconds > 0 else 0.0)
            _log_json(
                "info",
                {
                    "event": "billing_worker_iteration",
                    "worker_id": worker_id,
                    **payload,
                    "next_sleep_seconds": sleep_s,
                },
            )
        else:
            sleep_s = max(0.0, float(interval_seconds))
            _log_json(
                "error",
                {
                    "event": "billing_worker_error",
                    "worker_id": worker_id,
                    **payload,
                    "next_sleep_seconds": sleep_s,
                },
            )

        # Interruptible sleep (wake up immediately on stop)
        stop_event.wait(timeout=sleep_s)

        # Update queue gauges periodically (best-effort)
        if iter_n % queue_every == 0 and not stop_event.is_set():
            try:
                billing_metrics.update_queue_gauges()
            except Exception as e:
                billing_metrics.inc_worker_error(worker_id, f"queue_metrics_{type(e).__name__}", 1)
                _log_json(
                    "error",
                    {
                        "event": "billing_worker_error",
                        "worker_id": worker_id,
                        "error_type": type(e).__name__,
                        "error_message": str(e)[:200],
                    },
                )


def main() -> None:
    enabled = _bool_env("BILLING_WORKER_ENABLED", True)
    if not enabled:
        _log_json("info", {"event": "billing_worker_disabled"})
        return

    interval_seconds = float(_int_env("BILLING_WORKER_INTERVAL_SECONDS", 5))
    batch_size = int(_int_env("BILLING_WORKER_BATCH_SIZE", 50))
    jitter_seconds = float(_int_env("BILLING_WORKER_JITTER_SECONDS", 0))
    worker_id = _worker_id()

    _preflight_or_exit()

    stop_event = Event()

    def _handle_signal(signum, _frame=None):  # type: ignore[no-untyped-def]
        _log_json("info", {"event": "billing_worker_shutdown_requested", "signal": int(signum), "worker_id": worker_id})
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Metrics server (optional)
    metrics_enabled = _bool_env("BILLING_WORKER_METRICS_ENABLED", True)
    metrics_host = os.getenv("BILLING_WORKER_METRICS_HOST", "0.0.0.0").strip() or "0.0.0.0"
    metrics_port = _int_env("BILLING_WORKER_METRICS_PORT", 9101)
    if metrics_enabled:
        try:
            from prometheus_client import start_http_server

            start_http_server(int(metrics_port), addr=str(metrics_host))
            _log_json(
                "info",
                {
                    "event": "billing_worker_metrics_started",
                    "worker_id": worker_id,
                    "host": metrics_host,
                    "port": int(metrics_port),
                },
            )
        except Exception as e:
            _log_json(
                "error",
                {
                    "event": "billing_worker_error",
                    "worker_id": worker_id,
                    "error_type": type(e).__name__,
                    "error_message": f"Failed to start metrics server: {str(e)[:160]}",
                },
            )

    billing_metrics.set_worker_up(worker_id, True)

    _log_json(
        "info",
        {
            "event": "billing_worker_started",
            "worker_id": worker_id,
            "interval_seconds": interval_seconds,
            "batch_size": batch_size,
            "jitter_seconds": jitter_seconds,
            "metrics_enabled": bool(metrics_enabled),
        },
    )

    try:
        run_loop(
            stop_event=stop_event,
            interval_seconds=interval_seconds,
            batch_size=batch_size,
            worker_id=worker_id,
            jitter_seconds=jitter_seconds,
        )
    finally:
        billing_metrics.set_worker_up(worker_id, False)
        _log_json("info", {"event": "billing_worker_stopped", "worker_id": worker_id})


if __name__ == "__main__":
    # Ensure stdout logs are visible in docker
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _log_json("error", {"event": "billing_worker_error", "error_type": type(e).__name__, "error_message": str(e)[:200]})
        sys.exit(1)

