"""Agent access guards for tenant enablement checks."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AgentSKU, TenantAgent


class AgentNotFoundError(Exception):
    """Raised when agent SKU does not exist."""
    
    def __init__(self, agent_code: str):
        self.agent_code = agent_code
        super().__init__(f"Agent SKU not found: {agent_code}")


class AgentNotEnabledError(Exception):
    """Raised when agent is not enabled for tenant."""
    
    def __init__(self, tenant_id: str, agent_code: str):
        self.tenant_id = tenant_id
        self.agent_code = agent_code
        super().__init__(f"Agent '{agent_code}' is not enabled for tenant '{tenant_id}'")


def assert_agent_enabled(session: Session, tenant_id: str, agent_code: str) -> TenantAgent:
    """
    Check if agent is enabled for tenant.
    
    Args:
        session: SQLAlchemy session
        tenant_id: Tenant UUID
        agent_code: Agent SKU code (e.g., "sales_assistant")
    
    Returns:
        TenantAgent if enabled
    
    Raises:
        AgentNotFoundError: Agent SKU does not exist
        AgentNotEnabledError: Agent is not enabled for tenant
    """
    # Find AgentSKU by code
    sku = session.execute(
        select(AgentSKU).where(AgentSKU.code == agent_code)
    ).scalar_one_or_none()
    
    if not sku:
        raise AgentNotFoundError(agent_code)
    
    # Find TenantAgent with status=enabled
    tenant_agent = session.execute(
        select(TenantAgent).where(
            TenantAgent.tenant_id == tenant_id,
            TenantAgent.agent_sku_id == sku.id,
            TenantAgent.status == "enabled",
        )
    ).scalar_one_or_none()
    
    if not tenant_agent:
        raise AgentNotEnabledError(tenant_id, agent_code)
    
    return tenant_agent
