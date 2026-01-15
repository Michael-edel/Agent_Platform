"""SQLAlchemy models for product/UI layer."""

import os

from sqlalchemy import Column, String, Text, Integer, Boolean, Index, UniqueConstraint, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Используем общий Base, если он есть в проекте, иначе создаём новый
# В production лучше использовать единый Base из одного места
Base = declarative_base()


def _public_base_url() -> str:
    raw = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return raw or "http://localhost:8000"


class Tenant(Base):
    """Minimal tenants registry for operational onboarding."""

    __tablename__ = "tenants"

    id = Column(String, primary_key=True)  # tenant_id
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        Index("idx_tenants_is_active", "is_active"),
        Index("idx_tenants_created_at", "created_at"),
    )

    @property
    def stripe_webhook_url(self) -> str:
        # Inbound billing webhook endpoint (tenant inferred by payload / metadata)
        return f"{_public_base_url()}/api/v1/billing/webhook/stripe"

    @property
    def kaspi_webhook_url(self) -> str:
        return f"{_public_base_url()}/api/v1/billing/webhook/kaspi"


class TenantBillingSettings(Base):
    """Per-tenant billing provider settings (override global defaults)."""

    __tablename__ = "tenant_billing_settings"

    tenant_id = Column(String, primary_key=True)  # matches tenants.id
    default_provider = Column(String, nullable=True)  # kaspi|stripe|null -> env default
    stripe_enabled = Column(Boolean, nullable=False, default=True)
    kaspi_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        Index("idx_tenant_billing_settings_tenant_id", "tenant_id"),
        Index("idx_tenant_billing_settings_default_provider", "default_provider"),
    )


class ArtifactState(Base):
    """SQLAlchemy model для artifact_states."""
    
    __tablename__ = "artifact_states"
    
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    artifact_id = Column(String, nullable=False, unique=True, index=True)
    ui_status = Column(String, nullable=False, default="pending", index=True)
    source_artifact_id = Column(String, nullable=True, index=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    confirmed_at = Column(String, nullable=True)  # ISO format timestamp
    exported_at = Column(String, nullable=True)  # ISO format timestamp
    export_target = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_artifact_states_artifact_id"),
        Index("idx_artifact_states_tenant_id", "tenant_id"),
        Index("idx_artifact_states_artifact_id", "artifact_id"),
        Index("idx_artifact_states_ui_status", "ui_status"),
        Index("idx_artifact_states_source_artifact_id", "source_artifact_id"),
        Index("idx_artifact_states_created_at", "created_at"),
    )


class Export(Base):
    """SQLAlchemy model для exports."""
    
    __tablename__ = "exports"
    
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    artifact_id = Column(String, nullable=False, index=True)
    export_type = Column(String, nullable=False, index=True)
    file_id = Column(String, nullable=True)
    file_path = Column(String, nullable=True)
    export_config = Column(Text, nullable=True)  # JSON string
    status = Column(String, nullable=False, default="pending", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(String, nullable=False, index=True)
    completed_at = Column(String, nullable=True)
    
    __table_args__ = (
        Index("idx_exports_tenant_id", "tenant_id"),
        Index("idx_exports_artifact_id", "artifact_id"),
        Index("idx_exports_export_type", "export_type"),
        Index("idx_exports_status", "status"),
        Index("idx_exports_created_at", "created_at"),
    )


class EmailOcrJob(Base):
    """SQLAlchemy model для email_ocr_jobs (идемпотентность и ретраи auto-OCR)."""
    
    __tablename__ = "email_ocr_jobs"
    
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    document_artifact_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False, default="queued", index=True)  # queued|processing|done|failed|dead
    attempts = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=5)
    next_run_at = Column(String, nullable=True)  # ISO format timestamp
    last_error = Column(Text, nullable=True)  # Короткое сообщение об ошибке (без контента)
    invoice_artifact_id = Column(String, nullable=True)  # Созданный invoice artifact
    # Email metadata для inbox
    email_from = Column(Text, nullable=True)
    email_to = Column(Text, nullable=True)
    email_subject = Column(Text, nullable=True)
    attachment_filename = Column(Text, nullable=True)
    attachment_size = Column(Integer, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_email_ocr_jobs_idempotency_key"),
        Index("idx_email_ocr_jobs_tenant_id", "tenant_id"),
        Index("idx_email_ocr_jobs_document_artifact_id", "document_artifact_id"),
        Index("idx_email_ocr_jobs_status", "status"),
        Index("idx_email_ocr_jobs_idempotency_key", "idempotency_key"),
        Index("idx_email_ocr_jobs_tenant_status_next_run", "tenant_id", "status", "next_run_at"),
        Index("idx_email_ocr_jobs_tenant_created", "tenant_id", "created_at"),
    )


class Webhook(Base):
    """SQLAlchemy model для webhooks (конфигурация outgoing webhooks)."""
    
    __tablename__ = "webhooks"
    
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    url = Column(Text, nullable=False)
    events = Column(Text, nullable=False)  # JSON array: ["invoice.ready", ...]
    secret = Column(Text, nullable=False)  # Для HMAC подписи
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(String, nullable=False)
    
    __table_args__ = (
        Index("idx_webhooks_tenant_id", "tenant_id"),
        Index("idx_webhooks_active", "active"),
        Index("idx_webhooks_tenant_active", "tenant_id", "active"),
    )


class WebhookDelivery(Base):
    """SQLAlchemy model для webhook_deliveries (история доставок)."""
    
    __tablename__ = "webhook_deliveries"
    
    id = Column(String, primary_key=True)
    webhook_id = Column(String, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)  # JSON payload
    status = Column(String, nullable=False, default="queued", index=True)  # queued|sent|failed|dead
    attempts = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=5)
    last_error = Column(Text, nullable=True)
    next_run_at = Column(String, nullable=True)  # ISO timestamp для retry
    created_at = Column(String, nullable=False)
    sent_at = Column(String, nullable=True)  # ISO timestamp когда успешно отправлен
    
    __table_args__ = (
        Index("idx_webhook_deliveries_webhook_id", "webhook_id"),
        Index("idx_webhook_deliveries_status", "status"),
        Index("idx_webhook_deliveries_event_type", "event_type"),
        Index("idx_webhook_deliveries_next_run", "status", "next_run_at"),
        Index("idx_webhook_deliveries_status_next_run", "status", "next_run_at"),
    )


class Plan(Base):
    """SQLAlchemy model для plans (тарифные планы)."""
    
    __tablename__ = "plans"
    
    id = Column(String, primary_key=True)  # trial, pro, enterprise
    name = Column(String, nullable=False)  # Trial, Pro, Enterprise
    description = Column(Text, nullable=True)
    quotas = Column(Text, nullable=False)  # JSON: {"document_upload": 20, ...}
    price_minor = Column(Integer, nullable=True)  # null для trial
    currency = Column(String, nullable=True)  # null для trial
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(String, nullable=False)
    
    __table_args__ = (
        Index("idx_plans_active", "active"),
    )


class TenantPlan(Base):
    """SQLAlchemy model для tenant_plans (назначенные планы для tenant)."""
    
    __tablename__ = "tenant_plans"
    
    tenant_id = Column(String, primary_key=True)
    plan_id = Column(String, nullable=False, index=True)
    started_at = Column(String, nullable=False)  # ISO timestamp
    expires_at = Column(String, nullable=True, index=True)  # ISO timestamp, null для paid планов
    subscription_status = Column(String, nullable=True, index=True)  # trial|active|past_due|canceled
    failed_charges = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False)
    
    __table_args__ = (
        Index("idx_tenant_plans_tenant_id", "tenant_id"),
        Index("idx_tenant_plans_plan_id", "plan_id"),
        Index("idx_tenant_plans_expires_at", "expires_at"),
        Index("idx_tenant_plans_subscription_status", "subscription_status"),
    )


class KaspiOrder(Base):
    """SQLAlchemy model для kaspi_orders (link kaspi_order_id → tenant_id + plan_id)."""

    __tablename__ = "kaspi_orders"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    plan_id = Column(String, nullable=False, index=True)  # plans.id: pro | enterprise
    kaspi_order_id = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False, default="created", index=True)  # created|paid|failed|canceled
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    paid_at = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)  # JSON (safe fields only)

    __table_args__ = (
        UniqueConstraint("kaspi_order_id", name="uq_kaspi_orders_kaspi_order_id"),
        Index("idx_kaspi_orders_tenant_id", "tenant_id"),
        Index("idx_kaspi_orders_plan_id", "plan_id"),
        Index("idx_kaspi_orders_status", "status"),
        Index("idx_kaspi_orders_kaspi_order_id", "kaspi_order_id"),
    )


class TenantPortalToken(Base):
    """SQLAlchemy model for per-tenant portal access tokens."""
    
    __tablename__ = "tenant_portal_tokens"
    
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    token_hash = Column(String, nullable=False)  # SHA256 hex
    token_prefix = Column(String, nullable=False)  # First 8 chars for display
    created_at = Column(String, nullable=False)
    revoked_at = Column(String, nullable=True, index=True)
    
    __table_args__ = (
        Index("idx_tenant_portal_tokens_tenant_id", "tenant_id"),
        Index("idx_tenant_portal_tokens_revoked_at", "revoked_at"),
        Index("idx_tenant_portal_tokens_tenant_active", "tenant_id", "revoked_at"),
    )


class AgentSKU(Base):
    """SQLAlchemy model for agent SKUs (product catalog)."""
    
    __tablename__ = "agent_skus"
    
    id = Column(String, primary_key=True)  # UUID
    code = Column(String, nullable=False, unique=True, index=True)  # e.g., "sales_assistant"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active", index=True)  # active, deprecated, disabled
    pricing_model = Column(String, nullable=False)  # subscription, usage_based
    # Usage pricing v1 (minimal schema)
    usage_enabled = Column(Boolean, nullable=False, default=False)
    usage_price_cents = Column(Integer, nullable=True)
    usage_included_per_month = Column(Integer, nullable=False, default=0)
    usage_unit = Column(String, nullable=False, default="execution")
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("code", name="uq_agent_skus_code"),
        Index("idx_agent_skus_code", "code"),
        Index("idx_agent_skus_status", "status"),
    )


class TenantAgent(Base):
    """SQLAlchemy model for tenant-agent enablement."""
    
    __tablename__ = "tenant_agents"
    
    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    agent_sku_id = Column(String, ForeignKey("agent_skus.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="enabled", index=True)  # enabled, disabled, suspended
    activated_at = Column(String, nullable=True)
    disabled_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_sku_id", name="uq_tenant_agent"),
        Index("idx_tenant_agents_tenant_id", "tenant_id"),
        Index("idx_tenant_agents_agent_sku_id", "agent_sku_id"),
        Index("idx_tenant_agents_status", "status"),
    )


class TenantAgentSubscription(Base):
    """SQLAlchemy model for tenant agent add-on subscriptions."""
    
    __tablename__ = "tenant_agent_subscriptions"
    
    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    agent_sku_id = Column(String, ForeignKey("agent_skus.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="inactive", index=True)  # active, inactive, canceled, past_due
    starts_at = Column(String, nullable=False)
    ends_at = Column(String, nullable=True)
    cancel_at_period_end = Column(Integer, nullable=False, default=0)  # Boolean as int for SQLite
    source = Column(String, nullable=False, default="admin")  # admin, kaspi, stripe
    external_ref = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_sku_id", name="uq_tenant_agent_subscription"),
        Index("idx_tenant_agent_subscriptions_tenant_id", "tenant_id"),
        Index("idx_tenant_agent_subscriptions_agent_sku_id", "agent_sku_id"),
        Index("idx_tenant_agent_subscriptions_status", "status"),
    )


class AgentExecution(Base):
    """SQLAlchemy model for agent execution records."""
    
    __tablename__ = "agent_executions"
    
    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    agent_sku_id = Column(String, ForeignKey("agent_skus.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="accepted", index=True)  # accepted, running, completed, failed, rejected
    idempotency_key = Column(String, nullable=False)
    input_json = Column(Text, nullable=False)  # JSON as text for SQLite compatibility
    result_json = Column(Text, nullable=True)  # JSON as text
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    cancel_requested_at = Column(String, nullable=True)
    usage_counted_at = Column(String, nullable=True)
    
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_sku_id", "idempotency_key", name="uq_execution_idempotency"),
        Index("idx_agent_executions_tenant_created", "tenant_id", "created_at"),
        Index("idx_agent_executions_status", "status"),
    )


class TenantUsageMonthly(Base):
    """Monthly usage aggregation for usage-based billing (completed executions only)."""

    __tablename__ = "tenant_usage_monthly"

    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    agent_code = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    completed_executions = Column(Integer, nullable=False, default=0)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_code", "year", "month", name="uq_tenant_usage_monthly"),
        Index("idx_tenant_usage_monthly_tenant_period", "tenant_id", "year", "month"),
        Index("idx_tenant_usage_monthly_agent_period", "agent_code", "year", "month"),
    )


class UsageInvoice(Base):
    """Finalized usage invoice snapshot for a billing period."""

    __tablename__ = "usage_invoices"

    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    period_year = Column(Integer, nullable=False)
    period_month = Column(Integer, nullable=False)
    currency = Column(String, nullable=False)
    amount_cents = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="finalized")
    payment_status = Column(String, nullable=False, default="unpaid")  # unpaid|processing|paid|failed
    payment_status_updated_at = Column(String, nullable=True)
    paid_at = Column(String, nullable=True)
    failed_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    finalized_at = Column(String, nullable=True)
    event_emitted_at = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "period_year", "period_month", name="uq_usage_invoice_period"),
        Index("idx_usage_invoices_tenant_period", "tenant_id", "period_year", "period_month"),
    )


class UsageInvoiceLine(Base):
    """Finalized usage invoice line item."""

    __tablename__ = "usage_invoice_lines"

    id = Column(String, primary_key=True)  # UUID
    invoice_id = Column(String, ForeignKey("usage_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_code = Column(String, nullable=False, index=True)
    unit = Column(String, nullable=False, default="execution")
    used = Column(Integer, nullable=False, default=0)
    included = Column(Integer, nullable=False, default=0)
    billable = Column(Integer, nullable=False, default=0)
    price_cents = Column(Integer, nullable=False, default=0)
    amount_cents = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_usage_invoice_lines_invoice", "invoice_id"),
        Index("idx_usage_invoice_lines_agent", "agent_code"),
    )


class BillingJob(Base):
    """Billing jobs queue for invoice processing (no external calls yet)."""

    __tablename__ = "billing_jobs"

    id = Column(String, primary_key=True)  # UUID
    tenant_id = Column(String, nullable=False, index=True)
    invoice_id = Column(String, ForeignKey("usage_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False, default="dry_run")
    status = Column(String, nullable=False, default="pending")  # pending|pending_retry|processing|succeeded|failed
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    last_error_code = Column(String, nullable=True)
    last_error_message = Column(String, nullable=True)
    last_attempt_at = Column(String, nullable=True)
    next_attempt_at = Column(String, nullable=True)
    locked_at = Column(String, nullable=True)
    locked_by = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    processing_started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    provider_ref = Column(String, nullable=True, index=True)

    __table_args__ = (
        Index("idx_billing_jobs_status_next", "status", "next_attempt_at"),
        Index("idx_billing_jobs_invoice", "invoice_id"),
    )


class AdminAuditLog(Base):
    """Admin audit log (append-only, safe metadata)."""

    __tablename__ = "admin_audit_log"

    id = Column(String, primary_key=True)  # UUID
    created_at = Column(String, nullable=False)
    actor_username = Column(String, nullable=False)
    actor_role = Column(String, nullable=False)
    tenant_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, nullable=False, index=True)
    metadata_json = Column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_admin_audit_created_at", "created_at"),
        Index("idx_admin_audit_actor", "actor_username"),
    )
