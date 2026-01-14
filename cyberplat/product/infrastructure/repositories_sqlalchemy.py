"""SQLAlchemy-based repository implementations for product/UI layer."""

import uuid
import logging
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text

from cyberplat.product.domain.interfaces import (
    ArtifactStateRepository,
    ExportRepository
)
from cyberplat.product.infrastructure.models import ArtifactState, Export

logger = logging.getLogger(__name__)


class ArtifactStateRepositoryImpl(ArtifactStateRepository):
    """SQLAlchemy-based repository implementation для artifact states."""
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy session (injected via dependency)
        """
        self.session = session
    
    def get_state(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить состояние артефакта."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return None
        
        return {
            "id": state.id,
            "tenant_id": state.tenant_id,
            "artifact_id": state.artifact_id,
            "ui_status": state.ui_status,
            "source_artifact_id": state.source_artifact_id,
            "error_code": state.error_code,
            "error_message": state.error_message,
            "confirmed_at": state.confirmed_at,
            "exported_at": state.exported_at,
            "export_target": state.export_target,
            "created_at": state.created_at,
            "updated_at": state.updated_at
        }
    
    def create_or_update_state(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        source_artifact_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> str:
        """Создать или обновить состояние артефакта."""
        state = self.session.query(ArtifactState).filter(
            ArtifactState.artifact_id == artifact_id
        ).first()
        
        now = datetime.now().isoformat()
        
        if state:
            # Обновляем существующее (с проверкой tenant_id)
            if state.tenant_id != tenant_id:
                raise ValueError(f"Artifact {artifact_id} belongs to different tenant")
            
            state.ui_status = ui_status
            state.source_artifact_id = source_artifact_id
            state.error_code = error_code
            state.error_message = error_message
            state.updated_at = now
        else:
            # Создаём новое
            state_id = str(uuid.uuid4())
            state = ArtifactState(
                id=state_id,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                ui_status=ui_status,
                source_artifact_id=source_artifact_id,
                error_code=error_code,
                error_message=error_message,
                created_at=now,
                updated_at=now
            )
            self.session.add(state)
            state_id = state.id
        
        self.session.commit()
        return state.id
    
    def update_status(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус артефакта."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        state.ui_status = ui_status
        state.error_code = error_code
        state.error_message = error_message
        state.updated_at = datetime.now().isoformat()
        
        self.session.commit()
        return True
    
    def mark_confirmed(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> bool:
        """Отметить артефакт как подтверждённый."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        now = datetime.now().isoformat()
        state.ui_status = "confirmed"
        state.confirmed_at = now
        state.updated_at = now
        
        self.session.commit()
        return True
    
    def mark_exported(
        self,
        tenant_id: str,
        artifact_id: str,
        export_target: str
    ) -> bool:
        """Отметить артефакт как экспортированный."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        now = datetime.now().isoformat()
        state.ui_status = "exported"
        state.exported_at = now
        state.export_target = export_target
        state.updated_at = now
        
        self.session.commit()
        return True
    
    def list_by_kind_and_status(
        self,
        tenant_id: str,
        kind: str,
        ui_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список артефактов по kind и статусу.
        
        Возвращает UI-проекции (без raw OCR JSON из artifacts.data).
        Использует raw SQL для JOIN с artifacts (так как artifacts не SQLAlchemy model).
        """
        # Используем raw SQL для JOIN с artifacts (legacy таблица)
        query = """
            SELECT 
                a.id as artifact_id,
                a.kind,
                a.source,
                a.tenant_id,
                a.created_at as artifact_created_at,
                s.id as state_id,
                s.ui_status,
                s.source_artifact_id,
                s.error_code,
                s.error_message,
                s.confirmed_at,
                s.exported_at,
                s.export_target,
                s.created_at as state_created_at,
                s.updated_at as state_updated_at
            FROM artifacts a
            LEFT JOIN artifact_states s ON a.id = s.artifact_id AND s.tenant_id = a.tenant_id
            WHERE a.tenant_id = :tenant_id AND a.kind = :kind
        """
        
        params = {"tenant_id": tenant_id, "kind": kind}
        
        if ui_status:
            query += " AND (s.ui_status = :ui_status OR (s.ui_status IS NULL AND :ui_status = 'pending'))"
            params["ui_status"] = ui_status
        
        query += " ORDER BY a.created_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset
        
        # Используем text() для raw SQL
        result = self.session.execute(text(query), params)
        rows = result.fetchall()
        
        results = []
        for row in rows:
            # SQLAlchemy Row доступ через атрибуты (имена колонок из SELECT)
            # UI-проекция: не возвращаем raw OCR JSON
            result_dict = {
                "id": row.artifact_id,
                "kind": row.kind,
                "source": row.source,
                "tenant_id": row.tenant_id,
                "created_at": row.artifact_created_at,
                "state": {
                    "ui_status": row.ui_status or "pending",
                    "source_artifact_id": row.source_artifact_id,
                    "error_code": row.error_code,
                    "error_message": row.error_message,
                    "confirmed_at": row.confirmed_at,
                    "exported_at": row.exported_at,
                    "export_target": row.export_target,
                    "updated_at": row.state_updated_at or row.artifact_created_at
                } if row.state_id else {
                    "ui_status": "pending",
                    "source_artifact_id": None,
                    "error_code": None,
                    "error_message": None,
                    "confirmed_at": None,
                    "exported_at": None,
                    "export_target": None,
                    "updated_at": row.artifact_created_at
                }
            }
            results.append(result_dict)
        
        return results


class ExportRepositoryImpl(ExportRepository):
    """SQLAlchemy-based repository implementation для exports."""
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy session (injected via dependency)
        """
        self.session = session
    
    def create_export(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Создать запись об экспорте."""
        export_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        export = Export(
            id=export_id,
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type=export_type,
            export_config=json.dumps(export_config) if export_config else None,
            status="pending",
            created_at=now
        )
        
        self.session.add(export)
        self.session.commit()
        
        return export_id
    
    def update_export(
        self,
        export_id: str,
        status: str,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус экспорта."""
        export = self.session.query(Export).filter(Export.id == export_id).first()
        
        if not export:
            return False
        
        export.status = status
        export.file_id = file_id
        export.file_path = file_path
        export.error_message = error_message
        export.completed_at = datetime.now().isoformat() if status == "completed" else None
        
        self.session.commit()
        return True
    
    def get_export(
        self,
        tenant_id: str,
        export_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить экспорт по ID."""
        export = self.session.query(Export).filter(
            and_(
                Export.id == export_id,
                Export.tenant_id == tenant_id
            )
        ).first()
        
        if not export:
            return None
        
        return {
            "id": export.id,
            "tenant_id": export.tenant_id,
            "artifact_id": export.artifact_id,
            "export_type": export.export_type,
            "file_id": export.file_id,
            "file_path": export.file_path,
            "export_config": json.loads(export.export_config) if export.export_config else None,
            "status": export.status,
            "error_message": export.error_message,
            "created_at": export.created_at,
            "completed_at": export.completed_at
        }
    
    def list_exports(
        self,
        tenant_id: str,
        artifact_id: Optional[str] = None,
        export_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить список экспортов."""
        query = self.session.query(Export).filter(Export.tenant_id == tenant_id)
        
        if artifact_id:
            query = query.filter(Export.artifact_id == artifact_id)
        
        if export_type:
            query = query.filter(Export.export_type == export_type)
        
        exports = query.order_by(Export.created_at.desc()).limit(limit).offset(offset).all()
        
        results = []
        for export in exports:
            results.append({
                "id": export.id,
                "tenant_id": export.tenant_id,
                "artifact_id": export.artifact_id,
                "export_type": export.export_type,
                "file_id": export.file_id,
                "file_path": export.file_path,
                "export_config": json.loads(export.export_config) if export.export_config else None,
                "status": export.status,
                "error_message": export.error_message,
                "created_at": export.created_at,
                "completed_at": export.completed_at
            })
        
        return results
