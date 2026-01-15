"""Centralized URL helpers for admin drill-down links."""

from typing import Optional, Dict
from urllib.parse import quote, urlencode
from markupsafe import Markup, escape


# Admin list route identities (must match SQLAdmin model identities)
ADMIN_ROUTES = {
    "tenant_plan": "tenant-plan",
    "tenant_subscription": "tenant-subscription",
    "billing_order": "billing-order",
    "billing_webhook_event": "billing-webhook-event",
    "billing_usage": "billing-usage",
    "webhook": "webhook",
    "kaspi_order": "kaspi-order",
}


def safe_text(value: str) -> str:
    """Escape text for safe HTML display."""
    if not value:
        return ""
    return str(escape(value))


def safe_path(value: str) -> str:
    """Quote value for safe URL path/query usage."""
    if not value:
        return ""
    return quote(str(value), safe='')


def build_admin_detail_url(resource: str, pk: str) -> str:
    """
    Build admin detail URL for a specific record.
    
    Args:
        resource: Admin resource identity (e.g., "billing-webhook-event")
        pk: Primary key value (will be URL-encoded)
    
    Returns:
        URL string like "/admin/billing-webhook-event/details/abc123"
    """
    return f"/admin/{resource}/details/{safe_path(str(pk))}"


def build_admin_list_url(resource: str, query: Optional[Dict[str, str]] = None) -> str:
    """
    Build admin list URL with optional query parameters.
    
    Args:
        resource: Admin resource identity (e.g., "tenant-subscription")
        query: Optional dict of query parameters
    
    Returns:
        URL string like "/admin/tenant-subscription/list?tenant_id=xxx"
    """
    base_url = f"/admin/{resource}/list"
    
    if query:
        # URL-encode all query values
        safe_query = {k: safe_path(v) for k, v in query.items() if v}
        if safe_query:
            return f"{base_url}?{urlencode(safe_query)}"
    
    return base_url


def tenant_drill_links(tenant_id: str) -> Markup:
    """
    Generate drill-down links for a tenant_id.
    
    Returns HTML with links to:
    - Subscriptions
    - Orders
    - Webhook events
    """
    if not tenant_id:
        return Markup("")
    
    safe_display = safe_text(tenant_id)
    
    links = [
        f'<a href="{build_admin_list_url(ADMIN_ROUTES["tenant_subscription"], {"tenant_id": tenant_id})}" '
        f'title="Subscriptions"><i class="fa-solid fa-receipt"></i></a>',
        
        f'<a href="{build_admin_list_url(ADMIN_ROUTES["billing_order"], {"tenant_id": tenant_id})}" '
        f'title="Orders"><i class="fa-solid fa-shopping-cart"></i></a>',
        
        f'<a href="{build_admin_list_url(ADMIN_ROUTES["billing_webhook_event"], {"tenant_id": tenant_id})}" '
        f'title="Webhooks"><i class="fa-solid fa-bell"></i></a>',
    ]
    
    return Markup(f'{safe_display} ' + ' '.join(links))


# Dashboard navigation URLs
DASHBOARD_URLS = {
    "tenants": build_admin_list_url(ADMIN_ROUTES["tenant_plan"]),
    "subscriptions": build_admin_list_url(ADMIN_ROUTES["tenant_subscription"]),
    "orders": build_admin_list_url(ADMIN_ROUTES["billing_order"]),
    "webhooks": build_admin_list_url(ADMIN_ROUTES["billing_webhook_event"]),
}

# Quick links for dashboard operations
QUICK_LINKS = {
    "webhook_events": {
        "label": "Webhook Events (newest)",
        "url": build_admin_list_url(ADMIN_ROUTES["billing_webhook_event"]),
        "icon": "fa-solid fa-bell",
    },
    "webhook_errors": {
        "label": "Webhook Errors",
        "url": build_admin_list_url(ADMIN_ROUTES["billing_webhook_event"], {"status": "failed"}),
        "icon": "fa-solid fa-circle-exclamation",
    },
    "recent_orders": {
        "label": "Orders (newest)",
        "url": build_admin_list_url(ADMIN_ROUTES["billing_order"]),
        "icon": "fa-solid fa-shopping-cart",
    },
    "subscriptions": {
        "label": "Subscriptions",
        "url": build_admin_list_url(ADMIN_ROUTES["tenant_subscription"]),
        "icon": "fa-solid fa-receipt",
    },
}
