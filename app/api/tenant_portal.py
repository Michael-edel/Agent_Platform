"""
Tenant Portal API v1 - Read-only self-service endpoints for tenants.

Provides:
- GET /tenant/subscription - Current subscription info
- GET /tenant/usage - Usage metrics for a period
- GET /tenant/status - Tenant health status
"""

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Header, Query, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, desc

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import TenantPlan, TenantPortalToken, Plan
from cyberplat.billing.infrastructure.models_sqlalchemy import (
    TenantSubscription,
    BillingUsage,
    BillingOrder,
    BillingWebhookEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# Pydantic Schemas
# ============================================

class SubscriptionResponse(BaseModel):
    """Subscription information for a tenant."""
    tenant_id: str
    plan_id: Optional[str] = None
    subscription_status: Optional[str] = None
    provider: Optional[str] = None
    current_period_end: Optional[str] = None
    updated_at: Optional[str] = None


class UsageMetric(BaseModel):
    """Single usage metric."""
    metric: str
    units: int
    amount_minor: int


class UsageResponse(BaseModel):
    """Usage totals for a period."""
    tenant_id: str
    period: str
    totals: List[UsageMetric]


class StatusResponse(BaseModel):
    """Tenant health status."""
    tenant_id: str
    webhooks_failed_24h: int
    orders_failed_24h: int
    last_webhook_received_at: Optional[str] = None
    last_error_at: Optional[str] = None
    status: str  # "ok" | "degraded" | "unknown"


class LimitMetric(BaseModel):
    """Single limit metric with usage."""
    metric: str
    limit: int
    used: int
    remaining: int
    utilization: float  # 0..∞


class LimitsNotes(BaseModel):
    """Additional notes about limits."""
    limits_source: str  # "plan" | "none"
    unlimited_metrics: List[str] = []


class LimitsResponse(BaseModel):
    """Plan limits with current usage."""
    tenant_id: str
    period: str
    plan_id: Optional[str] = None
    limits: List[LimitMetric]
    notes: LimitsNotes


# ============================================
# Token Helpers
# ============================================

def generate_portal_token() -> str:
    """Generate a secure random token for portal access."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash token using SHA256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_portal_token(tenant_id: str) -> tuple[str, str]:
    """
    Create a new portal token for tenant.
    
    Returns:
        Tuple of (plain_token, token_id) - plain_token should be shown once
    """
    plain_token = generate_portal_token()
    token_id = str(uuid.uuid4())
    token_hash = hash_token(plain_token)
    token_prefix = plain_token[:8]
    now_iso = datetime.now(timezone.utc).isoformat()
    
    engine = get_engine()
    
    with engine.connect() as conn:
        from sqlalchemy import text
        
        # Revoke existing active tokens for this tenant
        conn.execute(
            text("""
                UPDATE tenant_portal_tokens 
                SET revoked_at = :now 
                WHERE tenant_id = :tenant_id AND revoked_at IS NULL
            """),
            {"tenant_id": tenant_id, "now": now_iso},
        )
        
        # Insert new token
        conn.execute(
            text("""
                INSERT INTO tenant_portal_tokens (id, tenant_id, token_hash, token_prefix, created_at)
                VALUES (:id, :tenant_id, :token_hash, :token_prefix, :created_at)
            """),
            {
                "id": token_id,
                "tenant_id": tenant_id,
                "token_hash": token_hash,
                "token_prefix": token_prefix,
                "created_at": now_iso,
            },
        )
        conn.commit()
    
    return plain_token, token_id


# ============================================
# Auth Dependency
# ============================================

def tenant_portal_auth(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    x_tenant_portal_key: Optional[str] = Header(None, alias="X-Tenant-Portal-Key"),
) -> str:
    """
    Validate per-tenant portal token.
    
    Returns:
        tenant_id on success
    
    Raises:
        HTTPException 400: Missing X-Tenant-ID
        HTTPException 403: Missing or invalid token
        HTTPException 503: Database error (fail-closed)
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")
    
    if not x_tenant_portal_key:
        logger.warning("Missing X-Tenant-Portal-Key header")
        raise HTTPException(status_code=403, detail="Missing portal key")
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get active token for tenant
            query = (
                select(TenantPortalToken)
                .where(TenantPortalToken.tenant_id == x_tenant_id)
                .where(TenantPortalToken.revoked_at.is_(None))
                .limit(1)
            )
            row = conn.execute(query).fetchone()
            
            if not row:
                logger.warning(f"No active token for tenant {x_tenant_id}")
                raise HTTPException(status_code=403, detail="Invalid portal key")
            
            stored_hash = row._mapping["token_hash"]
            provided_hash = hash_token(x_tenant_portal_key)
            
            if stored_hash != provided_hash:
                logger.warning(f"Token mismatch for tenant {x_tenant_id}")
                raise HTTPException(status_code=403, detail="Invalid portal key")
            
            return x_tenant_id
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database error during portal auth")
        raise HTTPException(status_code=503, detail="Service unavailable")


# ============================================
# Endpoints
# ============================================

@router.get("/tenant/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    tenant_id: str = Depends(tenant_portal_auth),
) -> SubscriptionResponse:
    """
    Get current subscription info for tenant.
    
    Returns the most recent plan and subscription data.
    """
    result = SubscriptionResponse(tenant_id=tenant_id)
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get latest TenantPlan
            plan_query = (
                select(TenantPlan)
                .where(TenantPlan.tenant_id == tenant_id)
                .order_by(desc(TenantPlan.created_at))
                .limit(1)
            )
            plan_row = conn.execute(plan_query).fetchone()
            
            if plan_row:
                m = plan_row._mapping
                result.plan_id = m.get("plan_id")
                result.subscription_status = m.get("subscription_status")
                result.updated_at = m.get("created_at")
            
            # Get latest TenantSubscription
            sub_query = (
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .order_by(desc(TenantSubscription.updated_at))
                .limit(1)
            )
            sub_row = conn.execute(sub_query).fetchone()
            
            if sub_row:
                m = sub_row._mapping
                result.provider = m.get("provider")
                result.current_period_end = m.get("current_period_end")
                
                # Use subscription status if plan status is empty
                if not result.subscription_status:
                    result.subscription_status = m.get("status")
                
                # Use latest updated_at
                sub_updated = m.get("updated_at")
                if sub_updated and (not result.updated_at or sub_updated > result.updated_at):
                    result.updated_at = sub_updated
                    
    except Exception as e:
        logger.exception(f"Error getting subscription for tenant {tenant_id}")
    
    return result


@router.get("/tenant/usage", response_model=UsageResponse)
async def get_usage(
    tenant_id: str = Depends(tenant_portal_auth),
    period: Optional[str] = Query(None, description="Period in YYYY-MM format"),
) -> UsageResponse:
    """
    Get usage metrics for a period.
    
    If period is not specified, uses current UTC month.
    """
    # Default to current month
    if not period:
        period = datetime.now(timezone.utc).strftime("%Y-%m")
    
    result = UsageResponse(tenant_id=tenant_id, period=period, totals=[])
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            usage_query = (
                select(
                    BillingUsage.metric,
                    func.sum(BillingUsage.units).label("total_units"),
                    func.sum(BillingUsage.amount_minor).label("total_amount"),
                )
                .where(BillingUsage.tenant_id == tenant_id)
                .where(BillingUsage.period == period)
                .group_by(BillingUsage.metric)
                .order_by(BillingUsage.metric)
            )
            
            rows = conn.execute(usage_query).fetchall()
            
            for row in rows:
                result.totals.append(UsageMetric(
                    metric=row[0],
                    units=row[1] or 0,
                    amount_minor=row[2] or 0,
                ))
                
    except Exception as e:
        logger.exception(f"Error getting usage for tenant {tenant_id}")
    
    return result


@router.get("/tenant/status", response_model=StatusResponse)
async def get_status(
    tenant_id: str = Depends(tenant_portal_auth),
) -> StatusResponse:
    """
    Get tenant health status.
    
    Returns error counts for last 24 hours and overall status.
    """
    result = StatusResponse(
        tenant_id=tenant_id,
        webhooks_failed_24h=0,
        orders_failed_24h=0,
        status="unknown",
    )
    
    try:
        engine = get_engine()
        threshold = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        
        with engine.connect() as conn:
            # Count failed webhooks in last 24h
            webhook_count_query = (
                select(func.count(BillingWebhookEvent.id))
                .where(BillingWebhookEvent.tenant_id == tenant_id)
                .where(BillingWebhookEvent.status == "failed")
                .where(BillingWebhookEvent.received_at >= threshold)
            )
            result.webhooks_failed_24h = conn.execute(webhook_count_query).scalar() or 0
            
            # Count failed orders in last 24h
            order_count_query = (
                select(func.count(BillingOrder.id))
                .where(BillingOrder.tenant_id == tenant_id)
                .where(BillingOrder.status == "failed")
                .where(BillingOrder.created_at >= threshold)
            )
            result.orders_failed_24h = conn.execute(order_count_query).scalar() or 0
            
            # Get last webhook received
            last_webhook_query = (
                select(func.max(BillingWebhookEvent.received_at))
                .where(BillingWebhookEvent.tenant_id == tenant_id)
            )
            result.last_webhook_received_at = conn.execute(last_webhook_query).scalar()
            
            # Get last error timestamp
            last_webhook_error_query = (
                select(func.max(BillingWebhookEvent.received_at))
                .where(BillingWebhookEvent.tenant_id == tenant_id)
                .where(BillingWebhookEvent.status == "failed")
            )
            last_webhook_error = conn.execute(last_webhook_error_query).scalar()
            
            last_order_error_query = (
                select(func.max(BillingOrder.created_at))
                .where(BillingOrder.tenant_id == tenant_id)
                .where(BillingOrder.status == "failed")
            )
            last_order_error = conn.execute(last_order_error_query).scalar()
            
            # Determine last error time
            if last_webhook_error and last_order_error:
                result.last_error_at = max(last_webhook_error, last_order_error)
            else:
                result.last_error_at = last_webhook_error or last_order_error
            
            # Determine status
            total_errors = result.webhooks_failed_24h + result.orders_failed_24h
            result.status = "ok" if total_errors == 0 else "degraded"
            
    except Exception as e:
        logger.exception(f"Error getting status for tenant {tenant_id}")
        result.status = "unknown"
    
    return result


@router.get("/tenant/limits", response_model=LimitsResponse)
async def get_limits(
    tenant_id: str = Depends(tenant_portal_auth),
    period: Optional[str] = Query(None, description="Period in YYYY-MM format"),
) -> LimitsResponse:
    """
    Get plan limits with current usage for a period.
    
    Shows limit, used, remaining, and utilization for each metric.
    """
    # Default to current month
    if not period:
        period = datetime.now(timezone.utc).strftime("%Y-%m")
    
    result = LimitsResponse(
        tenant_id=tenant_id,
        period=period,
        plan_id=None,
        limits=[],
        notes=LimitsNotes(limits_source="none", unlimited_metrics=[]),
    )
    
    try:
        engine = get_engine()
        
        with engine.connect() as conn:
            # Get plan_id for tenant
            plan_query = (
                select(TenantPlan.plan_id)
                .where(TenantPlan.tenant_id == tenant_id)
                .limit(1)
            )
            plan_row = conn.execute(plan_query).fetchone()
            
            if not plan_row:
                return result
            
            plan_id = plan_row[0]
            result.plan_id = plan_id
            
            # Get plan with quotas
            quota_query = (
                select(Plan.quotas)
                .where(Plan.id == plan_id)
                .limit(1)
            )
            quota_row = conn.execute(quota_query).fetchone()
            
            if not quota_row or not quota_row[0]:
                return result
            
            # Parse quotas JSON
            try:
                quotas = json.loads(quota_row[0])
                if not isinstance(quotas, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                return result
            
            result.notes.limits_source = "plan"
            
            # Get usage for this period
            usage_query = (
                select(
                    BillingUsage.metric,
                    func.sum(BillingUsage.units).label("total_units"),
                )
                .where(BillingUsage.tenant_id == tenant_id)
                .where(BillingUsage.period == period)
                .group_by(BillingUsage.metric)
            )
            usage_rows = conn.execute(usage_query).fetchall()
            usage_map = {row[0]: row[1] or 0 for row in usage_rows}
            
            # Build limits list
            unlimited_metrics = []
            for metric in sorted(quotas.keys()):
                limit_value = quotas[metric]
                if not isinstance(limit_value, (int, float)) or limit_value <= 0:
                    unlimited_metrics.append(metric)
                    continue
                
                limit_int = int(limit_value)
                used = usage_map.get(metric, 0)
                remaining = max(limit_int - used, 0)
                utilization = used / limit_int if limit_int > 0 else 0.0
                
                result.limits.append(LimitMetric(
                    metric=metric,
                    limit=limit_int,
                    used=used,
                    remaining=remaining,
                    utilization=round(utilization, 4),
                ))
            
            result.notes.unlimited_metrics = unlimited_metrics
            
    except Exception as e:
        logger.exception(f"Error getting limits for tenant {tenant_id}")
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    return result
