"""Сервис для работы с артефактами."""

import uuid
import logging
import sqlite3
import os
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ArtifactService:
    """Сервис для создания и управления артефактами."""
    
    def __init__(self, db_path: str = "platform.db", event_service=None):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Аналогично BillingService: для db_path=":memory:" используем shared in-memory URI,
        # иначе каждая операция видит пустую БД (no such table: artifacts).
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:artifacts_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._sqlite_connect_kwargs = {"uri": True}
            self._keeper_conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
            self._keeper_conn.row_factory = sqlite3.Row
        self.event_service = event_service
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
        conn.row_factory = sqlite3.Row
        return conn

    def close(self) -> None:
        """Закрыть keeper connection (для тестов/cleanup)."""
        try:
            if self._keeper_conn is not None:
                self._keeper_conn.close()
        except Exception:
            pass
    
    def _init_database(self) -> None:
        """Инициализировать таблицы для артефактов."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                tenant_id TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_source ON artifacts(source)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_tenant_id ON artifacts(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_created_at ON artifacts(created_at)")
        
        conn.commit()
        conn.close()
        logger.info("База артефактов инициализирована: %s", self.db_path)
    
    def create_artifact(
        self,
        kind: str,
        source: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        artifact_id: Optional[str] = None
    ) -> str:
        """
        Создать новый артефакт.
        
        Args:
            kind: Тип артефакта (например, "invoice")
            source: Источник артефакта (например, "doc_agent")
            data: Данные артефакта
            tenant_id: ID тенанта
            artifact_id: Явно заданный ID артефакта (нужно для совместимости legacy→product).
            
        Returns:
            ID созданного артефакта
        """
        import json
        
        # Важно: legacy endpoints могут генерировать artifact_id заранее и ожидать,
        # что он будет использован как primary key (нельзя плодить "другие id" для того же документа).
        artifact_id = artifact_id or str(uuid.uuid4())
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Идемпотентность по id: если запись уже существует, обновляем её (без смены id).
        # Это важно для ретраев webhook'ов и для bridge legacy↔product.
        cur.execute(
            """
            INSERT OR REPLACE INTO artifacts (id, kind, source, tenant_id, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                kind,
                source,
                tenant_id,
                json.dumps(data, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Создан артефакт: {artifact_id} (kind={kind}, source={source}, tenant_id={tenant_id})")
        
        # Эмитим событие создания артефакта
        # Передаем и tenant_id, и artifact_id для проверки консистентности
        if self.event_service:
            try:
                self.event_service.emit(
                    event_type="artifact.created",
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    payload={"kind": kind, "source": source}
                )
            except Exception as e:
                # Логируем ошибку, но не падаем - артефакт уже создан
                logger.error(f"Ошибка при эмиссии события artifact.created: {e}", exc_info=True)
                # В production можно решить, нужно ли падать здесь или просто логировать
        
        return artifact_id
    
    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Получить артефакт по ID."""
        import json
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "kind": row["kind"],
            "source": row["source"],
            "tenant_id": row["tenant_id"],
            "data": json.loads(row["data"]),
            "created_at": row["created_at"]
        }

    def list_artifacts(
        self,
        *,
        kind: str,
        tenant_id: str,
        limit: int = 5000,
    ) -> list[Dict[str, Any]]:
        """List artifacts by kind + tenant_id (read-only helper for Phase 2)."""
        import json

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, kind, source, tenant_id, data, created_at
            FROM artifacts
            WHERE kind = ? AND tenant_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (kind, tenant_id, int(limit)),
        )
        rows = cur.fetchall()
        conn.close()

        out: list[Dict[str, Any]] = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except Exception:
                data = {}
            out.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "source": row["source"],
                    "tenant_id": row["tenant_id"],
                    "data": data,
                    "created_at": row["created_at"],
                }
            )
        return out
