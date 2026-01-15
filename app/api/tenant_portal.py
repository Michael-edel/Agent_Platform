"""
Tenant Portal API v1 - Read-only self-service endpoints for tenants.

Provides:
- GET /tenant/subscription - Current subscription info
- GET /tenant/usage - Usage metrics for a period
- GET /tenant/status - Tenant health status
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Header, Query, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, desc

from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import TenantPlan
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


# ============================================
# Auth Dependency
# ============================================

def tenant_portal_auth(
    x_tenant_portal_key: Optional[str] = Header(None, alias="X-Tenant-Portal-Key"),
) -> None:
    """
    Validate tenant portal access key.
    
    Raises:
        HTTPException 503: If TENANT_PORTAL_KEY is not configured
        HTTPException 403: If key is missing or invalid
    """
    portal_key = os.getenv("TENANT_PORTAL_KEY", "")
    
    if not portal_key:
        logger.warning("TENANT_PORTAL_KEY not configured, denying access")
        raise HTTPException(
            status_code=503,
            detail="TENANT_PORTAL_KEY is not configured",
        )
    
    if not x_tenant_portal_key:
        logger.warning("Missing X-Tenant-Portal-Key header")
        raise HTTPException(status_code=403, detail="Missing portal key")
    
    if x_tenant_portal_key != portal_key:
        logger.warning("Invalid X-Tenant-Portal-Key provided")
        raise HTTPException(status_code=403, detail="Invalid portal key")


def get_tenant_id(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> str:
    """Extract and validate tenant ID from header."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")
    return x_tenant_id


# ============================================
# Endpoints
# ============================================

@router.get("/tenant/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    tenant_id: str = Depends(get_tenant_id),
    _auth: None = Depends(tenant_portal_auth),
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
    tenant_id: str = Depends(get_tenant_id),
    period: Optional[str] = Query(None, description="Period in YYYY-MM format"),
    _auth: None = Depends(tenant_portal_auth),
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
    tenant_id: str = Depends(get_tenant_id),
    _auth: None = Depends(tenant_portal_auth),
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
