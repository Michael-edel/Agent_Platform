"""Tenant Portal Web UI - server-rendered HTML pages."""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from app.api.tenant_portal import hash_token
from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import TenantPortalToken, TenantPlan, Plan

logger = logging.getLogger(__name__)

router = APIRouter()

# Templates
templates = Jinja2Templates(directory="templates/tenant_portal")


def get_session_secret() -> Optional[str]:
    """Get session secret from environment."""
    return os.getenv("TENANT_PORTAL_SESSION_SECRET", "").strip() or None


def check_portal_enabled():
    """Check if portal UI is enabled (session secret set)."""
    if not get_session_secret():
        return HTMLResponse(
            "<h3>Service Unavailable</h3><p>Tenant Portal UI is not configured.</p>",
            status_code=503,
        )
    return None


def get_auth_from_session(request: Request) -> Optional[dict]:
    """Get auth info from session."""
    if not request.session.get("portal_auth"):
        return None
    tenant_id = request.session.get("tenant_id")
    if not tenant_id:
        return None
    return {
        "tenant_id": tenant_id,
        "prefix": request.session.get("prefix", ""),
    }


def require_auth(request: Request):
    """Check if user is authenticated, return redirect if not."""
    auth = get_auth_from_session(request)
    if not auth:
        return RedirectResponse("/tenant/login", status_code=302)
    return auth


# ============================================
# Login / Logout
# ============================================

@router.get("/tenant/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    """Render login page."""
    disabled = check_portal_enabled()
    if disabled:
        return disabled
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
    })


@router.post("/tenant/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    tenant_id: str = Form(...),
    portal_key: str = Form(...),
):
    """Handle login form submission."""
    disabled = check_portal_enabled()
    if disabled:
        return disabled
    
    if not tenant_id or not portal_key:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Tenant ID and Portal Key are required",
        })
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            from sqlalchemy import select
            
            query = (
                select(TenantPortalToken)
                .where(TenantPortalToken.tenant_id == tenant_id)
                .where(TenantPortalToken.revoked_at.is_(None))
                .limit(1)
            )
            row = conn.execute(query).fetchone()
            
            if not row:
                logger.warning(f"No active key for tenant {tenant_id}")
                return templates.TemplateResponse("login.html", {
                    "request": request,
                    "error": "Invalid credentials",
                })
            
            stored_hash = row._mapping["token_hash"]
            provided_hash = hash_token(portal_key)
            
            if stored_hash != provided_hash:
                logger.warning(f"Key mismatch for tenant {tenant_id}")
                return templates.TemplateResponse("login.html", {
                    "request": request,
                    "error": "Invalid credentials",
                })
            
            # Success - set session
            request.session["portal_auth"] = True
            request.session["tenant_id"] = tenant_id
            request.session["prefix"] = row._mapping.get("token_prefix", "")
            
            logger.info(f"Tenant {tenant_id} logged into portal")
            return RedirectResponse("/tenant/", status_code=302)
            
    except Exception as e:
        logger.exception("Login error")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Service temporarily unavailable",
        })


@router.post("/tenant/logout")
async def logout(request: Request):
    """Handle logout."""
    request.session.clear()
    return RedirectResponse("/tenant/login", status_code=302)


# ============================================
# Protected Pages
# ============================================

@router.get("/tenant/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Tenant dashboard - status and subscription summary."""
    disabled = check_portal_enabled()
    if disabled:
        return disabled
    
    auth = require_auth(request)
    if isinstance(auth, RedirectResponse):
        return auth
    
    tenant_id = auth["tenant_id"]
    
    # Fetch status and subscription data
    status_data = {"status": "unknown", "webhooks_failed_24h": 0, "orders_failed_24h": 0}
    subscription_data = {"plan_id": None, "subscription_status": None, "provider": None}
    
    try:
        from app.api.tenant_portal import StatusResponse, SubscriptionResponse
        from cyberplat.product.infrastructure.models import TenantPlan
        from cyberplat.billing.infrastructure.models_sqlalchemy import (
            TenantSubscription, BillingWebhookEvent, BillingOrder, BillingUsage
        )
        from sqlalchemy import select, func, desc
        from datetime import timedelta
        
        engine = get_engine()
        now = datetime.now(timezone.utc)
        since_24h = now - timedelta(hours=24)
        
        with engine.connect() as conn:
            # Get subscription
            sub_q = (
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .order_by(desc(TenantSubscription.created_at))
                .limit(1)
            )
            sub_row = conn.execute(sub_q).fetchone()
            if sub_row:
                subscription_data = {
                    "plan_id": sub_row._mapping.get("plan_id"),
                    "subscription_status": sub_row._mapping.get("status"),
                    "provider": sub_row._mapping.get("provider"),
                    "current_period_end": sub_row._mapping.get("current_period_end"),
                }
            else:
                # Try tenant_plans
                plan_q = (
                    select(TenantPlan)
                    .where(TenantPlan.tenant_id == tenant_id)
                    .limit(1)
                )
                plan_row = conn.execute(plan_q).fetchone()
                if plan_row:
                    subscription_data = {
                        "plan_id": plan_row._mapping.get("plan_id"),
                        "subscription_status": plan_row._mapping.get("subscription_status"),
                        "provider": None,
                        "current_period_end": plan_row._mapping.get("expires_at"),
                    }
            
            # Get error counts
            webhook_errors = conn.execute(
                select(func.count())
                .select_from(BillingWebhookEvent)
                .where(BillingWebhookEvent.tenant_id == tenant_id)
                .where(BillingWebhookEvent.status == "failed")
                .where(BillingWebhookEvent.received_at >= since_24h.isoformat())
            ).scalar() or 0
            
            order_errors = conn.execute(
                select(func.count())
                .select_from(BillingOrder)
                .where(BillingOrder.tenant_id == tenant_id)
                .where(BillingOrder.status == "failed")
                .where(BillingOrder.created_at >= since_24h.isoformat())
            ).scalar() or 0
            
            status_data = {
                "status": "ok" if (webhook_errors == 0 and order_errors == 0) else "degraded",
                "webhooks_failed_24h": webhook_errors,
                "orders_failed_24h": order_errors,
            }
    except Exception as e:
        logger.exception("Dashboard data fetch error")
    
    # Get limits for current month
    limits_data = []
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get plan_id
            plan_q = select(TenantPlan.plan_id).where(TenantPlan.tenant_id == tenant_id).limit(1)
            plan_row = conn.execute(plan_q).fetchone()
            
            if plan_row:
                plan_id = plan_row[0]
                
                # Get quotas
                quota_q = select(Plan.quotas).where(Plan.id == plan_id).limit(1)
                quota_row = conn.execute(quota_q).fetchone()
                
                if quota_row and quota_row[0]:
                    try:
                        quotas = json.loads(quota_row[0])
                        if isinstance(quotas, dict):
                            # Get usage
                            usage_q = (
                                select(
                                    BillingUsage.metric,
                                    func.sum(BillingUsage.units).label("total"),
                                )
                                .where(BillingUsage.tenant_id == tenant_id)
                                .where(BillingUsage.period == current_period)
                                .group_by(BillingUsage.metric)
                            )
                            usage_rows = conn.execute(usage_q).fetchall()
                            usage_map = {r[0]: r[1] or 0 for r in usage_rows}
                            
                            for metric in sorted(quotas.keys())[:5]:
                                limit_val = quotas[metric]
                                if isinstance(limit_val, (int, float)) and limit_val > 0:
                                    limit_int = int(limit_val)
                                    used = usage_map.get(metric, 0)
                                    remaining = max(limit_int - used, 0)
                                    util = used / limit_int if limit_int > 0 else 0
                                    
                                    level = "ok"
                                    if util >= 1.0:
                                        level = "critical"
                                    elif util >= 0.8:
                                        level = "warning"
                                    
                                    limits_data.append({
                                        "metric": metric,
                                        "limit": limit_int,
                                        "used": used,
                                        "remaining": remaining,
                                        "utilization": util,
                                        "level": level,
                                    })
                    except (json.JSONDecodeError, TypeError):
                        pass
    except Exception as e:
        logger.exception("Limits data fetch error")
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tenant_id": tenant_id,
        "prefix": auth["prefix"],
        "status": status_data,
        "subscription": subscription_data,
        "limits": limits_data,
        "period": current_period,
    })


@router.get("/tenant/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    period: Optional[str] = Query(None),
):
    """Usage metrics page."""
    disabled = check_portal_enabled()
    if disabled:
        return disabled
    
    auth = require_auth(request)
    if isinstance(auth, RedirectResponse):
        return auth
    
    tenant_id = auth["tenant_id"]
    
    # Default to current month
    now = datetime.now(timezone.utc)
    if not period:
        period = now.strftime("%Y-%m")
    
    # Calculate prev/next periods
    try:
        year, month = map(int, period.split("-"))
        if month == 1:
            prev_period = f"{year-1}-12"
        else:
            prev_period = f"{year}-{month-1:02d}"
        if month == 12:
            next_period = f"{year+1}-01"
        else:
            next_period = f"{year}-{month+1:02d}"
    except:
        prev_period = next_period = period
    
    # Fetch usage data with limits
    totals = []
    limits_map = {}
    
    try:
        from cyberplat.billing.infrastructure.models_sqlalchemy import BillingUsage
        from sqlalchemy import select, func
        
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get plan quotas for limits
            plan_q = select(TenantPlan.plan_id).where(TenantPlan.tenant_id == tenant_id).limit(1)
            plan_row = conn.execute(plan_q).fetchone()
            
            if plan_row:
                quota_q = select(Plan.quotas).where(Plan.id == plan_row[0]).limit(1)
                quota_row = conn.execute(quota_q).fetchone()
                
                if quota_row and quota_row[0]:
                    try:
                        quotas = json.loads(quota_row[0])
                        if isinstance(quotas, dict):
                            limits_map = {k: int(v) for k, v in quotas.items() if isinstance(v, (int, float)) and v > 0}
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            # Get usage
            query = (
                select(
                    BillingUsage.metric,
                    func.sum(BillingUsage.units).label("total_units"),
                    func.sum(BillingUsage.amount_minor).label("total_amount"),
                )
                .where(BillingUsage.tenant_id == tenant_id)
                .where(BillingUsage.period.like(f"{period}%"))
                .group_by(BillingUsage.metric)
                .order_by(BillingUsage.metric)
            )
            rows = conn.execute(query).fetchall()
            
            for row in rows:
                metric = row._mapping["metric"]
                units = row._mapping["total_units"] or 0
                limit_val = limits_map.get(metric)
                remaining = max(limit_val - units, 0) if limit_val else None
                
                totals.append({
                    "metric": metric,
                    "units": units,
                    "amount_minor": row._mapping["total_amount"] or 0,
                    "limit": limit_val,
                    "remaining": remaining,
                })
    except Exception as e:
        logger.exception("Usage data fetch error")
    
    return templates.TemplateResponse("usage.html", {
        "request": request,
        "tenant_id": tenant_id,
        "prefix": auth["prefix"],
        "period": period,
        "prev_period": prev_period,
        "next_period": next_period,
        "totals": totals,
    })


@router.get("/tenant/subscription", response_class=HTMLResponse)
async def subscription_page(request: Request):
    """Subscription details page."""
    disabled = check_portal_enabled()
    if disabled:
        return disabled
    
    auth = require_auth(request)
    if isinstance(auth, RedirectResponse):
        return auth
    
    tenant_id = auth["tenant_id"]
    
    subscription = {}
    plan = {}
    
    try:
        from cyberplat.product.infrastructure.models import TenantPlan, Plan
        from cyberplat.billing.infrastructure.models_sqlalchemy import TenantSubscription
        from sqlalchemy import select, desc
        
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get subscription
            sub_q = (
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .order_by(desc(TenantSubscription.created_at))
                .limit(1)
            )
            sub_row = conn.execute(sub_q).fetchone()
            if sub_row:
                subscription = dict(sub_row._mapping)
            
            # Get tenant plan
            plan_q = (
                select(TenantPlan)
                .where(TenantPlan.tenant_id == tenant_id)
                .limit(1)
            )
            plan_row = conn.execute(plan_q).fetchone()
            if plan_row:
                plan = dict(plan_row._mapping)
    except Exception as e:
        logger.exception("Subscription data fetch error")
    
    return templates.TemplateResponse("subscription.html", {
        "request": request,
        "tenant_id": tenant_id,
        "prefix": auth["prefix"],
        "subscription": subscription,
        "plan": plan,
    })
