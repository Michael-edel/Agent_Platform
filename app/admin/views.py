"""SQLAdmin model views for admin panel."""

import json
from typing import Any, Optional
from markupsafe import Markup
from sqladmin import ModelView
from sqlalchemy import select, func, false as sql_false
from starlette.requests import Request

# Product models
from cyberplat.product.infrastructure.models import (
    Plan,
    TenantPlan,
    Webhook,
    WebhookDelivery,
    KaspiOrder,
    ArtifactState,
    Export,
    UsageInvoice,
    UsageInvoiceLine,
    BillingJob,
)

# Billing models
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    BillingPlan,
    TenantSubscription,
    BillingWebhookEvent,
    BillingOrder,
    BillingUsage,
)
from cyberplat.product.infrastructure.models import TenantPortalToken, AgentSKU, TenantAgent, TenantAgentSubscription

from app.admin.auth import get_admin_role, get_admin_tenant_id


from app.admin.links import (
    tenant_drill_links,
    ADMIN_ROUTES,
    build_admin_detail_url,
    build_admin_list_url,
    safe_text,
    safe_path,
)


def is_tenant_admin(request: Request) -> bool:
    """Check if current user is tenant_admin."""
    return get_admin_role(request) == "tenant_admin"


class TenantScopedMixin:
    """Mixin for tenant-scoped views (tenant_admin sees only own data)."""
    
    def _get_tenant_filter_params(self, request: Request):
        """Get tenant filtering parameters."""
        role = get_admin_role(request)
        if role == "tenant_admin":
            tenant_id = get_admin_tenant_id(request)
            return True, tenant_id
        return False, None
    
    def list_query(self, request: Request):
        """Override list query to apply tenant filtering."""
        query = select(self.model)
        scoped, tenant_id = self._get_tenant_filter_params(request)
        if scoped:
            if tenant_id and hasattr(self.model, "tenant_id"):
                query = query.where(self.model.tenant_id == tenant_id)
            else:
                # No tenant_id but tenant_admin role: return empty
                query = query.where(sql_false())
        return query
    
    def count_query(self, request: Request):
        """Override count query to apply tenant filtering (prevents total count leaks)."""
        query = select(func.count()).select_from(self.model)
        scoped, tenant_id = self._get_tenant_filter_params(request)
        if scoped:
            if tenant_id and hasattr(self.model, "tenant_id"):
                query = query.where(self.model.tenant_id == tenant_id)
            else:
                # No tenant_id but tenant_admin role: return 0
                query = query.where(sql_false())
        return query


# ============================================
# Product Models (from cyberplat/product)
# ============================================

class PlanAdmin(ModelView, model=Plan):
    """Admin view for Plans (editable)."""
    
    name = "Plan"
    name_plural = "Plans"
    icon = "fa-solid fa-tags"
    
    can_create = True
    can_edit = True
    can_delete = True
    
    column_list = ["id", "name", "price_minor", "currency", "active", "created_at"]
    column_searchable_list = ["id", "name"]
    column_filters = ["active", "created_at"]
    column_sortable_list = ["id", "name", "price_minor", "active", "created_at"]
    page_size = 50


class TenantPlanAdmin(TenantScopedMixin, ModelView, model=TenantPlan):
    """Admin view for Tenant Plans (editable)."""
    
    name = "Tenant Plan"
    name_plural = "Tenant Plans"
    icon = "fa-solid fa-user-tag"
    
    can_create = True
    can_edit = True
    can_delete = True
    
    column_list = ["tenant_id", "plan_id", "subscription_status", "started_at", "expires_at", "created_at"]
    column_searchable_list = ["tenant_id", "plan_id"]
    column_filters = ["plan_id", "subscription_status", "created_at"]
    column_sortable_list = ["tenant_id", "plan_id", "subscription_status", "created_at"]
    page_size = 50
    
    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id),
    }


class WebhookAdmin(TenantScopedMixin, ModelView, model=Webhook):
    """Admin view for Webhooks (read-only)."""
    
    name = "Webhook"
    name_plural = "Webhooks"
    icon = "fa-solid fa-link"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    # Note: secret column excluded by listing only safe columns
    column_list = ["id", "tenant_id", "url", "active", "created_at"]
    column_searchable_list = ["tenant_id", "url"]
    column_filters = ["tenant_id", "active", "created_at"]
    column_sortable_list = ["tenant_id", "active", "created_at"]
    page_size = 50


class WebhookDeliveryAdmin(ModelView, model=WebhookDelivery):
    """Admin view for Webhook Deliveries (read-only)."""
    
    name = "Webhook Delivery"
    name_plural = "Webhook Deliveries"
    icon = "fa-solid fa-paper-plane"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "webhook_id", "event_type", "status", "attempts", "created_at", "sent_at"]
    column_searchable_list = ["webhook_id", "event_type"]
    column_filters = ["status", "event_type", "created_at"]
    column_sortable_list = ["status", "event_type", "attempts", "created_at"]
    page_size = 50


class KaspiOrderAdmin(TenantScopedMixin, ModelView, model=KaspiOrder):
    """Admin view for Kaspi Orders (read-only)."""
    
    name = "Kaspi Order"
    name_plural = "Kaspi Orders"
    icon = "fa-solid fa-credit-card"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "tenant_id", "plan_id", "kaspi_order_id", "status", "created_at", "paid_at"]
    column_searchable_list = ["tenant_id", "kaspi_order_id", "plan_id"]
    column_filters = ["tenant_id", "plan_id", "status", "created_at"]
    column_sortable_list = ["tenant_id", "status", "created_at", "paid_at"]
    page_size = 50


class ArtifactStateAdmin(TenantScopedMixin, ModelView, model=ArtifactState):
    """Admin view for Artifact States (read-only)."""
    
    name = "Artifact State"
    name_plural = "Artifact States"
    icon = "fa-solid fa-file"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "tenant_id", "artifact_id", "ui_status", "created_at", "updated_at"]
    column_searchable_list = ["tenant_id", "artifact_id"]
    column_filters = ["tenant_id", "ui_status", "created_at"]
    column_sortable_list = ["tenant_id", "ui_status", "created_at"]
    page_size = 50


class ExportAdmin(TenantScopedMixin, ModelView, model=Export):
    """Admin view for Exports (read-only)."""
    
    name = "Export"
    name_plural = "Exports"
    icon = "fa-solid fa-download"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "tenant_id", "artifact_id", "export_type", "status", "created_at"]
    column_searchable_list = ["tenant_id", "artifact_id"]
    column_filters = ["tenant_id", "export_type", "status", "created_at"]
    column_sortable_list = ["tenant_id", "export_type", "status", "created_at"]
    page_size = 50


# ============================================
# Billing Models
# ============================================

class BillingPlanAdmin(ModelView, model=BillingPlan):
    """Admin view for Billing Plans (editable)."""
    
    name = "Billing Plan"
    name_plural = "Billing Plans"
    icon = "fa-solid fa-money-bill"
    
    can_create = True
    can_edit = True
    can_delete = True
    
    column_list = ["id", "name", "currency", "price_minor", "period", "active", "created_at"]
    column_searchable_list = ["id", "name"]
    column_filters = ["active", "period", "created_at"]
    column_sortable_list = ["id", "name", "price_minor", "active", "created_at"]
    page_size = 50


class TenantSubscriptionAdmin(TenantScopedMixin, ModelView, model=TenantSubscription):
    """Admin view for Tenant Subscriptions (read-only)."""
    
    name = "Tenant Subscription"
    name_plural = "Tenant Subscriptions"
    icon = "fa-solid fa-receipt"
    
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True
    
    column_list = ["id", "tenant_id", "provider", "plan_id", "status", "current_period_start", "current_period_end", "created_at"]
    column_searchable_list = ["tenant_id", "provider", "status", "provider_subscription_id"]
    column_filters = ["provider", "status", "tenant_id", "plan_id", "created_at"]
    column_sortable_list = ["tenant_id", "provider", "status", "current_period_start", "current_period_end", "created_at", "updated_at"]
    column_default_sort = ("created_at", True)  # Newest first
    page_size = 50
    
    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id),
    }


def truncate_error(text: str, max_length: int = 200) -> str:
    """Truncate error text for safe display."""
    if not text:
        return ""
    from markupsafe import escape
    safe_text = str(escape(text))
    if len(safe_text) > max_length:
        return safe_text[:max_length] + "..."
    return safe_text


class BillingWebhookEventAdmin(TenantScopedMixin, ModelView, model=BillingWebhookEvent):
    """Admin view for Billing Webhook Events (read-only)."""

    name = "Billing Webhook Event"
    name_plural = "Billing Webhook Events"
    icon = "fa-solid fa-bell"

    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True

    # Security: raw_json excluded - only safe columns shown
    column_list = ["id", "provider", "event_id", "tenant_id", "status", "error", "received_at", "processed_at"]
    column_details_exclude_list = ["raw_json"]
    
    # Search by key identifiers
    column_searchable_list = ["event_id", "tenant_id", "provider", "status"]
    
    # Filters for quick navigation
    column_filters = ["provider", "status", "tenant_id", "received_at"]
    
    # Sorting by time fields
    column_sortable_list = ["provider", "status", "received_at", "processed_at", "tenant_id"]
    column_default_sort = ("received_at", True)  # Newest first
    
    page_size = 50

    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id) if m.tenant_id else "",
        "error": lambda m, a: Markup(f'<span title="{truncate_error(m.error, 500)}">{truncate_error(m.error, 100)}</span>') if m.error else "",
        "event_id": lambda m, a: Markup(
            f'{safe_text(m.event_id)} {(_render_job_link_from_webhook_event(m) or "")}'
        )
        if getattr(m, "event_id", None)
        else "",
    }


def _count_billing_jobs_for_invoice(tenant_id: str, invoice_id: str) -> int:
    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import BillingJob

    engine = get_engine()
    with engine.connect() as conn:
        stmt = select(func.count()).select_from(BillingJob).where(
            BillingJob.tenant_id == tenant_id,
            BillingJob.invoice_id == invoice_id,
        )
        return int(conn.execute(stmt).scalar() or 0)


def _extract_provider_ref_from_webhook_raw(raw_json: str) -> Optional[str]:
    """
    Best-effort extraction of provider_ref from webhook raw_json.
    We only use it for linking/navigation and never display the raw payload.
    """
    if not raw_json:
        return None
    try:
        data = json.loads(raw_json)
    except Exception:
        return None

    # Stripe: data.object.id for payment_intent.* (pi_...)
    obj = None
    if isinstance(data, dict):
        obj = (data.get("data") or {}).get("object") if isinstance(data.get("data"), dict) else None
        if isinstance(obj, dict):
            v = obj.get("id")
            if isinstance(v, str) and v.strip():
                return v.strip()

        # Kaspi: order_id/external_order_id at top-level or under data
        for key in ("external_order_id", "kaspi_order_id", "order_id"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        d = data.get("data")
        if isinstance(d, dict):
            for key in ("external_order_id", "kaspi_order_id", "order_id"):
                v = d.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()

    return None


def _render_job_link_from_webhook_event(event: BillingWebhookEvent) -> str:
    """
    Render a drill-down link from a billing webhook event to a BillingJob (if possible).
    """
    provider_ref = _extract_provider_ref_from_webhook_raw(getattr(event, "raw_json", "") or "")
    tenant_id = getattr(event, "tenant_id", None)
    if not provider_ref or not tenant_id:
        return ""

    from cyberplat.product.infrastructure.database import get_engine
    from cyberplat.product.infrastructure.models import BillingJob

    engine = get_engine()
    with engine.connect() as conn:
        job_id = conn.execute(
            select(BillingJob.id).where(
                BillingJob.tenant_id == tenant_id,
                BillingJob.provider_ref == provider_ref,
            ).limit(1)
        ).scalar_one_or_none()

    if not job_id:
        return ""

    url = build_admin_detail_url(ADMIN_ROUTES["billing_job"], str(job_id))
    return f'<a href="{url}" class="ms-1" title="Open Billing Job"><i class="fa-solid fa-tasks"></i></a>'


class BillingOrderAdmin(TenantScopedMixin, ModelView, model=BillingOrder):
    """Admin view for Billing Orders (read-only)."""
    
    name = "Billing Order"
    name_plural = "Billing Orders"
    icon = "fa-solid fa-shopping-cart"
    
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True
    
    column_list = ["id", "tenant_id", "provider", "plan_id", "amount_minor", "currency", "status", "created_at", "paid_at"]
    column_searchable_list = ["tenant_id", "provider", "status", "external_order_id"]
    column_filters = ["provider", "status", "tenant_id", "created_at"]
    column_sortable_list = ["tenant_id", "provider", "status", "amount_minor", "created_at", "paid_at"]
    column_default_sort = ("created_at", True)  # Newest first
    page_size = 50
    
    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id),
    }


class BillingUsageAdmin(TenantScopedMixin, ModelView, model=BillingUsage):
    """Admin view for Billing Usage (read-only)."""
    
    name = "Billing Usage"
    name_plural = "Billing Usage"
    icon = "fa-solid fa-chart-line"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "tenant_id", "metric", "units", "amount_minor", "period", "created_at"]
    column_searchable_list = ["tenant_id", "event_id", "metric"]
    column_filters = ["tenant_id", "metric", "period", "created_at"]
    column_sortable_list = ["tenant_id", "metric", "units", "amount_minor", "created_at"]
    page_size = 50
    
    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id),
    }


# ============================================
# Usage Billing v1 (from cyberplat/product)
# ============================================


class UsageInvoiceAdmin(TenantScopedMixin, ModelView, model=UsageInvoice):
    """Admin view for Usage Invoices (read-only)."""

    name = "Usage Invoice"
    name_plural = "Usage Invoices"
    icon = "fa-solid fa-file-invoice-dollar"
    identity = "usage-invoice"

    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True

    column_list = [
        "id",
        "tenant_id",
        "period_year",
        "period_month",
        "currency",
        "amount_cents",
        "status",
        "payment_status",
        "created_at",
        "finalized_at",
    ]
    column_searchable_list = ["tenant_id", "id"]
    column_filters = ["tenant_id", "payment_status", "period_year", "period_month", "created_at"]
    # Operator comfort: include lifecycle status filter too.
    column_filters += ["status"]
    column_sortable_list = ["created_at", "finalized_at"]
    column_default_sort = ("created_at", True)  # newest first
    page_size = 50

    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id) if getattr(m, "tenant_id", None) else "",
        # Show invoice id + jobs drill-down (count + link to Billing Jobs list with search=invoice_id).
        "id": lambda m, a: Markup(
            f'{safe_text(m.id)} '
            f'<a class="ms-1" href="{build_admin_list_url(ADMIN_ROUTES["billing_job"], {"search": str(m.id)})}" '
            f'title="Open Billing Jobs"><i class="fa-solid fa-tasks"></i> '
            f'{_count_billing_jobs_for_invoice(str(m.tenant_id), str(m.id))}</a>'
        )
        if getattr(m, "id", None) and getattr(m, "tenant_id", None)
        else safe_text(getattr(m, "id", "") or ""),
    }


class UsageInvoiceLineAdmin(ModelView, model=UsageInvoiceLine):
    """Admin view for Usage Invoice Lines (read-only)."""

    name = "Usage Invoice Line"
    name_plural = "Usage Invoice Lines"
    icon = "fa-solid fa-list"
    identity = "usage-invoice-line"

    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True

    column_list = ["invoice_id", "agent_code", "used", "included", "billable", "price_cents", "amount_cents"]
    column_searchable_list = ["invoice_id", "agent_code"]
    column_filters = ["agent_code"]
    page_size = 50


class BillingJobAdmin(TenantScopedMixin, ModelView, model=BillingJob):
    """Admin view for Billing Jobs (read-only)."""

    name = "Billing Job"
    name_plural = "Billing Jobs"
    icon = "fa-solid fa-tasks"
    identity = "billing-job"

    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True

    column_list = [
        "id",
        "tenant_id",
        "invoice_id",
        "provider",
        "provider_ref",
        "status",
        "attempt_count",
        "last_error_code",
        "last_error_message",
        "created_at",
        "updated_at",
        "finished_at",
    ]
    column_searchable_list = ["tenant_id", "invoice_id", "provider_ref", "id", "idempotency_key"]
    column_filters = ["tenant_id", "provider", "status", "created_at"]
    column_default_sort = ("created_at", True)  # newest first
    page_size = 50

    column_formatters = {
        "tenant_id": lambda m, a: tenant_drill_links(m.tenant_id) if getattr(m, "tenant_id", None) else "",
        # invoice_id drill-down to UsageInvoice details
        "invoice_id": lambda m, a: Markup(
            f'<a href="{build_admin_detail_url(ADMIN_ROUTES["usage_invoice"], str(m.invoice_id))}">{safe_text(str(m.invoice_id))}</a>'
        )
        if getattr(m, "invoice_id", None)
        else "",
        # provider_ref drill-down to Search (webhooks by provider_ref are best-effort via raw_json)
        "provider_ref": lambda m, a: Markup(
            f'{safe_text(getattr(m, "provider_ref", "") or "")} '
            f'<a class="ms-1" href="/admin/search?q={safe_path(str(getattr(m, "provider_ref", "") or ""))}" '
            f'title="Search related webhooks"><i class="fa-solid fa-magnifying-glass"></i></a>'
        )
        if getattr(m, "provider_ref", None)
        else "",
        "last_error_message": lambda m, a: Markup(
            f'<span title="{truncate_error(getattr(m, "last_error_message", "") or "", 500)}">'
            f'{truncate_error(getattr(m, "last_error_message", "") or "", 100)}</span>'
        )
        if getattr(m, "last_error_message", None)
        else "",
    }


class TenantPortalTokenAdmin(ModelView, model=TenantPortalToken):
    """Admin view for Tenant Portal Tokens (read-only, platform_admin only)."""
    
    name = "Portal Token"
    name_plural = "Portal Tokens"
    icon = "fa-solid fa-key"
    
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True
    
    column_list = ["id", "tenant_id", "token_prefix", "created_at", "revoked_at"]
    column_searchable_list = ["tenant_id", "token_prefix"]
    column_filters = ["tenant_id", "revoked_at"]
    column_sortable_list = ["tenant_id", "created_at", "revoked_at"]
    column_default_sort = ("created_at", True)
    page_size = 50


class AgentSKUAdmin(ModelView, model=AgentSKU):
    """Admin view for Agent SKUs (platform_admin only, no delete)."""
    
    name = "Agent SKU"
    name_plural = "Agent SKUs"
    icon = "fa-solid fa-robot"
    
    can_create = True
    can_edit = True
    can_delete = False  # Soft policy: use status=disabled instead
    can_view_details = True
    
    column_list = ["code", "name", "status", "pricing_model", "created_at"]
    column_searchable_list = ["code", "name"]
    column_filters = ["status", "pricing_model"]
    column_sortable_list = ["code", "name", "status", "pricing_model", "created_at", "updated_at"]
    column_default_sort = ("created_at", True)
    page_size = 50


class TenantAgentAdmin(ModelView, model=TenantAgent):
    """Admin view for Tenant Agent enablement (platform_admin only, no delete)."""
    
    name = "Tenant Agent"
    name_plural = "Tenant Agents"
    icon = "fa-solid fa-plug"
    
    can_create = True
    can_edit = True
    can_delete = False  # Use status=disabled instead
    can_view_details = True
    
    column_list = ["tenant_id", "agent_sku_id", "status", "activated_at", "disabled_at", "created_at"]
    column_searchable_list = ["tenant_id", "agent_sku_id"]
    column_filters = ["status", "tenant_id"]
    column_sortable_list = ["tenant_id", "status", "activated_at", "disabled_at", "created_at", "updated_at"]
    column_default_sort = ("created_at", True)
    page_size = 50


class TenantAgentSubscriptionAdmin(ModelView, model=TenantAgentSubscription):
    """Admin view for Tenant Agent Subscriptions (platform_admin only, no delete)."""
    
    name = "Agent Subscription"
    name_plural = "Agent Subscriptions"
    icon = "fa-solid fa-credit-card"
    
    can_create = True
    can_edit = True
    can_delete = False  # Use status=canceled instead
    can_view_details = True
    
    column_list = ["tenant_id", "agent_sku_id", "status", "source", "starts_at", "ends_at", "created_at"]
    column_searchable_list = ["tenant_id", "agent_sku_id", "external_ref"]
    column_filters = ["status", "source", "tenant_id"]
    column_sortable_list = ["tenant_id", "status", "source", "starts_at", "ends_at", "created_at"]
    column_default_sort = ("created_at", True)
    page_size = 50
