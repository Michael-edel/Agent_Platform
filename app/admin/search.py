"""Admin unified search view."""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import select, or_
from starlette.requests import Request
from starlette.responses import Response
from sqladmin import BaseView, expose

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    TenantSubscription,
    BillingOrder,
    BillingWebhookEvent,
)
from app.admin.auth import get_admin_role, get_admin_tenant_id
from app.admin.links import ADMIN_ROUTES, build_admin_list_url

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
            # Search Webhook Events
            stmt = select(BillingWebhookEvent).where(
                or_(
                    BillingWebhookEvent.event_id.ilike(f"%{q}%"),
                    BillingWebhookEvent.provider.ilike(f"%{q}%"),
                )
            )
            if is_tenant_admin:
                stmt = stmt.where(BillingWebhookEvent.tenant_id == tenant_id)
            stmt = stmt.limit(MAX_RESULTS)
            
            result = conn.execute(stmt)
            results["webhook_events"] = [row._mapping for row in result.fetchall()]
            
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
            results["orders"] = [row._mapping for row in result.fetchall()]
            
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
            results["subscriptions"] = [row._mapping for row in result.fetchall()]
            
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
            query = form.get("q", "")
            results = build_search_results(query, role, tenant_id)
        
        # URLs for "Open in list" links
        list_urls = {
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
