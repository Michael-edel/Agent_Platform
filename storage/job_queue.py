"""Очередь задач для обработки документов (SQLite)."""

import sqlite3
import logging
import json
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Статусы задачи."""
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class JobQueue:
    """Очередь задач на SQLite."""
    
    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self) -> None:
        """Инициализация таблицы задач."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                client_id TEXT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                result_json TEXT,
                error_message TEXT,
                stats_json TEXT,
                billing_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_client_id ON jobs(client_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")
        
        conn.commit()
        conn.close()
        logger.info("Job queue инициализирована: %s", self.db_path)
    
    def add_job(
        self,
        job_id: str,
        filename: str,
        file_path: str,
        client_id: Optional[str] = None
    ) -> None:
        """Добавить задачу в очередь."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO jobs (job_id, client_id, filename, file_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            client_id,
            filename,
            file_path,
            JobStatus.QUEUED.value,
            datetime.now().isoformat(),
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"Задача добавлена в очередь: {job_id} ({filename})")
    
    def get_next_job(self) -> Optional[Dict[str, Any]]:
        """Получить следующую задачу из очереди (статус queued)."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Берем первую задачу со статусом queued
        cur.execute("""
            SELECT * FROM jobs
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (JobStatus.QUEUED.value,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "job_id": row["job_id"],
            "client_id": row["client_id"],
            "filename": row["filename"],
            "file_path": row["file_path"],
            "file_hash": row["file_hash"],
            "status": row["status"],
        }
    
    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: bool = False
    ) -> None:
        """Обновить статус задачи."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        if started_at:
            cur.execute("""
                UPDATE jobs
                SET status = ?, started_at = ?
                WHERE job_id = ?
            """, (status.value, datetime.now().isoformat(), job_id))
        else:
            cur.execute("""
                UPDATE jobs
                SET status = ?
                WHERE job_id = ?
            """, (status.value, job_id))
        
        conn.commit()
        conn.close()
    
    def complete_job(
        self,
        job_id: str,
        result: Dict[str, Any],
        stats: Dict[str, Any],
        billing: Dict[str, Any],
        file_hash: Optional[str] = None
    ) -> None:
        """Завершить задачу успешно."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE jobs
            SET status = ?,
                result_json = ?,
                stats_json = ?,
                billing_json = ?,
                file_hash = ?,
                completed_at = ?
            WHERE job_id = ?
        """, (
            JobStatus.DONE.value,
            json.dumps(result, ensure_ascii=False),
            json.dumps(stats, ensure_ascii=False),
            json.dumps(billing, ensure_ascii=False),
            file_hash,
            datetime.now().isoformat(),
            job_id,
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"Задача завершена успешно: {job_id}")
    
    def fail_job(
        self,
        job_id: str,
        error_message: str
    ) -> None:
        """Завершить задачу с ошибкой."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE jobs
            SET status = ?,
                error_message = ?,
                completed_at = ?
            WHERE job_id = ?
        """, (
            JobStatus.ERROR.value,
            error_message,
            datetime.now().isoformat(),
            job_id,
        ))
        
        conn.commit()
        conn.close()
        logger.error(f"Задача завершена с ошибкой: {job_id} - {error_message}")
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Получить задачу по ID."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        result = {
            "job_id": row["job_id"],
            "client_id": row["client_id"],
            "filename": row["filename"],
            "file_path": row["file_path"],
            "file_hash": row["file_hash"],
            "status": row["status"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }
        
        # Парсим JSON поля если они есть
        if row["result_json"]:
            try:
                result["result"] = json.loads(row["result_json"])
            except Exception:
                result["result"] = None
        
        if row["stats_json"]:
            try:
                result["stats"] = json.loads(row["stats_json"])
            except Exception:
                result["stats"] = {}
        
        if row["billing_json"]:
            try:
                result["billing"] = json.loads(row["billing_json"])
            except Exception:
                result["billing"] = {}
        
        return result

