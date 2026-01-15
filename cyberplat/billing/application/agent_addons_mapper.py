"""Mapper for billing webhook signals to agent addon subscription events."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated
from app.agents.subscription_handler import handle_agent_addon_subscription_updated

logger = logging.getLogger(__name__)


@dataclass
class NormalizedBillingSignal:
    """Normalized billing signal for agent addon subscription."""
    source: str  # stripe, kaspi, admin, test
    event_type: str
    tenant_id: str
    agent_code: str
    status: str  # active, inactive, canceled, past_due
    external_ref: Optional[str] = None
    effective_at: Optional[str] = None


# Status mapping from payment providers
STRIPE_STATUS_MAP = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "canceled": "canceled",
    "unpaid": "past_due",
    "incomplete": "inactive",
    "incomplete_expired": "canceled",
}

KASPI_STATUS_MAP = {
    "ACTIVE": "active",
    "PAST_DUE": "past_due",
    "CANCELED": "canceled",
    "SUSPENDED": "past_due",
    "PENDING": "inactive",
}


def map_stripe_to_agent_addon_signal(payload: Dict[str, Any]) -> Optional[NormalizedBillingSignal]:
    """
    Map Stripe webhook payload to NormalizedBillingSignal.
    
    Expected payload structure for agent addon events:
    {
        "type": "customer.subscription.updated" | "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_xxx",
                "status": "active|past_due|canceled|...",
                "metadata": {
                    "tenant_id": "...",
                    "agent_code": "...",
                    "addon_type": "agent"  # Required marker
                }
            }
        }
    }
    
    Returns None if not an agent addon event.
    """
    try:
        event_type = payload.get("type", "")
        
        # Only handle subscription events
        if event_type not in ("customer.subscription.updated", "customer.subscription.deleted"):
            return None
        
        data = payload.get("data", {})
        obj = data.get("object", {})
        metadata = obj.get("metadata", {})
        
        # Check if this is an agent addon subscription
        if metadata.get("addon_type") != "agent":
            return None
        
        tenant_id = metadata.get("tenant_id")
        agent_code = metadata.get("agent_code")
        
        if not tenant_id or not agent_code:
            logger.warning("Stripe agent addon event missing tenant_id or agent_code")
            return None
        
        stripe_status = obj.get("status", "")
        status = STRIPE_STATUS_MAP.get(stripe_status)
        
        if not status:
            logger.warning(f"Unknown Stripe subscription status: {stripe_status}")
            return None
        
        # For deleted events, force canceled status
        if event_type == "customer.subscription.deleted":
            status = "canceled"
        
        return NormalizedBillingSignal(
            source="stripe",
            event_type=event_type,
            tenant_id=tenant_id,
            agent_code=agent_code,
            status=status,
            external_ref=obj.get("id"),
            effective_at=datetime.now(timezone.utc).isoformat(),
        )
        
    except Exception as e:
        logger.exception(f"Error mapping Stripe payload: {e}")
        return None


def map_kaspi_to_agent_addon_signal(payload: Dict[str, Any]) -> Optional[NormalizedBillingSignal]:
    """
    Map Kaspi webhook payload to NormalizedBillingSignal.
    
    Expected payload structure for agent addon events:
    {
        "event_type": "SUBSCRIPTION_STATUS_CHANGED",
        "subscription_id": "...",
        "status": "ACTIVE|PAST_DUE|CANCELED|...",
        "tenant_id": "...",
        "agent_code": "...",
        "addon_type": "agent"  # Required marker
    }
    
    Returns None if not an agent addon event.
    """
    try:
        event_type = payload.get("event_type", "")
        
        # Only handle subscription status events
        if event_type != "SUBSCRIPTION_STATUS_CHANGED":
            return None
        
        # Check if this is an agent addon subscription
        if payload.get("addon_type") != "agent":
            return None
        
        tenant_id = payload.get("tenant_id")
        agent_code = payload.get("agent_code")
        
        if not tenant_id or not agent_code:
            logger.warning("Kaspi agent addon event missing tenant_id or agent_code")
            return None
        
        kaspi_status = payload.get("status", "")
        status = KASPI_STATUS_MAP.get(kaspi_status)
        
        if not status:
            logger.warning(f"Unknown Kaspi subscription status: {kaspi_status}")
            return None
        
        return NormalizedBillingSignal(
            source="kaspi",
            event_type=event_type,
            tenant_id=tenant_id,
            agent_code=agent_code,
            status=status,
            external_ref=payload.get("subscription_id"),
            effective_at=datetime.now(timezone.utc).isoformat(),
        )
        
    except Exception as e:
        logger.exception(f"Error mapping Kaspi payload: {e}")
        return None


def is_dry_run_enabled() -> bool:
    """Check if billing webhook dry-run mode is enabled."""
    return os.getenv("BILLING_WEBHOOK_DRY_RUN", "false").lower() == "true"


def dispatch_agent_addon_subscription_update(
    signal: NormalizedBillingSignal,
    session: Session,
    *,
    dry_run: Optional[bool] = None,
) -> bool:
    """
    Dispatch agent addon subscription update.
    
    Args:
        signal: Normalized billing signal
        session: SQLAlchemy session
        dry_run: Override dry-run mode (uses ENV if None)
    
    Returns:
        True if dispatched (or would dispatch in dry-run), False on error
    """
    if dry_run is None:
        dry_run = is_dry_run_enabled()
    
    try:
        event = AgentAddonSubscriptionUpdated(
            tenant_id=signal.tenant_id,
            agent_code=signal.agent_code,
            status=signal.status,
            source=signal.source,
            external_ref=signal.external_ref,
            effective_at=signal.effective_at,
        )
        
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would emit AgentAddonSubscriptionUpdated: "
                f"tenant={signal.tenant_id}, agent={signal.agent_code}, "
                f"status={signal.status}, source={signal.source}"
            )
            return True
        
        result = handle_agent_addon_subscription_updated(session, event)
        
        if result:
            logger.info(
                f"Dispatched AgentAddonSubscriptionUpdated: "
                f"tenant={signal.tenant_id}, agent={signal.agent_code}, "
                f"status={signal.status}"
            )
            return True
        else:
            logger.warning(
                f"Handler returned None for signal: "
                f"tenant={signal.tenant_id}, agent={signal.agent_code}"
            )
            return False
            
    except Exception as e:
        logger.exception(f"Error dispatching agent addon update: {e}")
        return False


def process_webhook_for_agent_addons(
    source: str,
    payload: Dict[str, Any],
    session: Session,
    *,
    dry_run: Optional[bool] = None,
) -> bool:
    """
    Process webhook payload for agent addon subscriptions.
    
    Args:
        source: "stripe" or "kaspi"
        payload: Webhook payload
        session: SQLAlchemy session
        dry_run: Override dry-run mode
    
    Returns:
        True if processed (or not applicable), False on error
    """
    signal = None
    
    if source == "stripe":
        signal = map_stripe_to_agent_addon_signal(payload)
    elif source == "kaspi":
        signal = map_kaspi_to_agent_addon_signal(payload)
    else:
        logger.warning(f"Unknown webhook source: {source}")
        return True  # Not an error, just not applicable
    
    if signal is None:
        # Not an agent addon event, continue with normal processing
        return True
    
    return dispatch_agent_addon_subscription_update(signal, session, dry_run=dry_run)
