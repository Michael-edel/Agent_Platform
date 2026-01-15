"""SQLAdmin model views for admin panel."""

from typing import Any
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
)

# Billing models
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    BillingPlan,
    TenantSubscription,
    BillingWebhookEvent,
    BillingOrder,
    BillingUsage,
)

from app.admin.auth import get_admin_role, get_admin_tenant_id


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
    
    column_list = ["id", "tenant_id", "provider", "plan_id", "status", "current_period_start", "current_period_end", "created_at"]
    column_searchable_list = ["tenant_id", "provider", "provider_subscription_id"]
    column_filters = ["tenant_id", "provider", "status", "created_at"]
    column_sortable_list = ["tenant_id", "provider", "status", "created_at"]
    page_size = 50


class BillingWebhookEventAdmin(TenantScopedMixin, ModelView, model=BillingWebhookEvent):
    """Admin view for Billing Webhook Events (read-only)."""
    
    name = "Billing Webhook Event"
    name_plural = "Billing Webhook Events"
    icon = "fa-solid fa-bell"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    # Note: raw_json excluded by listing only safe columns
    column_list = ["id", "provider", "event_id", "tenant_id", "status", "received_at", "processed_at"]
    column_searchable_list = ["event_id", "tenant_id", "provider"]
    column_filters = ["provider", "status", "tenant_id", "received_at"]
    column_sortable_list = ["provider", "status", "received_at", "processed_at"]
    page_size = 50


class BillingOrderAdmin(TenantScopedMixin, ModelView, model=BillingOrder):
    """Admin view for Billing Orders (read-only)."""
    
    name = "Billing Order"
    name_plural = "Billing Orders"
    icon = "fa-solid fa-shopping-cart"
    
    can_create = False
    can_edit = False
    can_delete = False
    
    column_list = ["id", "tenant_id", "provider", "plan_id", "amount_minor", "currency", "status", "created_at", "paid_at"]
    column_searchable_list = ["tenant_id", "provider", "external_order_id"]
    column_filters = ["tenant_id", "provider", "status", "created_at"]
    column_sortable_list = ["tenant_id", "provider", "status", "amount_minor", "created_at"]
    page_size = 50


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
