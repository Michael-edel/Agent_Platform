"""SQLAdmin setup and configuration."""

import os
import logging
from fastapi import FastAPI

logger = logging.getLogger(__name__)


def setup_admin(app: FastAPI) -> None:
    """
    Configure and mount SQLAdmin to the FastAPI app.
    
    Disabled by default (production-safe).
    Enable only when ADMIN_ENABLED is explicitly set to true/1/yes.
    """
    admin_enabled_raw = os.getenv("ADMIN_ENABLED", "false").strip().lower()
    admin_enabled = admin_enabled_raw in {"true", "1", "yes"}
    
    if not admin_enabled:
        logger.info("Admin panel disabled (ADMIN_ENABLED default false)")
        return
    
    # Check password is set
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_password:
        logger.warning("ADMIN_PASSWORD not set, admin panel will deny all logins")
    
    try:
        from sqladmin import Admin
        
        from cyberplat.product.infrastructure.database import get_engine
        from app.admin.auth import AdminAuthBackend
        from app.admin.dashboard import DashboardView
        from app.admin.search import SearchView
        from app.admin.access_rotate import TokenRotateView
        from app.admin.actions import SafeActionsView
        from app.admin.views import (
            # Product views
            PlanAdmin,
            TenantAdmin,
            TenantBillingSettingsAdmin,
            TenantPlanAdmin,
            WebhookAdmin,
            WebhookDeliveryAdmin,
            KaspiOrderAdmin,
            ArtifactStateAdmin,
            ExportAdmin,
            # Billing views
            BillingPlanAdmin,
            TenantSubscriptionAdmin,
            BillingWebhookEventAdmin,
            BillingOrderAdmin,
            BillingUsageAdmin,
            TenantPortalTokenAdmin,
            AgentSKUAdmin,
            TenantAgentAdmin,
            TenantAgentSubscriptionAdmin,
            UsageInvoiceAdmin,
            UsageInvoiceLineAdmin,
            BillingJobAdmin,
            AdminAuditLogAdmin,
        )
        
        # Get secret key for sessions
        secret_key = os.getenv("ADMIN_SECRET_KEY", os.getenv("SECRET_KEY", "change-me-in-production"))
        # NOTE: SQLAdmin AuthenticationBackend already installs SessionMiddleware
        # on its own mounted Starlette app. Adding SessionMiddleware again on the
        # parent FastAPI app may override/clear admin sessions.
        
        # Get engine
        engine = get_engine()
        
        # Create admin with auth backend and templates
        templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "templates")
        authentication_backend = AdminAuthBackend(secret_key=secret_key)
        admin = Admin(
            app,
            engine,
            authentication_backend=authentication_backend,
            title="Agent Platform Admin",
            templates_dir=templates_dir,
        )
        
        # Add Dashboard first (appears first in menu)
        admin.add_view(DashboardView)
        admin.add_view(SearchView)
        admin.add_view(SafeActionsView)
        
        # Add Product views
        admin.add_view(PlanAdmin)
        admin.add_view(TenantAdmin)
        admin.add_view(TenantBillingSettingsAdmin)
        admin.add_view(TenantPlanAdmin)
        admin.add_view(WebhookAdmin)
        admin.add_view(WebhookDeliveryAdmin)
        admin.add_view(KaspiOrderAdmin)
        admin.add_view(ArtifactStateAdmin)
        admin.add_view(ExportAdmin)
        
        # Add Billing views
        admin.add_view(BillingPlanAdmin)
        admin.add_view(TenantSubscriptionAdmin)
        admin.add_view(BillingWebhookEventAdmin)
        admin.add_view(BillingOrderAdmin)
        admin.add_view(BillingUsageAdmin)
        admin.add_view(BillingJobAdmin)
        admin.add_view(UsageInvoiceAdmin)
        admin.add_view(UsageInvoiceLineAdmin)
        admin.add_view(TenantPortalTokenAdmin)
        admin.add_view(AgentSKUAdmin)
        admin.add_view(TenantAgentAdmin)
        admin.add_view(TenantAgentSubscriptionAdmin)
        admin.add_view(TokenRotateView)
        admin.add_view(AdminAuditLogAdmin)
        
        logger.info("Admin panel enabled at /admin")
        
    except ImportError as e:
        logger.error(f"Failed to setup admin panel: {e}")
    except Exception as e:
        logger.error(f"Error setting up admin panel: {e}")
