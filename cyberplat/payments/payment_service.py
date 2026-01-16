"""Сервис для работы с платёжными поручениями и согласованием."""

import uuid
import logging
import sqlite3
import os
import json
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pathlib import Path

from cyberplat.event_service import EventService

logger = logging.getLogger(__name__)


class PaymentState(str, Enum):
    """Payment lifecycle states (stable API)."""

    # Keep DB values backward-compatible (lowercase), expose uppercase names in API/timeline.
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    FAILED = "failed"
    RECONCILED = "reconciled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_name_from_value(value: Optional[str]) -> str:
    try:
        return PaymentState(value or "").name
    except Exception:
        return (value or "").upper() or "UNKNOWN"


class PaymentNotFoundError(Exception):
    """Платёжное поручение не найдено."""
    pass


class InvalidApprovalError(Exception):
    """Ошибка согласования."""
    pass


class PaymentService:
    """Сервис для управления платёжными поручениями."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:payments_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._sqlite_connect_kwargs = {"uri": True}
            self._keeper_conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
            self._keeper_conn.row_factory = sqlite3.Row
        # EventService is the source of truth for Timeline API (events table).
        self._event_service = EventService(db_path=db_path)
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
        """Инициализировать таблицы для платёжных поручений."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Таблица платёжных поручений
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_id TEXT,
                source_invoice_id TEXT,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'KZT',
                beneficiary_name TEXT NOT NULL,
                beneficiary_iin_bin TEXT,
                beneficiary_bank_bic TEXT,
                beneficiary_account_iban TEXT NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by_role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                rejected_at TIMESTAMP,
                exported_at TIMESTAMP
            )
        """)
        
        # Таблица согласований
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_approvals (
                id TEXT PRIMARY KEY,
                payment_order_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                required_role TEXT NOT NULL,
                status TEXT NOT NULL,
                decided_at TIMESTAMP,
                decided_by TEXT,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (payment_order_id) REFERENCES payment_orders(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица политик согласования
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_payment_policies (
                tenant_id TEXT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                thresholds_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица событий (audit)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                payment_order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (payment_order_id) REFERENCES payment_orders(id) ON DELETE CASCADE
            )
        """)
        
        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant_id ON payment_orders(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_case_id ON payment_orders(case_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_approvals_order_id ON payment_approvals(payment_order_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_order_id ON payment_events(payment_order_id)")
        
        conn.commit()
        conn.close()
        logger.info("База платёжных поручений инициализирована: %s", self.db_path)
    
    def _log_event(self, conn: sqlite3.Connection, tenant_id: str, payment_order_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Записать событие в audit trail."""
        event_id = str(uuid.uuid4())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO payment_events (id, tenant_id, payment_order_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, tenant_id, payment_order_id, event_type, payload_json, datetime.now().isoformat())
        )

    def _emit_lifecycle_event(
        self,
        *,
        event_type: str,
        tenant_id: str,
        payment_id: str,
        payload: Optional[Dict[str, Any]] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        # Timeline API reads from EventService.events table (tenant_id + artifact_id).
        base = {"payment_id": payment_id, "timestamp": _now_iso()}
        if payload:
            base.update(payload)
        try:
            if conn is not None:
                self._event_service.emit_to_connection(conn, event_type, tenant_id=tenant_id, artifact_id=payment_id, payload=base)
            else:
                self._event_service.emit(event_type, tenant_id=tenant_id, artifact_id=payment_id, payload=base)
        except Exception:
            logger.warning("Failed to emit lifecycle event %s for payment_id=%s", event_type, payment_id, exc_info=True)

    def _set_state(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        payment_id: str,
        new_state: PaymentState,
        actor: str,
        reason: Optional[str] = None,
        decided_by: Optional[str] = None,
    ) -> None:
        cur = conn.cursor()
        cur.execute("SELECT status FROM payment_orders WHERE id = ? AND tenant_id = ? LIMIT 1", (payment_id, tenant_id))
        row = cur.fetchone()
        if not row:
            raise PaymentNotFoundError(f"Платёжное поручение {payment_id} не найдено")

        old_value = row["status"]
        old_state = _state_name_from_value(old_value)
        new_state_name = new_state.name

        now = _now_iso()
        approved_at = now if new_state == PaymentState.APPROVED else None
        rejected_at = now if new_state == PaymentState.REJECTED else None

        if old_value != new_state.value:
            cur.execute(
                """
                UPDATE payment_orders
                SET status = ?, updated_at = ?,
                    approved_at = COALESCE(?, approved_at),
                    rejected_at = COALESCE(?, rejected_at)
                WHERE id = ? AND tenant_id = ?
                """,
                (new_state.value, now, approved_at, rejected_at, payment_id, tenant_id),
            )

        self._emit_lifecycle_event(
            event_type="payment.state_changed",
            tenant_id=tenant_id,
            payment_id=payment_id,
            payload={
                "old_state": old_state,
                "new_state": new_state_name,
                "actor": actor,
                "decided_by": decided_by,
                "reason": reason,
            },
            conn=conn,
        )

        try:
            from cyberplat.observability.metrics import payments_state_transition_total, METRICS_ENABLED
            if METRICS_ENABLED and payments_state_transition_total:
                payments_state_transition_total.labels(from_state=old_state, to_state=new_state_name).inc()
        except Exception:
            pass
    
    def create_payment_order(
        self,
        tenant_id: str,
        amount: float,
        beneficiary_name: str,
        beneficiary_account_iban: str,
        purpose: str,
        created_by_role: str,
        case_id: Optional[str] = None,
        source_invoice_id: Optional[str] = None,
        currency: str = "KZT",
        beneficiary_iin_bin: Optional[str] = None,
        beneficiary_bank_bic: Optional[str] = None
    ) -> str:
        """
        Создать платёжное поручение.
        
        Args:
            tenant_id: ID тенанта
            amount: Сумма платежа
            beneficiary_name: Наименование получателя
            beneficiary_account_iban: IBAN счёта получателя
            purpose: Назначение платежа
            created_by_role: Роль создателя
            case_id: ID кейса (опционально)
            source_invoice_id: ID исходного счёта/инвойса (опционально)
            currency: Валюта (по умолчанию KZT)
            beneficiary_iin_bin: ИИН/БИН получателя (опционально)
            beneficiary_bank_bic: БИК банка получателя (опционально)
            
        Returns:
            ID созданного платёжного поручения
        """
        order_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO payment_orders
            (id, tenant_id, case_id, source_invoice_id, amount, currency, beneficiary_name,
             beneficiary_iin_bin, beneficiary_bank_bic, beneficiary_account_iban, purpose,
             status, created_by_role, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (order_id, tenant_id, case_id, source_invoice_id, amount, currency, beneficiary_name,
             beneficiary_iin_bin, beneficiary_bank_bic, beneficiary_account_iban, purpose,
             created_by_role, now, now)
        )
        
        # Логируем событие
        self._log_event(conn, tenant_id, order_id, "payment_order.created", {
            "amount": amount,
            "currency": currency,
            "beneficiary_name": beneficiary_name
        })

        # Lifecycle events (Timeline API source of truth)
        self._emit_lifecycle_event(
            event_type="payment.created",
            tenant_id=tenant_id,
            payment_id=order_id,
            payload={"state": PaymentState.DRAFT.name, "actor": "system"},
            conn=conn,
        )
        # Immediately transition to PENDING_APPROVAL
        self._set_state(
            conn,
            tenant_id=tenant_id,
            payment_id=order_id,
            new_state=PaymentState.PENDING_APPROVAL,
            actor="system",
        )
        
        conn.commit()
        conn.close()
        
        # Метрики
        try:
            from cyberplat.observability.metrics import payment_orders_total, METRICS_ENABLED
            if METRICS_ENABLED and payment_orders_total:
                payment_orders_total.labels(status="draft").inc()
        except Exception:
            pass
        
        logger.info(f"Создано платёжное поручение: {order_id} (tenant={tenant_id}, amount={amount}, currency={currency})")
        return order_id

    def run_post_create_integrations(self, *, payment_id: str, tenant_id: str) -> None:
        """
        Выполнить дополнительные шаги после создания платежа (best-effort).

        Важно: этот метод не должен удалять/откатывать созданный payment_order.
        По умолчанию — no-op. Используется как "крючок" для пилотных интеграций.
        """
        return None

    def record_payment_export_failed(self, *, payment_id: str, tenant_id: str, reason: str) -> None:
        """Зафиксировать сбой интеграции/экспорта, не откатывая payment."""
        conn = self._get_connection()
        try:
            self._log_event(conn, tenant_id, payment_id, "payment.export_failed", {"reason": reason})
            self._emit_lifecycle_event(
                event_type="payment.export_failed",
                tenant_id=tenant_id,
                payment_id=payment_id,
                payload={"actor": "system", "reason": reason},
                conn=conn,
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Метрики
        try:
            from cyberplat.observability.metrics import payments_export_fail_total, METRICS_ENABLED
            if METRICS_ENABLED and payments_export_fail_total:
                payments_export_fail_total.labels(reason="post_create_integration_failed").inc()
        except Exception:
            pass

    def get_payment_timeline(self, *, tenant_id: str, payment_id: str) -> List[Dict[str, Any]]:
        """Build timeline from EventService.events table (read-only)."""
        # Note: for db_path=":memory:" timeline is best-effort (not used in prod flows).
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
        except Exception:
            conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT event_type, payload, created_at
                FROM events
                WHERE tenant_id = ? AND artifact_id = ? AND event_type LIKE 'payment.%'
                ORDER BY created_at ASC
                """,
                (tenant_id, payment_id),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        out: List[Dict[str, Any]] = []
        for r in rows:
            event_type = r["event_type"]
            at = r["created_at"]
            payload = {}
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:
                payload = {}
            actor = payload.get("actor")
            reason = payload.get("reason")

            if event_type == "payment.created":
                item = {"event": event_type, "state": payload.get("state"), "actor": actor, "at": at}
            elif event_type == "payment.state_changed":
                item = {
                    "event": event_type,
                    "from": payload.get("old_state"),
                    "to": payload.get("new_state"),
                    "actor": actor,
                    "at": at,
                }
            elif event_type == "payment.reconciled":
                item = {
                    "event": event_type,
                    "actor": actor,
                    "at": at,
                    "source": payload.get("source"),
                    "paid_at": payload.get("paid_at"),
                    "note": payload.get("note"),
                    "statement_line_id": payload.get("statement_line_id"),
                }
            else:
                item = {"event": event_type, "actor": actor, "at": at}
            if reason:
                item["reason"] = reason
            out.append(item)
        return out

    def manual_reconcile(
        self,
        *,
        payment_id: str,
        tenant_id: str,
        paid_at: str,
        source: str,
        note: Optional[str] = None,
        statement_line_id: Optional[str] = None,
    ) -> None:
        """
        Manual reconciliation (mark as paid).

        Rules:
        - allowed only when state = APPROVED
        - transitions payment to RECONCILED
        - emits payment.reconciled (no silent updates)
        """
        order = self.get_payment_order(payment_id, tenant_id=tenant_id)
        if not order:
            raise PaymentNotFoundError(f"Платёжное поручение {payment_id} не найдено")
        if order["status"] != PaymentState.APPROVED.value:
            raise InvalidApprovalError("Сверка разрешена только для платежей в статусе APPROVED")

        conn = self._get_connection()
        try:
            self._set_state(
                conn,
                tenant_id=tenant_id,
                payment_id=payment_id,
                new_state=PaymentState.RECONCILED,
                actor="accountant",
            )
            self._emit_lifecycle_event(
                event_type="payment.reconciled",
                tenant_id=tenant_id,
                payment_id=payment_id,
                payload={
                    "actor": "accountant",
                    "source": source,
                    "paid_at": paid_at,
                    "note": note,
                    "statement_line_id": statement_line_id,
                },
                conn=conn,
            )
            self._log_event(conn, tenant_id, payment_id, "payment_order.reconciled", {"source": source, "paid_at": paid_at})
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        try:
            from cyberplat.observability.metrics import payments_reconciled_total, METRICS_ENABLED
            if METRICS_ENABLED and payments_reconciled_total:
                payments_reconciled_total.labels(source=source).inc()
        except Exception:
            pass
    
    def get_payment_order(self, order_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Получить платёжное поручение по ID."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        if tenant_id:
            cur.execute("SELECT * FROM payment_orders WHERE id = ? AND tenant_id = ?", (order_id, tenant_id))
        else:
            cur.execute("SELECT * FROM payment_orders WHERE id = ?", (order_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "case_id": row["case_id"],
            "source_invoice_id": row["source_invoice_id"],
            "amount": row["amount"],
            "currency": row["currency"],
            "beneficiary_name": row["beneficiary_name"],
            "beneficiary_iin_bin": row["beneficiary_iin_bin"],
            "beneficiary_bank_bic": row["beneficiary_bank_bic"],
            "beneficiary_account_iban": row["beneficiary_account_iban"],
            "purpose": row["purpose"],
            "status": row["status"],
            "created_by_role": row["created_by_role"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "exported_at": row["exported_at"]
        }
    
    def list_payment_orders(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        case_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Получить список платёжных поручений."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM payment_orders WHERE tenant_id = ?"
        params = [tenant_id]
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        
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
                "source_invoice_id": row["source_invoice_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "beneficiary_name": row["beneficiary_name"],
                "beneficiary_iin_bin": row["beneficiary_iin_bin"],
                "beneficiary_bank_bic": row["beneficiary_bank_bic"],
                "beneficiary_account_iban": row["beneficiary_account_iban"],
                "purpose": row["purpose"],
                "status": row["status"],
                "created_by_role": row["created_by_role"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "approved_at": row["approved_at"],
                "rejected_at": row["rejected_at"],
                "exported_at": row["exported_at"]
            }
            for row in rows
        ]
    
    def _build_approval_steps(self, tenant_id: str, amount: float) -> List[Dict[str, Any]]:
        """
        Построить шаги согласования на основе политики tenant.
        
        Returns:
            Список шагов: [{"step": 1, "required_role": "accountant"}, ...]
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT enabled, thresholds_json FROM tenant_payment_policies WHERE tenant_id = ?", (tenant_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            # Нет политики -> auto-approve (но вернём пустой список шагов)
            return []
        
        if not row["enabled"]:
            # Политика отключена -> auto-approve
            return []
        
        # Парсим thresholds_json
        try:
            thresholds = json.loads(row["thresholds_json"])
        except Exception as e:
            logger.error(f"Ошибка при парсинге thresholds_json для tenant {tenant_id}: {e}")
            return []
        
        # Находим подходящий threshold
        for threshold in thresholds:
            max_amount = threshold.get("max", float("inf"))
            if amount <= max_amount:
                roles = threshold.get("roles", [])
                # Создаём шаги по порядку ролей
                steps = []
                for idx, role in enumerate(roles, start=1):
                    steps.append({"step": idx, "required_role": role})
                return steps
        
        # Если сумма превышает все thresholds -> последний threshold
        if thresholds:
            last_threshold = thresholds[-1]
            roles = last_threshold.get("roles", [])
            steps = []
            for idx, role in enumerate(roles, start=1):
                steps.append({"step": idx, "required_role": role})
            return steps
        
        return []
    
    def submit_for_approval(self, order_id: str, tenant_id: str) -> None:
        """
        Отправить платёжное поручение на согласование.
        
        Args:
            order_id: ID платёжного поручения
            tenant_id: ID тенанта
        """
        order = self.get_payment_order(order_id, tenant_id=tenant_id)
        if not order:
            raise PaymentNotFoundError(f"Платёжное поручение {order_id} не найдено")

        if order["status"] not in {"draft", "pending_approval"}:
            raise InvalidApprovalError("Платёжное поручение уже обработано и не может быть отправлено на согласование")

        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()

        # Всегда приводим к PENDING_APPROVAL (идемпотентно) и фиксируем lifecycle событие
        self._set_state(
            conn,
            tenant_id=tenant_id,
            payment_id=order_id,
            new_state=PaymentState.PENDING_APPROVAL,
            actor="accountant",
        )

        # Строим шаги согласования (если политика задана)
        steps = self._build_approval_steps(tenant_id, order["amount"])

        # Создаём шаги только один раз (idempotent best-effort)
        cur.execute("SELECT COUNT(*) as cnt FROM payment_approvals WHERE payment_order_id = ?", (order_id,))
        approvals_exist = int(cur.fetchone()["cnt"]) > 0

        if steps and not approvals_exist:
            for step_info in steps:
                step_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO payment_approvals
                    (id, payment_order_id, tenant_id, step, required_role, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (step_id, order_id, tenant_id, step_info["step"], step_info["required_role"], now),
                )

            self._log_event(conn, tenant_id, order_id, "payment_order.submitted", {"steps_count": len(steps)})

            # Интеграция с Case: создаём задачу и событие, если есть case_id
            if order.get("case_id"):
                case_id = order["case_id"]
                try:
                    cur.execute("SELECT * FROM cases WHERE id = ? AND tenant_id = ?", (case_id, tenant_id))
                    case = cur.fetchone()
                    if case:
                        required_role = steps[0]["required_role"]
                        task_id = str(uuid.uuid4())
                        step_key = case["current_step"] if case["current_step"] else "approval"
                        cur.execute(
                            """
                            INSERT INTO case_tasks
                            (id, case_id, step_key, title, assignee_role, status, created_at)
                            VALUES (?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (task_id, case_id, step_key, "Согласовать платеж", required_role, now),
                        )
                        event_id = str(uuid.uuid4())
                        event_payload = json.dumps(
                            {"payment_order_id": order_id, "amount": order["amount"], "currency": order["currency"]},
                            ensure_ascii=False,
                        )
                        cur.execute(
                            """
                            INSERT INTO case_events
                            (id, case_id, event_type, payload_json, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (event_id, case_id, "payment_submitted", event_payload, now),
                        )
                except Exception as e:
                    logger.warning(f"Ошибка при создании задачи в кейсе: {e}")

        # Метрики (queueing to approval)
        try:
            from cyberplat.observability.metrics import payment_orders_total, METRICS_ENABLED
            if METRICS_ENABLED and payment_orders_total:
                payment_orders_total.labels(status="pending_approval").inc()
        except Exception:
            pass

        conn.commit()
        conn.close()

        logger.info(f"Платёжное поручение {order_id} отправлено на согласование ({len(steps)} шагов)")
    
    def approve(
        self,
        order_id: str,
        tenant_id: str,
        role: str,
        comment: Optional[str] = None,
        decided_by: Optional[str] = None
    ) -> None:
        """
        Одобрить текущий шаг согласования.
        
        Args:
            order_id: ID платёжного поручения
            tenant_id: ID тенанта
            role: Роль одобряющего
            comment: Комментарий (опционально)
            decided_by: ID пользователя (опционально)
        """
        order = self.get_payment_order(order_id, tenant_id=tenant_id)
        if not order:
            raise PaymentNotFoundError(f"Платёжное поручение {order_id} не найдено")
        
        if order["status"] not in {"pending_approval", "approved"}:
            if order["status"] == "rejected":
                raise InvalidApprovalError("Платёжное поручение уже отклонено")
            raise InvalidApprovalError(f"Платёжное поручение в статусе {order['status']}, нельзя одобрить")

        # Идемпотентно: уже одобрено
        if order["status"] == "approved":
            return
        
        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        
        # Находим текущий pending шаг
        cur.execute(
            """
            SELECT * FROM payment_approvals
            WHERE payment_order_id = ? AND status = 'pending'
            ORDER BY step ASC
            LIMIT 1
            """,
            (order_id,)
        )
        approval = cur.fetchone()
        
        if not approval:
            # Нет шагов согласования -> разрешаем прямое одобрение (Phase 1).
            self._set_state(
                conn,
                tenant_id=tenant_id,
                payment_id=order_id,
                new_state=PaymentState.APPROVED,
                actor="approver",
                reason=comment,
                decided_by=decided_by or role,
            )
            self._emit_lifecycle_event(
                event_type="payment.approved",
                tenant_id=tenant_id,
                payment_id=order_id,
                payload={"actor": "approver", "reason": comment, "decided_by": decided_by or role},
                conn=conn,
            )
            self._log_event(conn, tenant_id, order_id, "payment_order.approved", {"role": role, "comment": comment})

            try:
                from cyberplat.observability.metrics import payments_approved_total, METRICS_ENABLED
                if METRICS_ENABLED and payments_approved_total:
                    payments_approved_total.inc()
            except Exception:
                pass

            conn.commit()
            conn.close()
            return
        
        # Проверяем, что роль совпадает
        if approval["required_role"] != role:
            raise InvalidApprovalError(
                f"Текущий шаг требует роль '{approval['required_role']}', а не '{role}'"
            )
        
        # Одобряем шаг
        cur.execute(
            """
            UPDATE payment_approvals
            SET status = 'approved', decided_at = ?, decided_by = ?, comment = ?
            WHERE id = ?
            """,
            (now, decided_by or role, comment, approval["id"])
        )
        
        # Проверяем, есть ли ещё pending шаги
        cur.execute(
            "SELECT COUNT(*) as cnt FROM payment_approvals WHERE payment_order_id = ? AND status = 'pending'",
            (order_id,)
        )
        pending_count = cur.fetchone()["cnt"]
        
        if pending_count == 0:
            # Все шаги одобрены
            self._set_state(
                conn,
                tenant_id=tenant_id,
                payment_id=order_id,
                new_state=PaymentState.APPROVED,
                actor="approver",
                reason=comment,
                decided_by=decided_by or role,
            )
            self._emit_lifecycle_event(
                event_type="payment.approved",
                tenant_id=tenant_id,
                payment_id=order_id,
                payload={"actor": "approver", "reason": comment, "decided_by": decided_by or role},
                conn=conn,
            )
            self._log_event(conn, tenant_id, order_id, "payment_order.approved", {"step": approval["step"], "role": role})
            
            # Интеграция с Case: создаём задачу на экспорт и событие, если есть case_id
            if order.get("case_id"):
                case_id = order["case_id"]
                try:
                    # Используем то же соединение для записи в case_tasks и case_events
                    cur.execute("SELECT * FROM cases WHERE id = ? AND tenant_id = ?", (case_id, tenant_id))
                    case = cur.fetchone()
                    if case:
                        # Создаём задачу на экспорт
                        task_id = str(uuid.uuid4())
                        step_key = case["current_step"] if case["current_step"] else "export"
                        cur.execute(
                            """
                            INSERT INTO case_tasks
                            (id, case_id, step_key, title, assignee_role, status, created_at)
                            VALUES (?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (task_id, case_id, step_key, "Выгрузить платеж", "accountant", now)
                        )
                        
                        # Логируем событие
                        event_id = str(uuid.uuid4())
                        event_payload = json.dumps({
                            "payment_order_id": order_id,
                            "amount": order["amount"]
                        }, ensure_ascii=False)
                        cur.execute(
                            """
                            INSERT INTO case_events
                            (id, case_id, event_type, payload_json, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (event_id, case_id, "payment_approved", event_payload, now)
                        )
                except Exception as e:
                    logger.warning(f"Ошибка при создании задачи экспорта в кейсе: {e}")
            
            # Метрики
            try:
                import time
                from cyberplat.observability.metrics import (
                    payment_orders_total,
                    payment_approval_latency_seconds,
                    payments_approved_total,
                    METRICS_ENABLED
                )
                if METRICS_ENABLED:
                    if payment_orders_total:
                        payment_orders_total.labels(status="approved").inc()
                    if payments_approved_total:
                        payments_approved_total.inc()
                    if payment_approval_latency_seconds:
                        # Вычисляем latency (упрощённо, в production нужно хранить submit timestamp)
                        latency = 0.0  # Для MVP упрощённо
                        payment_approval_latency_seconds.observe(latency)
            except Exception:
                pass
        else:
            self._log_event(conn, tenant_id, order_id, "payment_order.step_approved", {
                "step": approval["step"],
                "role": role,
                "remaining_steps": pending_count
            })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Шаг {approval['step']} согласования для {order_id} одобрен (role={role})")
    
    def reject(
        self,
        order_id: str,
        tenant_id: str,
        role: str,
        comment: Optional[str] = None,
        decided_by: Optional[str] = None
    ) -> None:
        """
        Отклонить платёжное поручение.
        
        Args:
            order_id: ID платёжного поручения
            tenant_id: ID тенанта
            role: Роль отклоняющего
            comment: Комментарий (опционально)
            decided_by: ID пользователя (опционально)
        """
        order = self.get_payment_order(order_id, tenant_id=tenant_id)
        if not order:
            raise PaymentNotFoundError(f"Платёжное поручение {order_id} не найдено")

        # Причина обязательна
        if not comment or not str(comment).strip():
            raise InvalidApprovalError("Причина отклонения обязательна")
        
        if order["status"] in {"rejected", "exported"}:
            if order["status"] == "rejected":
                logger.debug(f"Платёжное поручение {order_id} уже отклонено")
                return  # Идемпотентно
            raise InvalidApprovalError("Платёжное поручение уже экспортировано, нельзя отклонить")
        
        # Проверяем approved отдельно, чтобы можно было отклонить до экспорта
        if order["status"] == "approved":
            raise InvalidApprovalError("Платёжное поручение уже одобрено, нельзя отклонить")
        
        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        
        # Отклоняем все pending шаги
        cur.execute(
            """
            UPDATE payment_approvals
            SET status = 'rejected', decided_at = ?, decided_by = ?, comment = ?
            WHERE payment_order_id = ? AND status = 'pending'
            """,
            (now, decided_by or role, comment, order_id)
        )
        
        # Обновляем lifecycle state + события (не откатываем payment)
        self._set_state(
            conn,
            tenant_id=tenant_id,
            payment_id=order_id,
            new_state=PaymentState.REJECTED,
            actor="approver",
            reason=comment,
            decided_by=decided_by or role,
        )
        self._emit_lifecycle_event(
            event_type="payment.rejected",
            tenant_id=tenant_id,
            payment_id=order_id,
            payload={"actor": "approver", "reason": comment, "decided_by": decided_by or role},
            conn=conn,
        )
        
        self._log_event(conn, tenant_id, order_id, "payment_order.rejected", {
            "role": role,
            "comment": comment
        })
        
        # Интеграция с Case: логируем событие, если есть case_id
        if order.get("case_id"):
            case_id = order["case_id"]
            try:
                # Используем то же соединение для записи в case_events
                cur.execute("SELECT * FROM cases WHERE id = ? AND tenant_id = ?", (case_id, tenant_id))
                case = cur.fetchone()
                if case:
                    event_id = str(uuid.uuid4())
                    event_payload = json.dumps({
                        "payment_order_id": order_id,
                        "role": role,
                        "comment": comment
                    }, ensure_ascii=False)
                    cur.execute(
                        """
                        INSERT INTO case_events
                        (id, case_id, event_type, payload_json, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (event_id, case_id, "payment_rejected", event_payload, now)
                    )
            except Exception as e:
                logger.warning(f"Ошибка при записи события в кейс: {e}")
        
        # Метрики
        try:
            from cyberplat.observability.metrics import payment_orders_total, payments_rejected_total, METRICS_ENABLED
            if METRICS_ENABLED and payment_orders_total:
                payment_orders_total.labels(status="rejected").inc()
            if METRICS_ENABLED and payments_rejected_total:
                payments_rejected_total.inc()
        except Exception:
            pass
        
        conn.commit()
        conn.close()
        
        logger.info(f"Платёжное поручение {order_id} отклонено (role={role})")
    
    def export(
        self,
        order_id: str,
        tenant_id: str,
        format: str = "csv"
    ) -> Dict[str, Any]:
        """
        Экспортировать платёжное поручение.
        
        Args:
            order_id: ID платёжного поручения
            tenant_id: ID тенанта
            format: Формат экспорта ('csv' или 'onec')
            
        Returns:
            Dict с export_id и file_path/download_url
        """
        order = self.get_payment_order(order_id, tenant_id=tenant_id)
        if not order:
            raise PaymentNotFoundError(f"Платёжное поручение {order_id} не найдено")
        
        if order["status"] != "approved":
            raise InvalidApprovalError(f"Платёжное поручение должно быть одобрено для экспорта (текущий статус: {order['status']})")
        
        conn = self._get_connection()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        
        if format == "csv":
            # Экспорт в CSV
            export_id = str(uuid.uuid4())
            export_dir = Path("out/exports")
            export_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = export_dir / f"payment_{order_id}_{export_id}.csv"
            
            # Формируем CSV
            import csv
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["amount", "currency", "iban", "bic", "beneficiary_name", "purpose"])
                writer.writerow([
                    order["amount"],
                    order["currency"],
                    order["beneficiary_account_iban"],
                    order["beneficiary_bank_bic"] or "",
                    order["beneficiary_name"],
                    order["purpose"]
                ])
            
            # Обновляем статус
            cur.execute(
                "UPDATE payment_orders SET status = 'exported', exported_at = ?, updated_at = ? WHERE id = ?",
                (now, now, order_id)
            )
            
            self._log_event(conn, tenant_id, order_id, "payment_order.exported", {
                "format": format,
                "export_id": export_id,
                "file_path": str(file_path)
            })
            
            # Метрики
            try:
                from cyberplat.observability.metrics import payment_orders_total, payment_export_total, METRICS_ENABLED
                if METRICS_ENABLED:
                    if payment_orders_total:
                        payment_orders_total.labels(status="exported").inc()
                    if payment_export_total:
                        payment_export_total.labels(format=format, status="succeeded").inc()
            except Exception:
                pass
            
            conn.commit()
            conn.close()
            
            logger.info(f"Платёжное поручение {order_id} экспортировано в CSV: {file_path}")
            
            return {
                "export_id": export_id,
                "format": format,
                "file_path": str(file_path),
                "file_id": f"payment_{order_id}_{export_id}"
            }
        
        elif format == "onec":
            # Экспорт в 1С через OneCClient
            from cyberplat.integrations.onec_settings_service import OneCSettingsService
            from cyberplat.integrations.onec_client import OneCClient
            
            # Используем тот же db_path для settings
            settings_service = OneCSettingsService(db_path=self.db_path)
            settings = settings_service.get_settings(tenant_id)
            
            if not settings or not settings["enabled"]:
                conn.close()
                raise InvalidApprovalError("Интеграция 1С не настроена или отключена для tenant")
            
            # Создаём клиент 1С
            client = OneCClient(
                base_url=settings["base_url"],
                auth_type=settings["auth_type"],
                token=settings.get("token"),
                username=settings.get("username"),
                password=settings.get("password"),
                timeout=settings["timeout_seconds"]
            )
            
            # Формируем payload для 1С
            onec_payload = {
                "amount": order["amount"],
                "currency": order["currency"],
                "beneficiary_name": order["beneficiary_name"],
                "beneficiary_account": order["beneficiary_account_iban"],
                "purpose": order["purpose"]
            }
            
            if order.get("beneficiary_bank_bic"):
                onec_payload["beneficiary_bank_bic"] = order["beneficiary_bank_bic"]
            if order.get("beneficiary_iin_bin"):
                onec_payload["beneficiary_iin_bin"] = order["beneficiary_iin_bin"]
            
            # Вызываем API 1С (stub endpoint, в production будет реальный)
            try:
                idempotency_key = f"payment_order:{order_id}"
                response = client.request(
                    method="POST",
                    path="/payment-orders",
                    json_data=onec_payload,
                    idempotency_key=idempotency_key
                )
                
                remote_id = response.get("id") or response.get("remote_id")
                
                # Обновляем статус
                cur.execute(
                    "UPDATE payment_orders SET status = 'exported', exported_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, order_id)
                )
                
                export_id = str(uuid.uuid4())
                self._log_event(conn, tenant_id, order_id, "payment_order.exported", {
                    "format": format,
                    "export_id": export_id,
                    "remote_id": remote_id
                })
                
                # Метрики
                try:
                    from cyberplat.observability.metrics import payment_orders_total, payment_export_total, METRICS_ENABLED
                    if METRICS_ENABLED:
                        if payment_orders_total:
                            payment_orders_total.labels(status="exported").inc()
                        if payment_export_total:
                            payment_export_total.labels(format=format, status="succeeded").inc()
                except Exception:
                    pass
                
                conn.commit()
                conn.close()
                
                logger.info(f"Платёжное поручение {order_id} экспортировано в 1С (remote_id={remote_id})")
                
                return {
                    "export_id": export_id,
                    "format": format,
                    "remote_id": remote_id,
                    "status": "exported"
                }
            except Exception as e:
                conn.close()
                logger.error(f"Ошибка при экспорте в 1С: {e}", exc_info=True)
                raise InvalidApprovalError(f"Ошибка при экспорте в 1С: {str(e)[:200]}")
        
        else:
            conn.close()
            raise ValueError(f"Неподдерживаемый формат экспорта: {format}")
    
    def get_payment_policy(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Получить политику согласования для tenant."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM tenant_payment_policies WHERE tenant_id = ?", (tenant_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "tenant_id": row["tenant_id"],
            "enabled": bool(row["enabled"]),
            "thresholds": json.loads(row["thresholds_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
    
    def upsert_payment_policy(
        self,
        tenant_id: str,
        enabled: bool,
        thresholds: List[Dict[str, Any]]
    ) -> None:
        """Создать или обновить политику согласования."""
        now = datetime.now().isoformat()
        thresholds_json = json.dumps(thresholds, ensure_ascii=False)
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT OR REPLACE INTO tenant_payment_policies
            (tenant_id, enabled, thresholds_json, created_at, updated_at)
            VALUES (?, ?, ?, COALESCE((SELECT created_at FROM tenant_payment_policies WHERE tenant_id = ?), ?), ?)
            """,
            (tenant_id, enabled, thresholds_json, tenant_id, now, now)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Политика согласования обновлена для tenant: {tenant_id} (enabled={enabled})")
    
    def get_approvals(self, order_id: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Получить список согласований для платёжного поручения."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        if tenant_id:
            cur.execute(
                "SELECT * FROM payment_approvals WHERE payment_order_id = ? AND tenant_id = ? ORDER BY step ASC",
                (order_id, tenant_id)
            )
        else:
            cur.execute(
                "SELECT * FROM payment_approvals WHERE payment_order_id = ? ORDER BY step ASC",
                (order_id,)
            )
        
        rows = cur.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "payment_order_id": row["payment_order_id"],
                "tenant_id": row["tenant_id"],
                "step": row["step"],
                "required_role": row["required_role"],
                "status": row["status"],
                "decided_at": row["decided_at"],
                "decided_by": row["decided_by"],
                "comment": row["comment"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
