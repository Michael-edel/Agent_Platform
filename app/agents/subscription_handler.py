"""Handler for agent addon subscription events."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentSKU, TenantAgentSubscription
from cyberplat.billing.domain.events import AgentAddonSubscriptionUpdated

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_agent_addon_subscription_updated(
    session: Session,
    event: AgentAddonSubscriptionUpdated,
) -> Optional[TenantAgentSubscription]:
    """
    Handle agent addon subscription update event.
    
    Idempotent upsert of TenantAgentSubscription.
    
    Args:
        session: SQLAlchemy session
        event: The subscription update event
    
    Returns:
        Updated/created TenantAgentSubscription or None if agent not found
    """
    # Find AgentSKU by code
    sku = session.execute(
        select(AgentSKU).where(AgentSKU.code == event.agent_code)
    ).scalar_one_or_none()
    
    if not sku:
        logger.warning(f"Agent SKU not found for code: {event.agent_code}")
        return None
    
    # Find existing subscription
    subscription = session.execute(
        select(TenantAgentSubscription).where(
            TenantAgentSubscription.tenant_id == event.tenant_id,
            TenantAgentSubscription.agent_sku_id == sku.id,
        )
    ).scalar_one_or_none()
    
    now = event.effective_at or now_iso()
    
    if subscription:
        # Update existing subscription
        subscription.status = event.status
        subscription.source = event.source
        subscription.updated_at = now
        
        if event.external_ref:
            subscription.external_ref = event.external_ref
        
        # Handle status-specific logic
        if event.status == "active" and not subscription.starts_at:
            subscription.starts_at = now
            subscription.ends_at = None
        elif event.status == "canceled" and not subscription.ends_at:
            subscription.ends_at = now
        elif event.status == "active":
            subscription.ends_at = None  # Clear ends_at when reactivated
    else:
        # Create new subscription
        subscription = TenantAgentSubscription(
            id=str(uuid.uuid4()),
            tenant_id=event.tenant_id,
            agent_sku_id=sku.id,
            status=event.status,
            starts_at=now if event.status == "active" else now,
            ends_at=now if event.status == "canceled" else None,
            source=event.source,
            external_ref=event.external_ref,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
    
    session.commit()
    
    logger.info(
        f"Agent subscription updated: tenant={event.tenant_id}, "
        f"agent={event.agent_code}, status={event.status}"
    )
    
    return subscription


def update_agent_subscription_via_event(
    session: Session,
    tenant_id: str,
    agent_code: str,
    status: str,
    source: str = "admin",
    external_ref: Optional[str] = None,
) -> Optional[TenantAgentSubscription]:
    """
    Service function to update subscription via event.
    
    Creates and processes AgentAddonSubscriptionUpdated event.
    Can be used from Admin, tests, or public API.
    """
    event = AgentAddonSubscriptionUpdated(
        tenant_id=tenant_id,
        agent_code=agent_code,
        status=status,
        source=source,
        external_ref=external_ref,
        effective_at=now_iso(),
    )
    
    return handle_agent_addon_subscription_updated(session, event)
