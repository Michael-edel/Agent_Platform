"""Admin dashboard with statistics."""

import logging
from typing import Optional, Dict, Any
from sqlalchemy import select, func, distinct
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
from app.admin.links import DASHBOARD_URLS
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
        
        return await self.templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            context={"stats": stats, "urls": DASHBOARD_URLS, "diagnostics": diagnostics},
        )
