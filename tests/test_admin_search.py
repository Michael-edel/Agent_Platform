from datetime import datetime, timezone
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_admin_search_finds_by_provider_ref_and_scopes_tenant(monkeypatch, tmp_path):
    # Use temp DB
    db_path = tmp_path / "admin_search.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base as ProductBase, UsageInvoice, BillingJob
    from cyberplat.billing.infrastructure.models_sqlalchemy import Base as BillingBase, BillingWebhookEvent
    from sqlalchemy.orm import Session

    engine = get_engine()
    ProductBase.metadata.create_all(engine)
    BillingBase.metadata.create_all(engine)

    tenant_ok = "tenant-ok"
    tenant_other = "tenant-other"
    invoice_id = str(uuid.uuid4())
    provider_ref = "pi_test_123"

    with Session(engine) as session:
        session.add(
            UsageInvoice(
                id=invoice_id,
                tenant_id=tenant_ok,
                period_year=2026,
                period_month=1,
                currency="KZT",
                amount_cents=10,
                status="finalized",
                payment_status="processing",
                created_at=_now_iso(),
                finalized_at=_now_iso(),
                event_emitted_at=None,
            )
        )
        session.add(
            BillingJob(
                id=str(uuid.uuid4()),
                tenant_id=tenant_ok,
                invoice_id=invoice_id,
                provider="stripe",
                status="succeeded",
                attempt_count=1,
                max_attempts=10,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=_now_iso(),
                idempotency_key=f"usage_invoice:{invoice_id}",
                provider_ref=provider_ref,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
            )
        )
        session.add(
            BillingWebhookEvent(
                id="evt-row-1",
                provider="stripe",
                event_id="evt_test_1",
                received_at=_now_iso(),
                processed_at=_now_iso(),
                tenant_id=tenant_ok,
                raw_json='{"type":"payment_intent.succeeded","data":{"object":{"id":"pi_test_123"}}}',
                status="processed",
                error=None,
            )
        )
        # Other-tenant noise
        session.add(
            BillingJob(
                id=str(uuid.uuid4()),
                tenant_id=tenant_other,
                invoice_id=str(uuid.uuid4()),
                provider="stripe",
                status="succeeded",
                attempt_count=1,
                max_attempts=10,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                processing_started_at=None,
                finished_at=_now_iso(),
                idempotency_key="usage_invoice:other",
                provider_ref=provider_ref,
                last_error_code=None,
                last_error_message=None,
                next_attempt_at=None,
            )
        )
        session.commit()

    from app.admin.search import build_search_results

    # platform_admin: sees both jobs (different tenants) but at least one match
    res = build_search_results(provider_ref, role="platform_admin", tenant_id=None)
    assert res["error"] is None
    assert len(res["billing_jobs"]) >= 1
    assert any(r["provider_ref"] == provider_ref for r in res["billing_jobs"])
    assert any(e["event_id"] == "evt_test_1" for e in res["webhook_events"])

    # tenant_admin: only own tenant
    res2 = build_search_results(provider_ref, role="tenant_admin", tenant_id=tenant_ok)
    assert all(r["tenant_id"] == tenant_ok for r in res2["billing_jobs"])


def test_admin_search_does_not_return_raw_payload(monkeypatch, tmp_path):
    db_path = tmp_path / "admin_search_no_raw.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import Base as ProductBase
    from cyberplat.billing.infrastructure.models_sqlalchemy import Base as BillingBase, BillingWebhookEvent
    from sqlalchemy.orm import Session

    engine = get_engine()
    ProductBase.metadata.create_all(engine)
    BillingBase.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            BillingWebhookEvent(
                id="evt-row-2",
                provider="stripe",
                event_id="evt_test_2",
                received_at=_now_iso(),
                processed_at=_now_iso(),
                tenant_id="tenant-1",
                raw_json='{"secret":"should-not-leak","data":{"object":{"id":"pi_x"}}}',
                status="processed",
                error=None,
            )
        )
        session.commit()

    from app.admin.search import build_search_results

    res = build_search_results("pi_x", role="platform_admin", tenant_id=None)
    assert res["error"] is None
    # Ensure raw_json is not present in result dicts
    assert all("raw_json" not in e for e in res["webhook_events"])

