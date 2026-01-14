"""Domain events for billing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass(frozen=True)
class SubscriptionActivated:
    """Событие активации подписки."""
    tenant_id: str
    plan_id: str
    provider: str
    provider_subscription_id: Optional[str]
    period_start: str  # ISO format
    period_end: str  # ISO format
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class SubscriptionRenewed:
    """Событие продления подписки."""
    tenant_id: str
    plan_id: str
    provider: str
    provider_subscription_id: Optional[str]
    period_start: str  # ISO format
    period_end: str  # ISO format
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class SubscriptionCanceled:
    """Событие отмены подписки."""
    tenant_id: str
    provider: str
    provider_subscription_id: Optional[str]
    reason: Optional[str]
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class PlanApplied:
    """Событие применения плана к тенанту."""
    tenant_id: str
    plan_id: str
    provider: str
    period_start: str  # ISO format
    period_end: str  # ISO format
    status: str
    metadata: Dict[str, Any]
