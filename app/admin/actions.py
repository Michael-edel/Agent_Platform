"""SQLAdmin Safe Actions (no charges, no retries executed directly)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import quote

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import BillingJob, UsageInvoice
from app.admin.auth import get_admin_role, get_admin_username
from app.admin.audit import write_audit_log


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redir(job_id: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    base = f"/admin/billing-job/details/{quote(str(job_id), safe='')}"
    if error:
        return RedirectResponse(url=f"{base}?error={quote(error[:200], safe='')}", status_code=303)
    if msg:
        return RedirectResponse(url=f"{base}?msg={quote(msg[:200], safe='')}", status_code=303)
    return RedirectResponse(url=base, status_code=303)


def _require_platform_admin(request: Request) -> Response | None:
    role = get_admin_role(request)
    if role != "platform_admin":
        return HTMLResponse("<h3>Forbidden</h3><p>platform_admin only</p>", status_code=403)
    return None


def _get_stripe_api_key() -> str:
    key = os.getenv("STRIPE_API_KEY", "").strip()
    if not key:
        key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    return key


def _map_stripe_intent_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "succeeded":
        return "paid"
    if s in {"canceled", "requires_payment_method"}:
        return "failed"
    return "processing"


class SafeActionsView(BaseView):
    """Safe operational actions for billing (admin-only)."""

    name = "Safe Actions"
    icon = "fa-solid fa-shield"

    @expose("/actions", methods=["GET"], identity="safe-actions")
    async def a_index(self, request: Request) -> Response:
        # Simple info page to keep menu routing stable (no path params).
        return HTMLResponse(
            "<h3>Safe Actions</h3>"
            "<p>These actions are safe by design:</p>"
            "<ul>"
            "<li><b>Refresh from provider</b>: reads Stripe payment status only (no charges).</li>"
            "<li><b>Mark job for retry</b>: queues retry for processor (no provider calls).</li>"
            "</ul>",
            status_code=200,
        )

    @expose("/actions/billing-job/{job_id}/refresh", methods=["POST"])
    async def refresh_billing_job(self, request: Request) -> Response:
        deny = _require_platform_admin(request)
        if deny:
            return deny

        actor = get_admin_username(request) or "admin"
        role = get_admin_role(request) or "unknown"

        engine = get_engine()
        job_id_str = str(request.path_params.get("job_id") or "")
        with Session(engine) as session:
            job = session.execute(select(BillingJob).where(BillingJob.id == job_id_str)).scalar_one_or_none()
            if not job:
                return _redir(job_id_str, error="Billing job not found")
            if job.provider != "stripe" or not job.provider_ref:
                return _redir(job_id_str, error="Refresh supported only for Stripe jobs with provider_ref")

            api_key = _get_stripe_api_key()
            if not api_key:
                return _redir(job_id_str, error="Stripe misconfigured: STRIPE_API_KEY is missing")

            try:
                import stripe  # type: ignore
            except Exception:
                return _redir(job_id_str, error="Stripe SDK not installed")

            stripe.api_key = api_key
            try:
                intent = stripe.PaymentIntent.retrieve(str(job.provider_ref))
                intent_status = str(getattr(intent, "status", "") or "")
            except Exception as e:
                return _redir(job_id_str, error=f"Failed to retrieve Stripe PaymentIntent: {str(e)[:120]}")

            new_payment_status = _map_stripe_intent_status(intent_status)

            inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == job.invoice_id)).scalar_one_or_none()
            if not inv:
                return _redir(job_id_str, error="Usage invoice not found for this job")

            old_payment_status = getattr(inv, "payment_status", None)
            if old_payment_status != new_payment_status:
                session.execute(
                    update(UsageInvoice)
                    .where(UsageInvoice.id == inv.id)
                    .where(UsageInvoice.tenant_id == inv.tenant_id)
                    .values(payment_status=new_payment_status)
                )

            write_audit_log(
                session,
                actor_username=actor,
                actor_role=role,
                tenant_id=str(inv.tenant_id),
                action="refresh_provider_status",
                entity_type="BillingJob",
                entity_id=str(job.id),
                metadata={
                    "provider": "stripe",
                    "provider_ref": str(job.provider_ref),
                    "stripe_status": intent_status,
                    "old_payment_status": old_payment_status,
                    "new_payment_status": new_payment_status,
                    "invoice_id": str(inv.id),
                },
            )
            session.commit()

            return _redir(job_id_str, msg=f"Refreshed: stripe_status={intent_status}, payment_status={new_payment_status}")

    @expose("/actions/billing-job/{job_id}/retry", methods=["POST"])
    async def retry_billing_job(self, request: Request) -> Response:
        deny = _require_platform_admin(request)
        if deny:
            return deny

        actor = get_admin_username(request) or "admin"
        role = get_admin_role(request) or "unknown"

        now = _now_iso()
        engine = get_engine()
        job_id_str = str(request.path_params.get("job_id") or "")
        with Session(engine) as session:
            job = session.execute(select(BillingJob).where(BillingJob.id == job_id_str)).scalar_one_or_none()
            if not job:
                return _redir(job_id_str, error="Billing job not found")

            if job.status not in {"failed", "retryable_failed"}:
                return _redir(job_id_str, error=f"Job status not retryable: {job.status}")

            # Respect max attempts
            attempt_count = int(getattr(job, "attempt_count", 0) or 0)
            max_attempts = int(getattr(job, "max_attempts", 0) or 0) or 5
            if attempt_count >= max_attempts:
                write_audit_log(
                    session,
                    actor_username=actor,
                    actor_role=role,
                    tenant_id=str(job.tenant_id),
                    action="mark_retry_denied",
                    entity_type="BillingJob",
                    entity_id=str(job.id),
                    metadata={
                        "reason": "max_attempts_reached",
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                        "invoice_id": str(job.invoice_id),
                    },
                )
                session.commit()
                return _redir(job_id_str, error="Max attempts reached")

            session.execute(
                update(BillingJob)
                .where(BillingJob.id == job_id_str)
                .values(status="pending_retry", next_attempt_at=now, updated_at=now)
            )

            write_audit_log(
                session,
                actor_username=actor,
                actor_role=role,
                tenant_id=str(job.tenant_id),
                action="mark_retry",
                entity_type="BillingJob",
                entity_id=str(job.id),
                metadata={
                    "provider": str(job.provider),
                    "provider_ref": str(job.provider_ref) if job.provider_ref else None,
                    "invoice_id": str(job.invoice_id),
                    "attempt_count": attempt_count,
                    "max_attempts": max_attempts,
                },
            )
            session.commit()

            return _redir(job_id_str, msg="Job marked for retry (queued).")

