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


# ============================================
# Supported Event Types (Strict Allowlists)
# ============================================

STRIPE_SUPPORTED_EVENTS = frozenset({
    "checkout.session.completed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.created",
})

KASPI_SUPPORTED_EVENTS = frozenset({
    "SUBSCRIPTION_STATUS_CHANGED",
    "SUBSCRIPTION_CREATED",
    "SUBSCRIPTION_CANCELED",
    "PAYMENT_COMPLETED",
    "PAYMENT_FAILED",
})


# ============================================
# Status Normalization
# ============================================

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


def normalize_stripe_status(status: str) -> Optional[str]:
    """
    Normalize Stripe subscription status to internal status.
    
    Returns None for unknown statuses (safe no-op).
    """
    if not status or not isinstance(status, str):
        return None
    return STRIPE_STATUS_MAP.get(status.lower())


def normalize_kaspi_status(status: str) -> Optional[str]:
    """
    Normalize Kaspi subscription status to internal status.
    
    Returns None for unknown statuses (safe no-op).
    """
    if not status or not isinstance(status, str):
        return None
    return KASPI_STATUS_MAP.get(status.upper())


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


def map_stripe_to_agent_addon_signal(payload: Dict[str, Any]) -> Optional[NormalizedBillingSignal]:
    """
    Map Stripe webhook payload to NormalizedBillingSignal.
    
    Expected payload structure for agent addon events:
    {
        "type": "customer.subscription.updated" | "customer.subscription.deleted" | ...,
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
    
    Returns None if not an agent addon event (safe no-op).
    Never raises exceptions.
    """
    try:
        if not isinstance(payload, dict):
            logger.debug("Stripe payload is not a dict")
            return None
        
        event_type = payload.get("type", "")
        
        # Strict allowlist check
        if event_type not in STRIPE_SUPPORTED_EVENTS:
            logger.debug(f"Stripe event type not in allowlist: {event_type}")
            return None
        
        # Only subscription events can be agent addons
        if not event_type.startswith("customer.subscription."):
            return None
        
        data = payload.get("data")
        if not isinstance(data, dict):
            logger.debug("Stripe payload missing data object")
            return None
        
        obj = data.get("object")
        if not isinstance(obj, dict):
            logger.debug("Stripe payload missing data.object")
            return None
        
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            return None
        
        # Check if this is an agent addon subscription
        if metadata.get("addon_type") != "agent":
            return None
        
        tenant_id = metadata.get("tenant_id")
        agent_code = metadata.get("agent_code")
        
        if not tenant_id or not agent_code:
            logger.debug("Stripe agent addon event missing tenant_id or agent_code")
            return None
        
        stripe_status = obj.get("status", "")
        status = normalize_stripe_status(stripe_status)
        
        if not status:
            logger.debug(f"Unknown Stripe subscription status: {stripe_status}")
            return None
        
        # For deleted events, force canceled status
        if event_type == "customer.subscription.deleted":
            status = "canceled"
        
        return NormalizedBillingSignal(
            source="stripe",
            event_type=event_type,
            tenant_id=str(tenant_id),
            agent_code=str(agent_code),
            status=status,
            external_ref=obj.get("id"),
            effective_at=datetime.now(timezone.utc).isoformat(),
        )
        
    except Exception as e:
        logger.debug(f"Error mapping Stripe payload (safe no-op): {e}")
        return None


def map_kaspi_to_agent_addon_signal(payload: Dict[str, Any]) -> Optional[NormalizedBillingSignal]:
    """
    Map Kaspi webhook payload to NormalizedBillingSignal.
    
    Expected payload structure for agent addon events:
    {
        "event_type": "SUBSCRIPTION_STATUS_CHANGED" | "SUBSCRIPTION_CANCELED" | ...,
        "subscription_id": "...",
        "status": "ACTIVE|PAST_DUE|CANCELED|...",
        "tenant_id": "...",
        "agent_code": "...",
        "addon_type": "agent"  # Required marker
    }
    
    Returns None if not an agent addon event (safe no-op).
    Never raises exceptions.
    """
    try:
        if not isinstance(payload, dict):
            logger.debug("Kaspi payload is not a dict")
            return None
        
        event_type = payload.get("event_type", "")
        
        # Strict allowlist check
        if event_type not in KASPI_SUPPORTED_EVENTS:
            logger.debug(f"Kaspi event type not in allowlist: {event_type}")
            return None
        
        # Only subscription events can be agent addons
        if not event_type.startswith("SUBSCRIPTION_"):
            return None
        
        # Check if this is an agent addon subscription
        if payload.get("addon_type") != "agent":
            return None
        
        tenant_id = payload.get("tenant_id")
        agent_code = payload.get("agent_code")
        
        if not tenant_id or not agent_code:
            logger.debug("Kaspi agent addon event missing tenant_id or agent_code")
            return None
        
        kaspi_status = payload.get("status", "")
        status = normalize_kaspi_status(kaspi_status)
        
        # For SUBSCRIPTION_CANCELED event, force canceled status
        if event_type == "SUBSCRIPTION_CANCELED":
            status = "canceled"
        elif not status:
            logger.debug(f"Unknown Kaspi subscription status: {kaspi_status}")
            return None
        
        return NormalizedBillingSignal(
            source="kaspi",
            event_type=event_type,
            tenant_id=str(tenant_id),
            agent_code=str(agent_code),
            status=status,
            external_ref=payload.get("subscription_id"),
            effective_at=datetime.now(timezone.utc).isoformat(),
        )
        
    except Exception as e:
        logger.debug(f"Error mapping Kaspi payload (safe no-op): {e}")
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
    
    This function is SAFE to call from any webhook pipeline:
    - Never raises exceptions
    - Returns True for non-applicable events (safe no-op)
    - Only processes events with addon_type="agent"
    
    Args:
        source: "stripe" or "kaspi"
        payload: Webhook payload
        session: SQLAlchemy session
        dry_run: Override dry-run mode
    
    Returns:
        True if processed (or not applicable), False on error
    """
    try:
        signal = None
        
        if source == "stripe":
            signal = map_stripe_to_agent_addon_signal(payload)
        elif source == "kaspi":
            signal = map_kaspi_to_agent_addon_signal(payload)
        else:
            logger.debug(f"Unknown webhook source for agent addons: {source}")
            return True  # Not an error, just not applicable
        
        if signal is None:
            # Not an agent addon event, continue with normal processing
            return True
        
        return dispatch_agent_addon_subscription_update(signal, session, dry_run=dry_run)
        
    except Exception as e:
        logger.debug(f"Error in process_webhook_for_agent_addons (safe no-op): {e}")
        return True  # Fail-safe: don't break main pipeline


def try_process_agent_addon_webhook(source: str, payload: Dict[str, Any]) -> None:
    """
    Try to process webhook for agent addons without breaking main flow.
    
    This is a convenience wrapper that:
    - Creates its own session
    - Handles all errors silently
    - Reads dry_run from ENV
    
    Call this from existing webhook handlers after successful validation.
    """
    try:
        from cyberplat.product.infrastructure.database import get_engine
        from sqlalchemy.orm import Session as SQLAlchemySession
        
        engine = get_engine()
        with SQLAlchemySession(engine) as session:
            process_webhook_for_agent_addons(
                source=source,
                payload=payload,
                session=session,
                dry_run=None,  # Use ENV
            )
    except Exception as e:
        logger.debug(f"try_process_agent_addon_webhook failed silently: {e}")
