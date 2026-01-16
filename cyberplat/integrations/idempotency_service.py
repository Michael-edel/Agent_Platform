"""Сервис для управления идемпотентностью интеграций (предотвращение дубликатов)."""

import uuid
import logging
import sqlite3
import os
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class IdempotencyService:
    """Сервис для управления идемпотентностью интеграций."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:idempotency_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._sqlite_connect_kwargs = {"uri": True}
            self._keeper_conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
            self._keeper_conn.row_factory = sqlite3.Row
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Получить соединение с БД."""
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
        """Инициализировать таблицу идемпотентности."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS integration_idempotency (
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                object_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                remote_id TEXT,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, provider, object_type, idempotency_key)
            )
        """)
        
        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_remote_id ON integration_idempotency(remote_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_status ON integration_idempotency(status)")
        
        conn.commit()
        conn.close()
        logger.info("База идемпотентности интеграций инициализирована: %s", self.db_path)
    
    def get_remote_id(
        self,
        tenant_id: str,
        provider: str,
        object_type: str,
        idempotency_key: str
    ) -> Optional[str]:
        """
        Получить remote_id для существующей записи (если уже была создана).
        
        Args:
            tenant_id: ID тенанта
            provider: Провайдер интеграции ('onec')
            object_type: Тип объекта ('counterparty', 'contract', 'invoice')
            idempotency_key: Ключ идемпотентности
            
        Returns:
            remote_id если запись существует и успешна, иначе None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT remote_id FROM integration_idempotency
            WHERE tenant_id = ? AND provider = ? AND object_type = ? AND idempotency_key = ? AND status = 'succeeded'
            """,
            (tenant_id, provider, object_type, idempotency_key)
        )
        
        row = cur.fetchone()
        conn.close()
        
        return row["remote_id"] if row and row["remote_id"] else None
    
    def mark_succeeded(
        self,
        tenant_id: str,
        provider: str,
        object_type: str,
        idempotency_key: str,
        remote_id: str
    ) -> None:
        """
        Отметить операцию как успешную.
        
        Args:
            tenant_id: ID тенанта
            provider: Провайдер интеграции
            object_type: Тип объекта
            idempotency_key: Ключ идемпотентности
            remote_id: ID объекта в удалённой системе
        """
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # INSERT OR REPLACE для идемпотентности
        cur.execute(
            """
            INSERT OR REPLACE INTO integration_idempotency
            (tenant_id, provider, object_type, idempotency_key, remote_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'succeeded', COALESCE((SELECT created_at FROM integration_idempotency WHERE tenant_id = ? AND provider = ? AND object_type = ? AND idempotency_key = ?), ?), ?)
            """,
            (tenant_id, provider, object_type, idempotency_key, remote_id, tenant_id, provider, object_type, idempotency_key, now, now)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Idempotency marked succeeded: {provider}/{object_type}/{idempotency_key} -> {remote_id}")
    
    def mark_failed(
        self,
        tenant_id: str,
        provider: str,
        object_type: str,
        idempotency_key: str
    ) -> None:
        """
        Отметить операцию как неудачную.
        
        Args:
            tenant_id: ID тенанта
            provider: Провайдер интеграции
            object_type: Тип объекта
            idempotency_key: Ключ идемпотентности
        """
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # INSERT OR REPLACE
        cur.execute(
            """
            INSERT OR REPLACE INTO integration_idempotency
            (tenant_id, provider, object_type, idempotency_key, remote_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, 'failed', COALESCE((SELECT created_at FROM integration_idempotency WHERE tenant_id = ? AND provider = ? AND object_type = ? AND idempotency_key = ?), ?), ?)
            """,
            (tenant_id, provider, object_type, idempotency_key, tenant_id, provider, object_type, idempotency_key, now, now)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Idempotency marked failed: {provider}/{object_type}/{idempotency_key}")
