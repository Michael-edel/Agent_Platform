"""Сервис для работы с кейсами (cases) и workflow."""

import uuid
import logging
import sqlite3
import os
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class CaseNotFoundError(Exception):
    """Кейс не найден."""
    pass


class InvalidTransitionError(Exception):
    """Недопустимый переход статуса."""
    pass


class CaseService:
    """Сервис для создания и управления кейсами."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:cases_{uuid.uuid4().hex}?mode=memory&cache=shared"
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
        """Инициализировать таблицы для кейсов."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Таблица кейсов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_type TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step TEXT,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица шагов кейса
        cur.execute("""
            CREATE TABLE IF NOT EXISTS case_steps (
                case_id TEXT NOT NULL,
                step_key TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                PRIMARY KEY (case_id, step_key),
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица задач кейса
        cur.execute("""
            CREATE TABLE IF NOT EXISTS case_tasks (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                step_key TEXT NOT NULL,
                title TEXT NOT NULL,
                assignee_role TEXT,
                status TEXT NOT NULL,
                due_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица событий кейса (audit trail)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS case_events (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            )
        """)
        
        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_tenant_id ON cases(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_case_type ON cases(case_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_case_steps_case_id ON case_steps(case_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_case_tasks_case_id ON case_tasks(case_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_case_events_case_id ON case_events(case_id)")
        
        conn.commit()
        conn.close()
        logger.info("База кейсов инициализирована: %s", self.db_path)
    
    def _log_event(self, conn: sqlite3.Connection, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Записать событие в audit trail."""
        event_id = str(uuid.uuid4())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO case_events (id, case_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, case_id, event_type, payload_json, datetime.now().isoformat())
        )
    
    def create_case(
        self,
        tenant_id: str,
        case_type: str,
        title: str,
        initial_step: Optional[str] = None
    ) -> str:
        """
        Создать новый кейс.
        
        Args:
            tenant_id: ID тенанта
            case_type: Тип кейса
            title: Заголовок кейса
            initial_step: Начальный шаг (опционально)
            
        Returns:
            ID созданного кейса
        """
        case_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO cases (id, tenant_id, case_type, status, current_step, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, tenant_id, case_type, "open", initial_step, title, now, now)
        )
        
        # Логируем событие создания
        self._log_event(conn, case_id, "case.created", {
            "case_type": case_type,
            "title": title,
            "initial_step": initial_step
        })
        
        # Если указан начальный шаг, создаём запись в case_steps
        if initial_step:
            cur.execute(
                """
                INSERT INTO case_steps (case_id, step_key, status, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (case_id, initial_step, "in_progress", now)
            )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Создан кейс: {case_id} (tenant_id={tenant_id}, case_type={case_type}, title={title})")
        return case_id
    
    def get_case(self, case_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Получить кейс по ID.
        
        Args:
            case_id: ID кейса
            tenant_id: ID тенанта (для проверки доступа, опционально)
            
        Returns:
            Данные кейса или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        if tenant_id:
            cur.execute("SELECT * FROM cases WHERE id = ? AND tenant_id = ?", (case_id, tenant_id))
        else:
            cur.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "case_type": row["case_type"],
            "status": row["status"],
            "current_step": row["current_step"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
    
    def list_cases(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        case_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Получить список кейсов.
        
        Args:
            tenant_id: ID тенанта
            status: Фильтр по статусу (опционально)
            case_type: Фильтр по типу (опционально)
            limit: Максимальное количество записей
            
        Returns:
            Список кейсов
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM cases WHERE tenant_id = ?"
        params = [tenant_id]
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if case_type:
            query += " AND case_type = ?"
            params.append(case_type)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "case_type": row["case_type"],
                "status": row["status"],
                "current_step": row["current_step"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ]
    
    def add_task(
        self,
        case_id: str,
        step_key: str,
        title: str,
        assignee_role: Optional[str] = None,
        due_at: Optional[str] = None
    ) -> str:
        """
        Добавить задачу к кейсу.
        
        Args:
            case_id: ID кейса
            step_key: Ключ шага
            title: Заголовок задачи
            assignee_role: Роль исполнителя (опционально)
            due_at: Срок выполнения в формате ISO (опционально)
            
        Returns:
            ID созданной задачи
        """
        # Проверяем, что кейс существует
        case = self.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Кейс {case_id} не найден")
        
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO case_tasks (id, case_id, step_key, title, assignee_role, status, due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, case_id, step_key, title, assignee_role, "pending", due_at, now)
        )
        
        # Логируем событие
        self._log_event(conn, case_id, "task.created", {
            "task_id": task_id,
            "step_key": step_key,
            "title": title,
            "assignee_role": assignee_role
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Добавлена задача: {task_id} к кейсу {case_id}")
        return task_id
    
    def complete_task(self, case_id: str, task_id: str) -> None:
        """
        Завершить задачу.
        
        Args:
            case_id: ID кейса
            task_id: ID задачи
        """
        # Проверяем, что кейс существует
        case = self.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Кейс {case_id} не найден")
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, что задача существует и принадлежит кейсу
        cur.execute("SELECT * FROM case_tasks WHERE id = ? AND case_id = ?", (task_id, case_id))
        task = cur.fetchone()
        if not task:
            conn.close()
            raise CaseNotFoundError(f"Задача {task_id} не найдена в кейсе {case_id}")
        
        # Обновляем статус задачи
        now = datetime.now().isoformat()
        cur.execute(
            "UPDATE case_tasks SET status = ?, completed_at = ? WHERE id = ?",
            ("completed", now, task_id)
        )
        
        # Логируем событие
        self._log_event(conn, case_id, "task.completed", {
            "task_id": task_id,
            "step_key": task["step_key"]
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Завершена задача: {task_id} в кейсе {case_id}")
    
    def transition_step(
        self,
        case_id: str,
        new_step: str,
        from_step: Optional[str] = None
    ) -> None:
        """
        Перевести кейс на новый шаг.
        
        Args:
            case_id: ID кейса
            new_step: Новый шаг
            from_step: Предыдущий шаг (для проверки допустимости перехода, опционально)
        """
        # Проверяем, что кейс существует
        case = self.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Кейс {case_id} не найден")
        
        # Проверяем, что кейс не закрыт
        if case["status"] == "closed":
            raise InvalidTransitionError("Нельзя переводить закрытый кейс на новый шаг")
        
        # Если указан from_step, проверяем, что текущий шаг совпадает
        if from_step and case["current_step"] != from_step:
            raise InvalidTransitionError(
                f"Текущий шаг '{case['current_step']}' не совпадает с ожидаемым '{from_step}'"
            )
        
        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        
        # Завершаем предыдущий шаг, если он был
        if case["current_step"]:
            cur.execute(
                """
                UPDATE case_steps
                SET status = ?, completed_at = ?
                WHERE case_id = ? AND step_key = ?
                """,
                ("completed", now, case_id, case["current_step"])
            )
        
        # Создаём или обновляем новый шаг
        cur.execute(
            """
            INSERT OR REPLACE INTO case_steps (case_id, step_key, status, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (case_id, new_step, "in_progress", now)
        )
        
        # Обновляем current_step в кейсе
        cur.execute(
            "UPDATE cases SET current_step = ?, updated_at = ? WHERE id = ?",
            (new_step, now, case_id)
        )
        
        # Логируем событие
        self._log_event(conn, case_id, "step.transitioned", {
            "from_step": case["current_step"],
            "to_step": new_step
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Кейс {case_id} переведён на шаг: {new_step}")
    
    def get_task(self, task_id: str, case_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Получить задачу по ID.
        
        Args:
            task_id: ID задачи
            case_id: ID кейса (для проверки принадлежности, опционально)
            
        Returns:
            Данные задачи или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        if case_id:
            cur.execute("SELECT * FROM case_tasks WHERE id = ? AND case_id = ?", (task_id, case_id))
        else:
            cur.execute("SELECT * FROM case_tasks WHERE id = ?", (task_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "step_key": row["step_key"],
            "title": row["title"],
            "assignee_role": row["assignee_role"],
            "status": row["status"],
            "due_at": row["due_at"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"]
        }
    
    def close_case(self, case_id: str) -> None:
        """
        Закрыть кейс.
        
        Args:
            case_id: ID кейса
        """
        # Проверяем, что кейс существует
        case = self.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Кейс {case_id} не найден")
        
        # Проверяем, что кейс ещё не закрыт
        if case["status"] == "closed":
            logger.warning(f"Кейс {case_id} уже закрыт")
            return
        
        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        
        # Обновляем статус кейса и сбрасываем current_step
        cur.execute(
            "UPDATE cases SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
            ("closed", None, now, case_id)
        )
        
        # Завершаем текущий шаг, если он был
        if case["current_step"]:
            cur.execute(
                """
                UPDATE case_steps
                SET status = ?, completed_at = ?
                WHERE case_id = ? AND step_key = ? AND status = 'in_progress'
                """,
                ("completed", now, case_id, case["current_step"])
            )
        
        # Логируем событие
        self._log_event(conn, case_id, "case.closed", {})
        
        conn.commit()
        conn.close()
        
        logger.info(f"Кейс {case_id} закрыт")
