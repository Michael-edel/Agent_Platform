"""Plan limits enforcement module."""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, func

logger = logging.getLogger(__name__)


class PlanLimitExceededError(Exception):
    """Raised when a plan limit would be exceeded."""
    
    def __init__(self, metric: str, limit: int, used: int, tenant_id: str = ""):
        self.metric = metric
        self.limit = limit
        self.used = used
        self.tenant_id = tenant_id
        super().__init__(f"Plan limit exceeded for {metric}: {used}/{limit}")


@dataclass
class LimitCheckResult:
    """Result of a limit check."""
    allowed: bool
    limit: Optional[int] = None
    used: int = 0
    remaining: Optional[int] = None
    reason: Optional[str] = None  # "limit_exceeded", "no_limit", "check_failed"


def check_plan_limit(
    *,
    tenant_id: str,
    metric: str,
    increment: int,
    period: str,
    engine,
) -> LimitCheckResult:
    """
    Check if a tenant can consume more of a metric.
    
    Args:
        tenant_id: Tenant identifier
        metric: Metric name (e.g., "documents", "ocr_pages", "webhooks")
        increment: How many units to add
        period: Period in YYYY-MM format
        engine: SQLAlchemy engine
    
    Returns:
        LimitCheckResult with allowed=True/False and details
    """
    try:
        from cyberplat.product.infrastructure.models import TenantPlan, Plan
        from cyberplat.billing.infrastructure.models_sqlalchemy import BillingUsage
        
        with engine.connect() as conn:
            # Get plan_id for tenant
            plan_query = (
                select(TenantPlan.plan_id)
                .where(TenantPlan.tenant_id == tenant_id)
                .limit(1)
            )
            plan_row = conn.execute(plan_query).fetchone()
            
            if not plan_row:
                # No plan = no limits
                return LimitCheckResult(
                    allowed=True,
                    reason="no_limit",
                )
            
            plan_id = plan_row[0]
            
            # Get plan quotas
            quota_query = (
                select(Plan.quotas)
                .where(Plan.id == plan_id)
                .limit(1)
            )
            quota_row = conn.execute(quota_query).fetchone()
            
            if not quota_row or not quota_row[0]:
                return LimitCheckResult(
                    allowed=True,
                    reason="no_limit",
                )
            
            # Parse quotas
            try:
                quotas = json.loads(quota_row[0])
                if not isinstance(quotas, dict):
                    return LimitCheckResult(allowed=True, reason="no_limit")
            except (json.JSONDecodeError, TypeError):
                return LimitCheckResult(allowed=True, reason="no_limit")
            
            # Check if metric has a limit
            if metric not in quotas:
                return LimitCheckResult(allowed=True, reason="no_limit")
            
            limit_value = quotas[metric]
            if not isinstance(limit_value, (int, float)) or limit_value <= 0:
                return LimitCheckResult(allowed=True, reason="no_limit")
            
            limit_int = int(limit_value)
            
            # Get current usage
            usage_query = (
                select(func.sum(BillingUsage.units))
                .where(BillingUsage.tenant_id == tenant_id)
                .where(BillingUsage.metric == metric)
                .where(BillingUsage.period == period)
            )
            used = conn.execute(usage_query).scalar() or 0
            
            remaining = max(limit_int - used, 0)
            
            # Check if allowed
            if used + increment > limit_int:
                return LimitCheckResult(
                    allowed=False,
                    limit=limit_int,
                    used=used,
                    remaining=remaining,
                    reason="limit_exceeded",
                )
            
            return LimitCheckResult(
                allowed=True,
                limit=limit_int,
                used=used,
                remaining=remaining,
            )
            
    except Exception as e:
        logger.exception(f"Limit check failed for tenant {tenant_id}, metric {metric}")
        return LimitCheckResult(
            allowed=False,
            reason="check_failed",
        )
