import os


def test_billing_metrics_module_import_and_names():
    from prometheus_client import REGISTRY, generate_latest

    # Import should register collectors (idempotently)
    import cyberplat.billing.metrics as m  # noqa: F401

    body = generate_latest(REGISTRY).decode("utf-8", errors="ignore")
    assert "billing_worker_iterations_total" in body
    assert "billing_jobs_processed_total" in body
    assert "billing_jobs_queue_depth" in body
    assert "billing_worker_up" in body


def test_worker_single_iteration_updates_counters(monkeypatch):
    # Avoid starting HTTP server in tests (we don't call main anyway)
    monkeypatch.setenv("BILLING_WORKER_METRICS_ENABLED", "false")

    import cyberplat.billing.worker as w

    monkeypatch.setattr(
        w,
        "process_due_billing_jobs_stats",
        lambda **kwargs: {
            "processed_count": 3,
            "succeeded_count": 1,
            "failed_count": 1,
            "retried_count": 1,
            "skipped_count": 0,
        },
    )

    ok, payload = w.single_iteration(batch_size=10, worker_id="w-test")
    assert ok is True
    assert payload["processed_count"] == 3

