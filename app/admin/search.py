"""Admin unified search view."""

import logging
from typing import Optional, Dict, Any
import re
from sqlalchemy import select, or_
from starlette.requests import Request
from starlette.responses import Response, RedirectResponse
from sqladmin import BaseView, expose

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    TenantSubscription,
    BillingOrder,
    BillingWebhookEvent,
)
from app.admin.auth import get_admin_role, get_admin_tenant_id
from app.admin.links import ADMIN_ROUTES, build_admin_list_url, build_admin_detail_url

from cyberplat.product.infrastructure.models import BillingJob, UsageInvoice

logger = logging.getLogger(__name__)

MAX_RESULTS = 20


def build_search_results(
    query: str,
    role: Optional[str],
    tenant_id: Optional[str]
) -> Dict[str, Any]:
    """
    Build search results across billing entities.
    
    Args:
        query: Search query string
        role: User role (platform_admin/tenant_admin)
        tenant_id: Tenant ID for scoping (required for tenant_admin)
    
    Returns:
        Dict with webhook_events, orders, subscriptions lists and error if any
    """
    results = {
        "billing_jobs": [],
        "usage_invoices": [],
        "webhook_events": [],
        "orders": [],
        "subscriptions": [],
        "error": None,
    }
    
    if not query or not query.strip():
        return results
    
    q = query.strip()
    
    # Deny by default for tenant_admin without tenant_id
    is_tenant_admin = role == "tenant_admin"
    if is_tenant_admin and not tenant_id:
        return results
    
    # No role means no access
    if not role:
        return results
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Search Billing Jobs (provider_ref / invoice_id / idempotency_key)
            stmt = select(
                BillingJob.id,
                BillingJob.tenant_id,
                BillingJob.invoice_id,
                BillingJob.provider,
                BillingJob.provider_ref,
                BillingJob.status,
                BillingJob.created_at,
            ).where(
                or_(
                    BillingJob.provider_ref.ilike(f"%{q}%"),
                    BillingJob.invoice_id.ilike(f"%{q}%"),
                    BillingJob.idempotency_key.ilike(f"%{q}%"),
                    BillingJob.id.ilike(f"%{q}%"),
                )
            )
            if is_tenant_admin:
                stmt = stmt.where(BillingJob.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            rows = conn.execute(stmt).fetchall()
            results["billing_jobs"] = [
                {
                    "id": r._mapping["id"],
                    "tenant_id": r._mapping["tenant_id"],
                    "invoice_id": r._mapping["invoice_id"],
                    "provider": r._mapping["provider"],
                    "provider_ref": r._mapping["provider_ref"],
                    "status": r._mapping["status"],
                    "created_at": r._mapping["created_at"],
                    "detail_url": build_admin_detail_url(ADMIN_ROUTES["billing_job"], r._mapping["id"]),
                }
                for r in rows
            ]

            # Search Usage Invoices (id / tenant_id / period / payment_status)
            period_match = re.match(r"^(\d{4})-(\d{2})$", q)
            period_year = int(period_match.group(1)) if period_match else None
            period_month = int(period_match.group(2)) if period_match else None

            stmt = select(
                UsageInvoice.id,
                UsageInvoice.tenant_id,
                UsageInvoice.period_year,
                UsageInvoice.period_month,
                UsageInvoice.amount_cents,
                UsageInvoice.currency,
                UsageInvoice.status,
                UsageInvoice.payment_status,
                UsageInvoice.created_at,
            ).where(
                or_(
                    UsageInvoice.id.ilike(f"%{q}%"),
                    UsageInvoice.tenant_id.ilike(f"%{q}%"),
                    UsageInvoice.payment_status.ilike(f"%{q}%"),
                    UsageInvoice.status.ilike(f"%{q}%"),
                )
            )
            if period_year is not None and period_month is not None:
                stmt = stmt.where(UsageInvoice.period_year == period_year, UsageInvoice.period_month == period_month)
            if is_tenant_admin:
                stmt = stmt.where(UsageInvoice.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            rows = conn.execute(stmt).fetchall()
            results["usage_invoices"] = [
                {
                    "id": r._mapping["id"],
                    "tenant_id": r._mapping["tenant_id"],
                    "period_year": r._mapping["period_year"],
                    "period_month": r._mapping["period_month"],
                    "amount_cents": r._mapping["amount_cents"],
                    "currency": r._mapping["currency"],
                    "status": r._mapping["status"],
                    "payment_status": r._mapping["payment_status"],
                    "created_at": r._mapping["created_at"],
                    "detail_url": build_admin_detail_url(ADMIN_ROUTES["usage_invoice"], r._mapping["id"]),
                }
                for r in rows
            ]

            # Search Webhook Events
            # Note: provider_ref/request_id are not first-class columns in v1 schema, so we do best-effort match:
            # - event_id (indexed)
            # - provider
            # - raw_json LIKE (limited results, never returned/displayed)
            stmt = select(
                BillingWebhookEvent.id,
                BillingWebhookEvent.provider,
                BillingWebhookEvent.event_id,
                BillingWebhookEvent.tenant_id,
                BillingWebhookEvent.status,
                BillingWebhookEvent.received_at,
            ).where(
                or_(
                    BillingWebhookEvent.event_id.ilike(f"%{q}%"),
                    BillingWebhookEvent.provider.ilike(f"%{q}%"),
                    BillingWebhookEvent.raw_json.ilike(f"%{q}%"),
                )
            )
            if is_tenant_admin:
                stmt = stmt.where(BillingWebhookEvent.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            
            result = conn.execute(stmt)
            results["webhook_events"] = [
                {
                    "id": row._mapping["id"],
                    "provider": row._mapping["provider"],
                    "event_id": row._mapping["event_id"],
                    "tenant_id": row._mapping["tenant_id"],
                    "status": row._mapping["status"],
                    "received_at": row._mapping["received_at"],
                    "detail_url": build_admin_detail_url(ADMIN_ROUTES["billing_webhook_event"], row._mapping["id"]),
                }
                for row in result.fetchall()
            ]
            
            # Search Orders
            stmt = select(BillingOrder).where(
                or_(
                    BillingOrder.external_order_id.ilike(f"%{q}%"),
                    BillingOrder.provider.ilike(f"%{q}%"),
                )
            )
            if is_tenant_admin:
                stmt = stmt.where(BillingOrder.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            
            result = conn.execute(stmt)
            results["orders"] = [
                {**dict(row._mapping), "detail_url": build_admin_detail_url(ADMIN_ROUTES["billing_order"], row._mapping["id"])}
                for row in result.fetchall()
            ]
            
            # Search Subscriptions
            stmt = select(TenantSubscription).where(
                or_(
                    TenantSubscription.provider_subscription_id.ilike(f"%{q}%"),
                    TenantSubscription.provider.ilike(f"%{q}%"),
                )
            )
            if is_tenant_admin:
                stmt = stmt.where(TenantSubscription.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            
            result = conn.execute(stmt)
            results["subscriptions"] = [
                {**dict(row._mapping), "detail_url": build_admin_detail_url(ADMIN_ROUTES["tenant_subscription"], row._mapping["id"])}
                for row in result.fetchall()
            ]
            
    except Exception as e:
        error_msg = str(e)[:100]
        logger.error(f"Search failed: {error_msg}")
        results["error"] = error_msg
    
    return results


class SearchView(BaseView):
    """Unified search across billing entities."""
    
    name = "Search"
    icon = "fa-solid fa-magnifying-glass"
    
    @expose("/search", methods=["GET", "POST"])
    async def search(self, request: Request) -> Response:
        """Search page handler."""
        role = get_admin_role(request)
        tenant_id = get_admin_tenant_id(request)
        
        query = ""
        results = None

        if request.method == "POST":
            form = await request.form()
            query = (form.get("q", "") or "").strip()
            # Redirect to GET for sharable links / drill-down.
            return RedirectResponse(url=f"/admin/search?q={query}", status_code=303)

        query = (request.query_params.get("q") or "").strip()
        if query:
            results = build_search_results(query, role, tenant_id)
        
        # URLs for "Open in list" links
        list_urls = {
            "billing_jobs": build_admin_list_url(ADMIN_ROUTES["billing_job"]),
            "usage_invoices": build_admin_list_url(ADMIN_ROUTES["usage_invoice"]),
            "webhook_events": build_admin_list_url(ADMIN_ROUTES["billing_webhook_event"]),
            "orders": build_admin_list_url(ADMIN_ROUTES["billing_order"]),
            "subscriptions": build_admin_list_url(ADMIN_ROUTES["tenant_subscription"]),
        }
        
        return await self.templates.TemplateResponse(
            request,
            "admin/search.html",
            context={
                "query": query,
                "results": results,
                "list_urls": list_urls,
            },
        )
