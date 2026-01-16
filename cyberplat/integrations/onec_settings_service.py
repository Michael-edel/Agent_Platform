"""Сервис для хранения настроек интеграции 1С (per-tenant)."""

import uuid
import logging
import sqlite3
import os
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class OneCSettingsService:
    """Сервис для управления настройками интеграции 1С."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:onec_settings_{uuid.uuid4().hex}?mode=memory&cache=shared"
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
        """Инициализировать таблицу настроек 1С."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_integrations_1c (
                tenant_id TEXT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                base_url TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'token',
                token TEXT,
                username TEXT,
                password TEXT,
                timeout_seconds INTEGER NOT NULL DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("База настроек 1С инициализирована: %s", self.db_path)
    
    def get_settings(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить настройки интеграции 1С для tenant.
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Настройки или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM tenant_integrations_1c WHERE tenant_id = ?", (tenant_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "tenant_id": row["tenant_id"],
            "enabled": bool(row["enabled"]),
            "base_url": row["base_url"],
            "auth_type": row["auth_type"],
            "token": row["token"],
            "username": row["username"],
            "password": row["password"],
            "timeout_seconds": row["timeout_seconds"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
    
    def upsert_settings(
        self,
        tenant_id: str,
        enabled: bool,
        base_url: str,
        auth_type: str = "token",
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout_seconds: int = 10
    ) -> None:
        """
        Создать или обновить настройки интеграции 1С.
        
        Args:
            tenant_id: ID тенанта
            enabled: Включена ли интеграция
            base_url: Базовый URL API 1С
            auth_type: Тип аутентификации ('token' или 'basic')
            token: Токен для аутентификации (если auth_type='token')
            username: Имя пользователя (если auth_type='basic')
            password: Пароль (если auth_type='basic')
            timeout_seconds: Таймаут запросов в секундах
        """
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, существует ли запись
        cur.execute("SELECT tenant_id FROM tenant_integrations_1c WHERE tenant_id = ?", (tenant_id,))
        exists = cur.fetchone() is not None
        
        if exists:
            # Обновляем
            cur.execute(
                """
                UPDATE tenant_integrations_1c
                SET enabled = ?, base_url = ?, auth_type = ?, token = ?, username = ?, password = ?,
                    timeout_seconds = ?, updated_at = ?
                WHERE tenant_id = ?
                """,
                (enabled, base_url, auth_type, token, username, password, timeout_seconds, now, tenant_id)
            )
        else:
            # Создаём
            cur.execute(
                """
                INSERT INTO tenant_integrations_1c
                (tenant_id, enabled, base_url, auth_type, token, username, password, timeout_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, enabled, base_url, auth_type, token, username, password, timeout_seconds, now, now)
            )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Настройки 1С обновлены для tenant: {tenant_id} (enabled={enabled})")
    
    def disable(self, tenant_id: str) -> None:
        """
        Отключить интеграцию 1С для tenant.
        
        Args:
            tenant_id: ID тенанта
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            "UPDATE tenant_integrations_1c SET enabled = 0, updated_at = ? WHERE tenant_id = ?",
            (datetime.now().isoformat(), tenant_id)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Интеграция 1С отключена для tenant: {tenant_id}")
