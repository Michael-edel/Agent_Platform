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
        return True, out
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
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
    while not stop_event.is_set():
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

    _log_json(
        "info",
        {
            "event": "billing_worker_started",
            "worker_id": worker_id,
            "interval_seconds": interval_seconds,
            "batch_size": batch_size,
            "jitter_seconds": jitter_seconds,
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

