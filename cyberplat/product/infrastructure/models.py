"""SQLAlchemy models for product/UI layer."""

from sqlalchemy import Column, String, Text, Integer, Boolean, Index, UniqueConstraint, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Используем общий Base, если он есть в проекте, иначе создаём новый
# В production лучше использовать единый Base из одного места
Base = declarative_base()


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
