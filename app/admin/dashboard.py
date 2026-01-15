"""Admin dashboard with statistics."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import select, func, distinct, desc
from starlette.requests import Request
from starlette.responses import Response
from sqladmin import BaseView, expose

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    TenantSubscription,
    BillingOrder,
    BillingWebhookEvent,
)
from cyberplat.product.infrastructure.models import TenantPlan
from app.admin.auth import get_admin_role, get_admin_tenant_id
from app.admin.links import DASHBOARD_URLS, QUICK_LINKS, build_admin_detail_url, ADMIN_ROUTES
from utils.db_migrations import check_database_migration

logger = logging.getLogger(__name__)


def build_dashboard_stats(role: Optional[str], tenant_id: Optional[str]) -> Dict[str, Any]:
    """
    Build dashboard statistics.
    
    Args:
        role: Admin role (platform_admin, tenant_admin, or None)
        tenant_id: Tenant ID for scoping (only used for tenant_admin)
    
    Returns:
        Dictionary with stats: tenants_count, subscriptions_count, orders_count, webhooks_count
    """
    stats = {
        "tenants_count": 0,
        "subscriptions_count": 0,
        "orders_count": 0,
        "webhooks_count": 0,
        "role": role or "unknown",
        "scoped": False,
    }
    
    # Deny access if no role
    if not role:
        return stats
    
    # Tenant scoping for tenant_admin
    is_tenant_scoped = role == "tenant_admin"
    stats["scoped"] = is_tenant_scoped
    
    # If tenant_admin but no tenant_id, return zeros
    if is_tenant_scoped and not tenant_id:
        logger.warning("tenant_admin without tenant_id, returning zero stats")
        return stats
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Count unique tenants (from TenantPlan table)
            tenants_query = select(func.count(distinct(TenantPlan.tenant_id)))
            if is_tenant_scoped:
                tenants_query = tenants_query.where(TenantPlan.tenant_id == tenant_id)
            stats["tenants_count"] = conn.execute(tenants_query).scalar() or 0
            
            # Count subscriptions
            subs_query = select(func.count(TenantSubscription.id))
            if is_tenant_scoped:
                subs_query = subs_query.where(TenantSubscription.tenant_id == tenant_id)
            stats["subscriptions_count"] = conn.execute(subs_query).scalar() or 0
            
            # Count orders
            orders_query = select(func.count(BillingOrder.id))
            if is_tenant_scoped:
                orders_query = orders_query.where(BillingOrder.tenant_id == tenant_id)
            stats["orders_count"] = conn.execute(orders_query).scalar() or 0
            
            # Count webhook events
            webhooks_query = select(func.count(BillingWebhookEvent.id))
            if is_tenant_scoped:
                webhooks_query = webhooks_query.where(BillingWebhookEvent.tenant_id == tenant_id)
            stats["webhooks_count"] = conn.execute(webhooks_query).scalar() or 0
            
    except Exception as e:
        logger.error(f"Error building dashboard stats: {e}")
    
    return stats


def build_system_diagnostics() -> Dict[str, Any]:
    """
    Build system diagnostics using existing readiness checks.
    
    Returns:
        Dictionary with:
        - readiness_status: "ok" | "degraded" | "unknown"
        - database_driver: driver name or "unknown"
        - migrations_status: "up_to_date" | "pending" | "unknown"
        - migrations_message: detailed message
        - error: error message if any (no secrets)
    """
    diagnostics = {
        "readiness_status": "unknown",
        "database_driver": "unknown",
        "migrations_status": "unknown",
        "migrations_message": "",
        "error": None,
    }
    
    try:
        engine = get_engine()
        
        # Get database driver
        diagnostics["database_driver"] = engine.dialect.driver or "unknown"
        
        # Check migrations
        migration_ok, migration_message = check_database_migration(engine)
        diagnostics["migrations_message"] = migration_message
        
        if migration_ok:
            diagnostics["migrations_status"] = "up_to_date"
            diagnostics["readiness_status"] = "ok"
        else:
            diagnostics["migrations_status"] = "pending"
            diagnostics["readiness_status"] = "degraded"
            
    except Exception as e:
        error_msg = str(e)[:100]  # Truncate to avoid secrets
        logger.error(f"System diagnostics failed: {error_msg}")
        diagnostics["error"] = error_msg
        diagnostics["readiness_status"] = "unknown"
    
    return diagnostics


def _truncate_error(text: Optional[str], max_length: int = 100) -> str:
    """Truncate error text safely."""
    if not text:
        return ""
    from markupsafe import escape
    safe_text = str(escape(text))
    if len(safe_text) > max_length:
        return safe_text[:max_length] + "..."
    return safe_text


def build_recent_errors(role: Optional[str], tenant_id: Optional[str]) -> Dict[str, List[Dict]]:
    """
    Build recent errors (last 24h) for dashboard.
    
    Args:
        role: Admin role
        tenant_id: Tenant ID for scoping
    
    Returns:
        Dictionary with webhook_errors and order_errors lists
    """
    result = {
        "webhook_errors": [],
        "order_errors": [],
    }
    
    # Deny if no role
    if not role:
        return result
    
    # Tenant scoping
    is_tenant_scoped = role == "tenant_admin"
    if is_tenant_scoped and not tenant_id:
        return result
    
    # Calculate 24h ago (ISO format for SQLite compatibility)
    time_24h_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Webhook errors (status = 'failed')
            webhook_query = (
                select(BillingWebhookEvent)
                .where(BillingWebhookEvent.status == "failed")
                .where(BillingWebhookEvent.received_at >= time_24h_ago)
                .order_by(desc(BillingWebhookEvent.received_at))
                .limit(10)
            )
            if is_tenant_scoped:
                webhook_query = webhook_query.where(BillingWebhookEvent.tenant_id == tenant_id)
            
            webhook_rows = conn.execute(webhook_query).fetchall()
            for row in webhook_rows:
                m = row._mapping
                result["webhook_errors"].append({
                    "id": m["id"],
                    "provider": m["provider"],
                    "event_id": m.get("event_id", ""),
                    "tenant_id": m.get("tenant_id", ""),
                    "status": m["status"],
                    "received_at": m["received_at"],
                    "error": _truncate_error(m.get("error")),
                    "detail_url": build_admin_detail_url(ADMIN_ROUTES["billing_webhook_event"], m["id"]),
                })
            
            # Order errors (status = 'failed')
            order_query = (
                select(BillingOrder)
                .where(BillingOrder.status == "failed")
                .where(BillingOrder.created_at >= time_24h_ago)
                .order_by(desc(BillingOrder.created_at))
                .limit(10)
            )
            if is_tenant_scoped:
                order_query = order_query.where(BillingOrder.tenant_id == tenant_id)
            
            order_rows = conn.execute(order_query).fetchall()
            for row in order_rows:
                m = row._mapping
                result["order_errors"].append({
                    "id": m["id"],
                    "provider": m["provider"],
                    "tenant_id": m["tenant_id"],
                    "status": m["status"],
                    "created_at": m["created_at"],
                    "detail_url": build_admin_detail_url(ADMIN_ROUTES["billing_order"], m["id"]),
                })
                
    except Exception as e:
        logger.error(f"Error building recent errors: {e}")
    
    return result


class DashboardView(BaseView):
    """Admin dashboard with key metrics."""
    
    name = "Dashboard"
    icon = "fa-solid fa-chart-line"
    
    @expose("/", methods=["GET"])
    async def dashboard(self, request: Request) -> Response:
        """Render dashboard with statistics."""
        role = get_admin_role(request)
        tenant_id = get_admin_tenant_id(request)
        
        stats = build_dashboard_stats(role, tenant_id)
        diagnostics = build_system_diagnostics()
        recent_errors = build_recent_errors(role, tenant_id)
        
        return await self.templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            context={
                "stats": stats,
                "urls": DASHBOARD_URLS,
                "diagnostics": diagnostics,
                "quick_links": QUICK_LINKS,
                "recent_errors": recent_errors,
            },
        )
