"""
LEGACY REPOSITORY - НЕ ИСПОЛЬЗОВАТЬ!

Этот файл содержит legacy реализацию репозиториев на sqlite3 с self-healing schema.
Он был заменён на SQLAlchemy-based реализацию в repositories_sqlalchemy.py.

ВНИМАНИЕ:
- ❌ НЕ ИМПОРТИРОВАТЬ этот файл в новом коде
- ❌ Содержит CREATE TABLE IF NOT EXISTS (недопустимо в проде)
- ✅ Используйте repositories_sqlalchemy.py вместо этого файла
- ✅ Все таблицы создаются через Alembic миграции

Этот файл сохранён только для справки и может быть удалён после миграции всех данных.
"""

import uuid
import logging
import sqlite3
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

# Защита от случайного импорта
import warnings
warnings.warn(
    "repositories_sqlite_legacy.py is deprecated. Use repositories_sqlalchemy.py instead.",
    DeprecationWarning,
    stacklevel=2
)

from cyberplat.product.domain.interfaces import (
    ArtifactStateRepository,
    ExportRepository
)

logger = logging.getLogger(__name__)


class ArtifactStateRepositoryImpl(ArtifactStateRepository):
    """Repository implementation для artifact states (использует sqlite3)."""
    
    def __init__(self, db_path: str = "platform.db"):
        self.db_path = db_path
        self._ensure_schema()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_schema(self) -> None:
        """Инициализировать таблицу artifact_states (если не существует)."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifact_states (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL UNIQUE,
                ui_status TEXT NOT NULL DEFAULT 'pending',
                source_artifact_id TEXT,
                error_code TEXT,
                error_message TEXT,
                confirmed_at TEXT,
                exported_at TEXT,
                export_target TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifact_states_tenant_id ON artifact_states(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifact_states_artifact_id ON artifact_states(artifact_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifact_states_ui_status ON artifact_states(ui_status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifact_states_source_artifact_id ON artifact_states(source_artifact_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifact_states_created_at ON artifact_states(created_at)")
        
        conn.commit()
        conn.close()
    
    def get_state(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить состояние артефакта."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT * FROM artifact_states
            WHERE tenant_id = ? AND artifact_id = ?
        """, (tenant_id, artifact_id))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "artifact_id": row["artifact_id"],
            "ui_status": row["ui_status"],
            "source_artifact_id": row["source_artifact_id"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "confirmed_at": row["confirmed_at"],
            "exported_at": row["exported_at"],
            "export_target": row["export_target"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
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
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, существует ли состояние
        cur.execute("""
            SELECT id FROM artifact_states
            WHERE artifact_id = ?
        """, (artifact_id,))
        
        existing = cur.fetchone()
        now = datetime.now().isoformat()
        
        if existing:
            # Обновляем существующее
            state_id = existing["id"]
            cur.execute("""
                UPDATE artifact_states
                SET ui_status = ?,
                    source_artifact_id = ?,
                    error_code = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ? AND tenant_id = ?
            """, (ui_status, source_artifact_id, error_code, error_message, now, state_id, tenant_id))
        else:
            # Создаём новое
            state_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO artifact_states (
                    id, tenant_id, artifact_id, ui_status,
                    source_artifact_id, error_code, error_message,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state_id, tenant_id, artifact_id, ui_status,
                source_artifact_id, error_code, error_message,
                now, now
            ))
        
        conn.commit()
        conn.close()
        
        return state_id
    
    def update_status(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус артефакта."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE artifact_states
            SET ui_status = ?,
                error_code = ?,
                error_message = ?,
                updated_at = ?
            WHERE tenant_id = ? AND artifact_id = ?
        """, (ui_status, error_code, error_message, datetime.now().isoformat(), tenant_id, artifact_id))
        
        updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated
    
    def mark_confirmed(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> bool:
        """Отметить артефакт как подтверждённый."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        now = datetime.now().isoformat()
        cur.execute("""
            UPDATE artifact_states
            SET ui_status = 'confirmed',
                confirmed_at = ?,
                updated_at = ?
            WHERE tenant_id = ? AND artifact_id = ?
        """, (now, now, tenant_id, artifact_id))
        
        updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated
    
    def mark_exported(
        self,
        tenant_id: str,
        artifact_id: str,
        export_target: str
    ) -> bool:
        """Отметить артефакт как экспортированный."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        now = datetime.now().isoformat()
        cur.execute("""
            UPDATE artifact_states
            SET ui_status = 'exported',
                exported_at = ?,
                export_target = ?,
                updated_at = ?
            WHERE tenant_id = ? AND artifact_id = ?
        """, (now, export_target, now, tenant_id, artifact_id))
        
        updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated
    
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
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # JOIN с artifacts для получения базовых данных
        # Фильтруем по tenant_id и kind
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
            WHERE a.tenant_id = ? AND a.kind = ?
        """
        
        params = [tenant_id, kind]
        
        if ui_status:
            query += " AND (s.ui_status = ? OR (s.ui_status IS NULL AND ? = 'pending'))"
            params.append(ui_status)
            params.append(ui_status)
        
        query += " ORDER BY a.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cur.execute(query, params)
        
        results = []
        for row in cur.fetchall():
            # UI-проекция: не возвращаем raw OCR JSON
            result = {
                "id": row["artifact_id"],
                "kind": row["kind"],
                "source": row["source"],
                "tenant_id": row["tenant_id"],
                "created_at": row["artifact_created_at"],
                "state": {
                    "ui_status": row["ui_status"] or "pending",
                    "source_artifact_id": row["source_artifact_id"],
                    "error_code": row["error_code"],
                    "error_message": row["error_message"],
                    "confirmed_at": row["confirmed_at"],
                    "exported_at": row["exported_at"],
                    "export_target": row["export_target"],
                    "updated_at": row["state_updated_at"] or row["artifact_created_at"]
                } if row["state_id"] else {
                    "ui_status": "pending",
                    "source_artifact_id": None,
                    "error_code": None,
                    "error_message": None,
                    "confirmed_at": None,
                    "exported_at": None,
                    "export_target": None,
                    "updated_at": row["artifact_created_at"]
                }
            }
            results.append(result)
        
        conn.close()
        return results


class ExportRepositoryImpl(ExportRepository):
    """Repository implementation для exports (использует sqlite3)."""
    
    def __init__(self, db_path: str = "platform.db"):
        self.db_path = db_path
        self._ensure_schema()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_schema(self) -> None:
        """Инициализировать таблицу exports (если не существует)."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                export_type TEXT NOT NULL,
                file_id TEXT,
                file_path TEXT,
                export_config TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_tenant_id ON exports(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_artifact_id ON exports(artifact_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_export_type ON exports(export_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_status ON exports(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_created_at ON exports(created_at)")
        
        conn.commit()
        conn.close()
    
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
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO exports (
                id, tenant_id, artifact_id, export_type,
                export_config, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            export_id,
            tenant_id,
            artifact_id,
            export_type,
            json.dumps(export_config) if export_config else None,
            "pending",
            now
        ))
        
        conn.commit()
        conn.close()
        
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
        conn = self._get_connection()
        cur = conn.cursor()
        
        now = datetime.now().isoformat() if status == "completed" else None
        
        cur.execute("""
            UPDATE exports
            SET status = ?,
                file_id = ?,
                file_path = ?,
                error_message = ?,
                completed_at = ?
            WHERE id = ?
        """, (status, file_id, file_path, error_message, now, export_id))
        
        updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated
    
    def get_export(
        self,
        tenant_id: str,
        export_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить экспорт по ID."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT * FROM exports
            WHERE id = ? AND tenant_id = ?
        """, (export_id, tenant_id))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "artifact_id": row["artifact_id"],
            "export_type": row["export_type"],
            "file_id": row["file_id"],
            "file_path": row["file_path"],
            "export_config": json.loads(row["export_config"]) if row["export_config"] else None,
            "status": row["status"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"]
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
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM exports WHERE tenant_id = ?"
        params = [tenant_id]
        
        if artifact_id:
            query += " AND artifact_id = ?"
            params.append(artifact_id)
        
        if export_type:
            query += " AND export_type = ?"
            params.append(export_type)
        
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cur.execute(query, params)
        
        results = []
        for row in cur.fetchall():
            results.append({
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "artifact_id": row["artifact_id"],
                "export_type": row["export_type"],
                "file_id": row["file_id"],
                "file_path": row["file_path"],
                "export_config": json.loads(row["export_config"]) if row["export_config"] else None,
                "status": row["status"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"]
            })
        
        conn.close()
        return results
