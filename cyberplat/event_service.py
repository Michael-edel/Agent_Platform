"""Сервис для работы с событиями."""

import uuid
import logging
import sqlite3
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class TenantValidationError(Exception):
    """Ошибка валидации tenant_id."""
    pass


class EventService:
    """Сервис для эмиссии и управления событиями."""
    
    def __init__(self, db_path: str = "platform.db", artifact_service=None):
        self.db_path = db_path
        self.artifact_service = artifact_service
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self) -> None:
        """Инициализировать таблицы для событий."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                tenant_id TEXT,
                artifact_id TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_tenant_id ON events(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_artifact_id ON events(artifact_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)")
        
        conn.commit()
        conn.close()
        logger.info("База событий инициализирована: %s", self.db_path)
    
    def _normalize_tenant_id(self, tenant_id: Optional[str]) -> str:
        """
        Нормализовать и валидировать tenant_id.
        
        Args:
            tenant_id: ID тенанта для нормализации
            
        Returns:
            Нормализованный tenant_id
            
        Raises:
            TenantValidationError: Если tenant_id некорректен
        """
        if tenant_id is None:
            raise TenantValidationError("tenant_id обязателен и не может быть None")
        
        # Trim
        tenant_id = tenant_id.strip()
        
        # Проверка на пустую строку
        if not tenant_id:
            raise TenantValidationError("tenant_id не может быть пустой строкой")
        
        # Запрещаем "string" как значение
        if tenant_id == "string":
            raise TenantValidationError("tenant_id не может быть строкой 'string' (возможный баг из OpenAPI)")
        
        return tenant_id
    
    def _resolve_tenant_id(
        self,
        tenant_id: Optional[str],
        artifact_id: Optional[str]
    ) -> str:
        """
        Разрешить tenant_id из переданного значения или артефакта.
        
        Args:
            tenant_id: Явно переданный tenant_id
            artifact_id: ID артефакта для получения tenant_id
            
        Returns:
            Разрешенный tenant_id
            
        Raises:
            TenantValidationError: Если tenant_id не может быть разрешен
        """
        # Если tenant_id передан явно - нормализуем и возвращаем
        if tenant_id is not None:
            normalized = self._normalize_tenant_id(tenant_id)
            
            # Если есть artifact_id - проверяем консистентность
            if artifact_id and self.artifact_service:
                artifact = self.artifact_service.get_artifact(artifact_id)
                if artifact:
                    artifact_tenant_id = artifact.get("tenant_id")
                    if artifact_tenant_id and normalized != artifact_tenant_id:
                        raise TenantValidationError(
                            f"tenant_id ({normalized}) не совпадает с tenant_id артефакта "
                            f"({artifact_tenant_id}). Cross-tenant write запрещен."
                        )
            
            return normalized
        
        # Если tenant_id не передан - пытаемся получить из артефакта
        if artifact_id and self.artifact_service:
            artifact = self.artifact_service.get_artifact(artifact_id)
            if artifact:
                artifact_tenant_id = artifact.get("tenant_id")
                if artifact_tenant_id:
                    return self._normalize_tenant_id(artifact_tenant_id)
                else:
                    raise TenantValidationError(
                        f"Артефакт {artifact_id} не имеет tenant_id, "
                        f"и tenant_id не передан явно для события"
                    )
            else:
                raise TenantValidationError(
                    f"Артефакт {artifact_id} не найден, "
                    f"и tenant_id не передан явно для события"
                )
        
        # Если нет ни tenant_id, ни artifact_id - ошибка
        raise TenantValidationError(
            "tenant_id обязателен. Либо передайте tenant_id явно, "
            "либо передайте artifact_id для получения tenant_id из артефакта"
        )
    
    def emit(
        self,
        event_type: str,
        tenant_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Эмитировать событие с строгой валидацией tenant_id.
        
        Args:
            event_type: Тип события (например, "document.extracted")
            tenant_id: ID тенанта (обязателен, если не передан artifact_id)
            artifact_id: ID артефакта, связанного с событием (обязателен, если не передан tenant_id)
            payload: Дополнительные данные события
            
        Returns:
            ID созданного события
            
        Raises:
            TenantValidationError: Если tenant_id некорректен или не может быть разрешен
        """
        import json
        
        # Разрешаем tenant_id с строгой валидацией
        resolved_tenant_id = self._resolve_tenant_id(tenant_id, artifact_id)
        
        event_id = str(uuid.uuid4())
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO events (id, event_type, tenant_id, artifact_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            event_type,
            resolved_tenant_id,
            artifact_id,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Эмитировано событие: {event_type} (tenant_id={resolved_tenant_id}, artifact_id={artifact_id})")
        return event_id
