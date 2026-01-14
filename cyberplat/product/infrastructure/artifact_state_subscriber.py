"""Event subscriber для автоматического создания/обновления artifact_states."""

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from cyberplat.product.infrastructure.database import get_sessionmaker

logger = logging.getLogger(__name__)


def artifact_state_subscriber(
    event_id: str,
    event_type: str,
    tenant_id: str,
    artifact_id: Optional[str],
    payload: Optional[Dict[str, Any]],
    created_at: str
):
    """
    Event subscriber для автоматического создания/обновления artifact_states.
    
    Обрабатывает события:
    - artifact.created: создаёт state с ui_status в зависимости от kind
    - document.extracted: обновляет state для invoice и связывает source_artifact_id
    
    Args:
        event_id: ID события
        event_type: Тип события
        tenant_id: ID тенанта
        artifact_id: ID артефакта
        payload: Payload события
        created_at: Timestamp создания события
    """
    if not artifact_id or not tenant_id:
        return
    
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    
    try:
        from cyberplat.product.infrastructure.models import ArtifactState
        from cyberplat.artifact_service import ArtifactService
        
        # Получаем artifact_service для доступа к артефактам
        # Используем глобальный ArtifactService (legacy, пока не переведён на SQLAlchemy)
        # Используем тот же db_path, что и для session
        import os
        db_path = os.getenv("PLATFORM_DB_PATH", "platform.db")
        artifact_service = ArtifactService(db_path=db_path)
        
        if event_type == "artifact.created":
            # Получаем kind из payload или из артефакта
            kind = payload.get("kind") if payload else None
            if not kind and artifact_id:
                artifact = artifact_service.get_artifact(artifact_id)
                if artifact:
                    kind = artifact.get("kind")
            
            if not kind:
                logger.warning(f"Cannot determine kind for artifact {artifact_id}")
                return
            
            # Определяем начальный ui_status в зависимости от kind
            if kind == "document":
                ui_status = "uploaded"
            elif kind == "invoice":
                ui_status = "draft"
            elif kind == "payment":
                ui_status = "pending"
            else:
                ui_status = "pending"
            
            # Проверяем, существует ли уже state
            existing_state = session.query(ArtifactState).filter(
                ArtifactState.artifact_id == artifact_id
            ).first()
            
            if not existing_state:
                # Создаём новый state
                import uuid
                now = datetime.now().isoformat()
                state = ArtifactState(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    ui_status=ui_status,
                    created_at=now,
                    updated_at=now
                )
                session.add(state)
                session.commit()
                logger.info(f"Created artifact_state for {kind} artifact {artifact_id} (status={ui_status})")
            else:
                # Обновляем существующий (если нужно)
                if existing_state.ui_status == "pending" and ui_status != "pending":
                    existing_state.ui_status = ui_status
                    existing_state.updated_at = datetime.now().isoformat()
                    session.commit()
                    logger.info(f"Updated artifact_state for artifact {artifact_id} (status={ui_status})")
        
        elif event_type == "document.extracted":
            # Это событие эмитится когда doc_agent создал invoice из document
            # artifact_id в событии = invoice artifact_id
            # source_artifact_id в payload = document artifact_id
            
            if not artifact_id:
                return
            
            source_artifact_id = payload.get("source_artifact_id") if payload else None
            
            # Обновляем state для invoice
            invoice_state = session.query(ArtifactState).filter(
                ArtifactState.artifact_id == artifact_id
            ).first()
            
            if invoice_state:
                # Обновляем source_artifact_id и статус
                invoice_state.source_artifact_id = source_artifact_id
                if invoice_state.ui_status == "draft":
                    invoice_state.ui_status = "pending"  # Invoice готов к подтверждению
                invoice_state.updated_at = datetime.now().isoformat()
                session.commit()
                logger.info(f"Updated invoice state {artifact_id} (source={source_artifact_id})")
            else:
                # Создаём state для invoice (если его ещё нет)
                import uuid
                now = datetime.now().isoformat()
                invoice_state = ArtifactState(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    ui_status="pending",
                    source_artifact_id=source_artifact_id,
                    created_at=now,
                    updated_at=now
                )
                session.add(invoice_state)
                session.commit()
                logger.info(f"Created invoice state {artifact_id} (source={source_artifact_id})")
            
            # Обновляем state для document (mark as extracted)
            if source_artifact_id:
                document_state = session.query(ArtifactState).filter(
                    ArtifactState.artifact_id == source_artifact_id
                ).first()
                
                if document_state:
                    document_state.ui_status = "extracted"
                    document_state.updated_at = datetime.now().isoformat()
                    session.commit()
                    logger.info(f"Updated document state {source_artifact_id} (status=extracted)")
    
    except Exception as e:
        logger.error(f"Error in artifact_state_subscriber for {event_type}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
