"""SQLAlchemy models for product/UI layer."""

from sqlalchemy import Column, String, Text, Index, UniqueConstraint
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
