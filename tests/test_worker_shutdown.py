from threading import Event


def test_worker_stop_event_interrupts_sleep(monkeypatch):
    from cyberplat.billing.worker import run_loop

    stop = Event()
    stop.set()

    # Should return immediately when stop is already set (no iteration).
    run_loop(stop_event=stop, interval_seconds=60, batch_size=1, worker_id="t", jitter_seconds=0)


def test_single_iteration_handles_exception(monkeypatch):
    import cyberplat.billing.worker as w

    monkeypatch.setattr(w, "process_due_billing_jobs_stats", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, payload = w.single_iteration(batch_size=10, worker_id="w1")
    assert ok is False
    assert payload["error_type"] == "RuntimeError"

