"""Сервис для работы с банковскими выписками и сверкой платежей."""

import uuid
import logging
import sqlite3
import os
import json
import csv
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from pathlib import Path
from decimal import Decimal

logger = logging.getLogger(__name__)


class ReconciliationError(Exception):
    """Ошибка сверки."""
    pass


class ReconciliationService:
    """Сервис для управления банковскими выписками и сверкой."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # Для db_path=":memory:" используем shared in-memory URI
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:reconciliation_{uuid.uuid4().hex}?mode=memory&cache=shared"
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
        """Инициализировать таблицы для сверки."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Таблица банковских выписок
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bank_statements (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                source TEXT NOT NULL,
                period_start DATE,
                period_end DATE,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица банковских транзакций
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bank_transactions (
                id TEXT PRIMARY KEY,
                statement_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                txn_date DATE NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'KZT',
                counterparty_name TEXT,
                counterparty_account TEXT,
                description TEXT,
                direction TEXT NOT NULL,
                matched BOOLEAN NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (statement_id) REFERENCES bank_statements(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица сопоставлений
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reconciliation_matches (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                payment_order_id TEXT,
                match_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (transaction_id) REFERENCES bank_transactions(id) ON DELETE CASCADE
            )
        """)
        
        # Таблица событий (audit)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reconciliation_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                statement_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (statement_id) REFERENCES bank_statements(id) ON DELETE CASCADE
            )
        """)
        
        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_statements_tenant_id ON bank_statements(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_statements_status ON bank_statements(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_transactions_statement_id ON bank_transactions(statement_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_transactions_tenant_id ON bank_transactions(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_transactions_matched ON bank_transactions(matched)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reconciliation_matches_transaction_id ON reconciliation_matches(transaction_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reconciliation_matches_payment_order_id ON reconciliation_matches(payment_order_id)")
        
        conn.commit()
        conn.close()
        logger.info("База сверки инициализирована: %s", self.db_path)
    
    def _log_event(self, conn: sqlite3.Connection, tenant_id: str, statement_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Записать событие в audit trail."""
        event_id = str(uuid.uuid4())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reconciliation_events (id, tenant_id, statement_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, tenant_id, statement_id, event_type, payload_json, datetime.now().isoformat())
        )
    
    def ingest_statement(
        self,
        tenant_id: str,
        source: str,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None
    ) -> str:
        """
        Создать банковскую выписку.
        
        Args:
            tenant_id: ID тенанта
            source: Источник (upload|api)
            period_start: Начало периода (опционально)
            period_end: Конец периода (опционально)
            
        Returns:
            ID созданной выписки
        """
        statement_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO bank_statements
            (id, tenant_id, source, period_start, period_end, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)
            """,
            (
                statement_id, tenant_id, source,
                period_start.isoformat() if period_start else None,
                period_end.isoformat() if period_end else None,
                now, now
            )
        )
        
        self._log_event(conn, tenant_id, statement_id, "statement.uploaded", {
            "source": source
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Создана банковская выписка: {statement_id} (tenant={tenant_id}, source={source})")
        return statement_id
    
    def parse_transactions_csv(self, statement_id: str, tenant_id: str, file_path: str) -> int:
        """
        Распарсить транзакции из CSV файла.
        
        Args:
            statement_id: ID выписки
            tenant_id: ID тенанта
            file_path: Путь к CSV файлу
            
        Returns:
            Количество распарсенных транзакций
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        transactions_count = 0
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                
                # Ожидаемые колонки: date, amount, description, counterparty
                for row in reader:
                    try:
                        txn_id = str(uuid.uuid4())
                        txn_date = datetime.strptime(row.get("date", ""), "%Y-%m-%d").date()
                        amount = float(row.get("amount", "0"))
                        description = row.get("description", "")
                        counterparty_name = row.get("counterparty", "")
                        
                        # Определяем direction по знаку amount
                        direction = "out" if amount < 0 else "in"
                        amount_abs = abs(amount)
                        
                        cur.execute(
                            """
                            INSERT INTO bank_transactions
                            (id, statement_id, tenant_id, txn_date, amount, currency, counterparty_name, description, direction, matched, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                            """,
                            (
                                txn_id, statement_id, tenant_id, txn_date.isoformat(),
                                amount_abs, "KZT", counterparty_name, description, direction,
                                datetime.now().isoformat()
                            )
                        )
                        transactions_count += 1
                    except Exception as e:
                        logger.warning(f"Ошибка при парсинге строки CSV: {e}, row={row}")
                        continue
            
            # Обновляем статус выписки
            cur.execute(
                "UPDATE bank_statements SET status = 'parsed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), statement_id)
            )
            
            self._log_event(conn, tenant_id, statement_id, "statement.parsed", {
                "transactions_count": transactions_count
            })
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            raise ReconciliationError(f"Ошибка при парсинге CSV: {str(e)}")
        
        conn.close()
        
        logger.info(f"Распарсено транзакций: {transactions_count} для выписки {statement_id}")
        return transactions_count
    
    def auto_match(
        self,
        statement_id: str,
        tenant_id: str,
        payment_service
    ) -> Dict[str, Any]:
        """
        Автоматическое сопоставление транзакций с платёжными поручениями.
        
        Args:
            statement_id: ID выписки
            tenant_id: ID тенанта
            payment_service: PaymentService для поиска payment orders
            
        Returns:
            Dict с результатами: matched_count, unmatched_count, matches
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем все unmatched транзакции с direction=out
        cur.execute(
            """
            SELECT * FROM bank_transactions
            WHERE statement_id = ? AND tenant_id = ? AND matched = 0 AND direction = 'out'
            ORDER BY txn_date DESC
            """,
            (statement_id, tenant_id)
        )
        transactions = cur.fetchall()
        
        matched_count = 0
        matches = []
        
        for txn in transactions:
            txn_id = txn["id"]
            txn_amount = txn["amount"]
            txn_date = datetime.strptime(txn["txn_date"], "%Y-%m-%d").date()
            txn_description = txn["description"] or ""
            
            # Ищем подходящие payment orders
            # Получаем все approved/exported orders для tenant
            payment_orders = payment_service.list_payment_orders(
                tenant_id=tenant_id,
                status=None,  # Все статусы
                limit=1000
            )
            
            best_match = None
            best_confidence = 0.0
            
            for order in payment_orders:
                # Пропускаем уже оплаченные или не подходящие по статусу
                if order["status"] not in {"approved", "exported"}:
                    continue
                
                order_amount = order["amount"]
                order_purpose = order.get("purpose", "")
                
                # Вычисляем confidence
                confidence = 0.0
                
                # 1. Amount match (0.6)
                amount_diff = abs(txn_amount - order_amount)
                amount_tolerance = order_amount * 0.01  # 1% tolerance
                if amount_diff <= amount_tolerance:
                    confidence += 0.6
                elif amount_diff <= order_amount * 0.05:  # 5% tolerance
                    confidence += 0.3
                
                # 2. Description hint (0.3)
                if order_purpose and txn_description:
                    # Простая проверка на наличие ключевых слов из purpose в description
                    purpose_words = set(order_purpose.lower().split())
                    desc_words = set(txn_description.lower().split())
                    common_words = purpose_words.intersection(desc_words)
                    if len(common_words) > 0:
                        confidence += 0.3 * min(len(common_words) / max(len(purpose_words), 1), 1.0)
                
                # 3. Date proximity (0.1) - упрощённо, в production можно учитывать дату экспорта
                # Для MVP пропускаем
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = order
            
            # Если confidence >= 0.8, создаём match
            if best_match and best_confidence >= 0.8:
                match_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO reconciliation_matches
                    (id, tenant_id, transaction_id, payment_order_id, match_type, confidence, created_at)
                    VALUES (?, ?, ?, ?, 'auto', ?, ?)
                    """,
                    (match_id, tenant_id, txn_id, best_match["id"], best_confidence, datetime.now().isoformat())
                )
                
                # Помечаем транзакцию как matched
                cur.execute(
                    "UPDATE bank_transactions SET matched = 1 WHERE id = ?",
                    (txn_id,)
                )
                
                matched_count += 1
                matches.append({
                    "transaction_id": txn_id,
                    "payment_order_id": best_match["id"],
                    "confidence": best_confidence
                })
        
        self._log_event(conn, tenant_id, statement_id, "statement.auto_matched", {
            "matched_count": matched_count,
            "total_transactions": len(transactions)
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Автосопоставление завершено: {matched_count}/{len(transactions)} транзакций для выписки {statement_id}")
        
        return {
            "matched_count": matched_count,
            "unmatched_count": len(transactions) - matched_count,
            "matches": matches
        }
    
    def manual_match(
        self,
        tenant_id: str,
        transaction_id: str,
        payment_order_id: str
    ) -> str:
        """
        Ручное сопоставление транзакции с платёжным поручением.
        
        Args:
            tenant_id: ID тенанта
            transaction_id: ID транзакции
            payment_order_id: ID платёжного поручения
            
        Returns:
            ID созданного match
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, что транзакция существует и принадлежит tenant
        cur.execute(
            "SELECT * FROM bank_transactions WHERE id = ? AND tenant_id = ?",
            (transaction_id, tenant_id)
        )
        txn = cur.fetchone()
        if not txn:
            conn.close()
            raise ReconciliationError(f"Транзакция {transaction_id} не найдена")
        
        # Проверяем, что payment_order существует (через payment_service)
        # Для упрощения, проверяем что match не существует
        cur.execute(
            "SELECT * FROM reconciliation_matches WHERE transaction_id = ?",
            (transaction_id,)
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            raise ReconciliationError("Транзакция уже сопоставлена")
        
        match_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO reconciliation_matches
            (id, tenant_id, transaction_id, payment_order_id, match_type, confidence, created_at)
            VALUES (?, ?, ?, ?, 'manual', 1.0, ?)
            """,
            (match_id, tenant_id, transaction_id, payment_order_id, datetime.now().isoformat())
        )
        
        # Помечаем транзакцию как matched
        cur.execute(
            "UPDATE bank_transactions SET matched = 1 WHERE id = ?",
            (transaction_id,)
        )
        
        # Логируем событие
        statement_id = txn["statement_id"]
        self._log_event(conn, tenant_id, statement_id, "statement.manual_matched", {
            "transaction_id": transaction_id,
            "payment_order_id": payment_order_id
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Ручное сопоставление: transaction={transaction_id}, payment_order={payment_order_id}")
        return match_id
    
    def finalize_statement(self, statement_id: str, tenant_id: str) -> None:
        """
        Завершить обработку выписки (пометить как reconciled).
        
        Args:
            statement_id: ID выписки
            tenant_id: ID тенанта
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, что все транзакции обработаны
        cur.execute(
            "SELECT COUNT(*) as cnt FROM bank_transactions WHERE statement_id = ? AND matched = 0",
            (statement_id,)
        )
        unmatched_count = cur.fetchone()["cnt"]
        
        if unmatched_count > 0:
            logger.warning(f"В выписке {statement_id} осталось {unmatched_count} несопоставленных транзакций")
        
        # Обновляем статус
        cur.execute(
            "UPDATE bank_statements SET status = 'reconciled', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), statement_id)
        )
        
        self._log_event(conn, tenant_id, statement_id, "statement.finalized", {
            "unmatched_count": unmatched_count
        })
        
        conn.commit()
        conn.close()
        
        logger.info(f"Выписка {statement_id} завершена (reconciled)")
    
    def get_statement(self, statement_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Получить выписку по ID."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        if tenant_id:
            cur.execute("SELECT * FROM bank_statements WHERE id = ? AND tenant_id = ?", (statement_id, tenant_id))
        else:
            cur.execute("SELECT * FROM bank_statements WHERE id = ?", (statement_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "source": row["source"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
    
    def get_matches(self, statement_id: Optional[str] = None, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Получить сопоставления."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM reconciliation_matches WHERE 1=1"
        params = []
        
        if statement_id:
            # Получаем через транзакции
            query = """
                SELECT rm.* FROM reconciliation_matches rm
                JOIN bank_transactions bt ON rm.transaction_id = bt.id
                WHERE bt.statement_id = ?
            """
            params.append(statement_id)
        
        if tenant_id:
            query += " AND rm.tenant_id = ?"
            params.append(tenant_id)
        
        query += " ORDER BY rm.created_at DESC"
        
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "transaction_id": row["transaction_id"],
                "payment_order_id": row["payment_order_id"],
                "match_type": row["match_type"],
                "confidence": row["confidence"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
    
    def get_transactions(self, statement_id: str, tenant_id: Optional[str] = None, matched: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Получить транзакции выписки."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        query = "SELECT * FROM bank_transactions WHERE statement_id = ?"
        params = [statement_id]
        
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        
        if matched is not None:
            query += " AND matched = ?"
            params.append(1 if matched else 0)
        
        query += " ORDER BY txn_date DESC"
        
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "statement_id": row["statement_id"],
                "tenant_id": row["tenant_id"],
                "txn_date": row["txn_date"],
                "amount": row["amount"],
                "currency": row["currency"],
                "counterparty_name": row["counterparty_name"],
                "counterparty_account": row["counterparty_account"],
                "description": row["description"],
                "direction": row["direction"],
                "matched": bool(row["matched"]),
                "created_at": row["created_at"]
            }
            for row in rows
        ]
