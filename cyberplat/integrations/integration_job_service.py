"""Сервис для управления jobs интеграций."""

import uuid
import logging
import sqlite3
import os
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class IntegrationJobService:
    """Сервис для управления jobs интеграций."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:integration_jobs_{uuid.uuid4().hex}?mode=memory&cache=shared"
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
        """Инициализировать таблицу jobs интеграций."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS integration_jobs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_id TEXT,
                provider TEXT NOT NULL,
                job_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                last_error_ru TEXT,
                last_error_code TEXT,
                next_attempt_at TIMESTAMP,
                locked_at TIMESTAMP,
                locked_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_integration_jobs_tenant_id ON integration_jobs(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_integration_jobs_status ON integration_jobs(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_integration_jobs_provider ON integration_jobs(provider)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_integration_jobs_next_attempt ON integration_jobs(next_attempt_at)")
        
        conn.commit()
        conn.close()
        logger.info("База jobs интеграций инициализирована: %s", self.db_path)
    
    def enqueue(
        self,
        tenant_id: str,
        provider: str,
        job_type: str,
        payload: Dict[str, Any],
        case_id: Optional[str] = None,
        max_attempts: int = 5
    ) -> str:
        """
        Добавить job в очередь.
        
        Args:
            tenant_id: ID тенанта
            provider: Провайдер интеграции ('onec')
            job_type: Тип job ('upsert_counterparty', 'upsert_contract', 'upsert_invoice')
            payload: Данные для обработки
            case_id: ID кейса (опционально)
            max_attempts: Максимальное количество попыток
            
        Returns:
            ID созданного job
        """
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO integration_jobs
            (id, tenant_id, case_id, provider, job_type, payload_json, status, attempt, max_attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (job_id, tenant_id, case_id, provider, job_type, json.dumps(payload, ensure_ascii=False), max_attempts, now, now)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Integration job enqueued: {job_id} (tenant={tenant_id}, provider={provider}, type={job_type})")
        return job_id
    
    def claim_for_processing(self, limit: int = 50, worker_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Забрать jobs для обработки (best-effort locking для SQLite).
        
        Args:
            limit: Максимальное количество jobs
            worker_id: ID воркера
            
        Returns:
            Список jobs
        """
        worker_id = worker_id or "worker-1"
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Best-effort locking для SQLite: обновляем locked_at/locked_by атомарно
        cur.execute(
            """
            UPDATE integration_jobs
            SET status = 'processing', locked_at = ?, locked_by = ?, updated_at = ?
            WHERE id IN (
                SELECT id FROM integration_jobs
                WHERE status IN ('pending', 'pending_retry')
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                AND attempt < max_attempts
                AND (locked_at IS NULL OR locked_at < datetime('now', '-5 minutes'))
                ORDER BY created_at
                LIMIT ?
            )
            """,
            (now, worker_id, now, now, limit)
        )
        
        # Получаем забранные jobs
        cur.execute(
            """
            SELECT * FROM integration_jobs
            WHERE status = 'processing' AND locked_by = ? AND locked_at = ?
            """,
            (worker_id, now)
        )
        
        rows = cur.fetchall()
        conn.commit()
        conn.close()
        
        jobs = []
        for row in rows:
            jobs.append({
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "case_id": row["case_id"],
                "provider": row["provider"],
                "job_type": row["job_type"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
                "attempt": row["attempt"],
                "max_attempts": row["max_attempts"],
                "last_error_ru": row["last_error_ru"],
                "last_error_code": row["last_error_code"],
                "next_attempt_at": row["next_attempt_at"]
            })
        
        return jobs
    
    def mark_succeeded(self, job_id: str) -> None:
        """Отметить job как успешный."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            UPDATE integration_jobs
            SET status = 'succeeded', locked_at = NULL, locked_by = NULL, updated_at = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), job_id)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Integration job succeeded: {job_id}")
    
    def mark_failed(
        self,
        job_id: str,
        error_ru: str,
        error_code: str,
        schedule_retry: bool = True
    ) -> None:
        """
        Отметить job как неудачный.
        
        Args:
            job_id: ID job
            error_ru: Сообщение об ошибке на русском
            error_code: Код ошибки (EN)
            schedule_retry: Запланировать повторную попытку
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем текущий attempt
        cur.execute("SELECT attempt, max_attempts FROM integration_jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        
        if not row:
            conn.close()
            return
        
        current_attempt = row["attempt"]
        max_attempts = row["max_attempts"]
        new_attempt = current_attempt + 1
        
        now = datetime.now().isoformat()
        
        if schedule_retry and new_attempt < max_attempts:
            # Планируем retry (используем now для немедленного retry в тестах)
            cur.execute(
                """
                UPDATE integration_jobs
                SET status = 'pending_retry', attempt = ?, last_error_ru = ?, last_error_code = ?,
                    next_attempt_at = ?, locked_at = NULL, locked_by = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_attempt, error_ru, error_code, now, now, job_id)
            )
        else:
            # Финальная ошибка (new_attempt >= max_attempts или schedule_retry=False)
            cur.execute(
                """
                UPDATE integration_jobs
                SET status = 'failed', attempt = ?, last_error_ru = ?, last_error_code = ?,
                    next_attempt_at = NULL, locked_at = NULL, locked_by = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_attempt, error_ru, error_code, now, job_id)
            )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Integration job failed: {job_id} (attempt={new_attempt}/{max_attempts}, error_code={error_code})")
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Получить job по ID."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "case_id": row["case_id"],
            "provider": row["provider"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "attempt": row["attempt"],
            "max_attempts": row["max_attempts"],
            "last_error_ru": row["last_error_ru"],
            "last_error_code": row["last_error_code"],
            "next_attempt_at": row["next_attempt_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
    
    def list_jobs(
        self,
        tenant_id: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Получить список jobs с фильтрацией."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM integration_jobs WHERE 1=1"
        params = []
        
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "case_id": row["case_id"],
                "provider": row["provider"],
                "job_type": row["job_type"],
                "status": row["status"],
                "attempt": row["attempt"],
                "max_attempts": row["max_attempts"],
                "last_error_ru": row["last_error_ru"],
                "last_error_code": row["last_error_code"],
                "next_attempt_at": row["next_attempt_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ]
