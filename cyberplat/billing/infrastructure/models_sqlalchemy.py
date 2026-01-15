"""SQLAlchemy ORM models for billing tables (admin read-only views)."""

from sqlalchemy import Column, Text, Integer, Boolean, ForeignKey, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class BillingPlan(Base):
    """Billing plans table."""
    
    __tablename__ = "billing_plans"
    
    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    currency = Column(Text, nullable=False)
    price_minor = Column(Integer, nullable=False)
    period = Column(Text, nullable=False)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False)


class BillingPlanLimit(Base):
    """Plan limits table."""
    
    __tablename__ = "billing_plan_limits"
    
    id = Column(Text, primary_key=True)
    plan_id = Column(Text, ForeignKey("billing_plans.id"), nullable=False)
    metric = Column(Text, nullable=False)
    monthly_quota = Column(Integer, nullable=True)
    created_at = Column(Text, nullable=False)


class TenantSubscription(Base):
    """Tenant subscriptions table."""
    
    __tablename__ = "tenant_subscriptions"
    
    id = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False, index=True)
    provider = Column(Text, nullable=False, index=True)
    provider_customer_id = Column(Text, nullable=True)
    provider_subscription_id = Column(Text, nullable=True)
    plan_id = Column(Text, ForeignKey("billing_plans.id"), nullable=False)
    status = Column(Text, nullable=False, index=True)
    current_period_start = Column(Text, nullable=True)
    current_period_end = Column(Text, nullable=True)
    updated_at = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class BillingWebhookEvent(Base):
    """Billing webhook events table (idempotency)."""
    
    __tablename__ = "billing_webhook_events"
    
    id = Column(Text, primary_key=True)
    provider = Column(Text, nullable=False, index=True)
    event_id = Column(Text, nullable=False, unique=True, index=True)
    received_at = Column(Text, nullable=False)
    processed_at = Column(Text, nullable=True)
    tenant_id = Column(Text, nullable=True, index=True)
    raw_json = Column(Text, nullable=False)
    status = Column(Text, nullable=False, index=True)
    error = Column(Text, nullable=True)


class BillingOrder(Base):
    """Billing orders table."""
    
    __tablename__ = "billing_orders"
    
    id = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False, index=True)
    provider = Column(Text, nullable=False, index=True)
    plan_id = Column(Text, ForeignKey("billing_plans.id"), nullable=False)
    amount_minor = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False)
    status = Column(Text, nullable=False, index=True)
    external_order_id = Column(Text, nullable=True, index=True)
    created_at = Column(Text, nullable=False)
    paid_at = Column(Text, nullable=True)


class BillingUsage(Base):
    """Billing usage table."""
    
    __tablename__ = "billing_usage"
    
    id = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False, index=True)
    event_id = Column(Text, nullable=False, unique=True)
    artifact_id = Column(Text, nullable=True)
    event_type = Column(Text, nullable=False)
    metric = Column(Text, nullable=False)
    units = Column(Integer, nullable=False)
    unit_price_minor = Column(Integer, nullable=False)
    amount_minor = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False)
    period = Column(Text, nullable=False, index=True)
    created_at = Column(Text, nullable=False)


class BillingTenantPaymentProfile(Base):
    """Tenant payment profiles table."""
    
    __tablename__ = "billing_tenant_payment_profiles"
    
    tenant_id = Column(Text, primary_key=True)
    stripe_customer_id = Column(Text, nullable=True)
    kaspi_token = Column(Text, nullable=True)
    updated_at = Column(Text, nullable=False)
