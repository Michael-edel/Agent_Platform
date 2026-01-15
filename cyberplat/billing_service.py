"""Сервис для multi-tenant billing (usage metering)."""

import uuid
import logging
import sqlite3
import os
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from calendar import monthrange

logger = logging.getLogger(__name__)


class PlanLimitExceededError(Exception):
    """Raised when a plan limit would be exceeded."""
    
    def __init__(self, metric: str, limit: int, used: int):
        self.metric = metric
        self.limit = limit
        self.used = used
        super().__init__(f"Plan limit exceeded for {metric}: {used}/{limit}")


class BillingService:
    """Сервис для управления биллингом и тарифами."""
    
    def __init__(self, db_path: str = "platform.db"):
        if db_path == "platform.db":
            # Для тестов/локального запуска можно переопределять путь через env.
            db_path = os.getenv("PLATFORM_DB_PATH", db_path)
        # В тестах часто используется db_path=":memory:".
        # Важно: обычный ":memory:" создаёт НОВУЮ БД на каждое sqlite3.connect(),
        # из-за чего таблицы "пропадают" между вызовами (no such table: billing_rates).
        # Чтобы сохранить поведение "в памяти" и при этом сделать его стабильным,
        # используем shared in-memory URI для каждого инстанса.
        self.db_path = db_path
        self._sqlite_connect_target = db_path
        self._sqlite_connect_kwargs = {}
        self._keeper_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._sqlite_connect_target = f"file:billing_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._sqlite_connect_kwargs = {"uri": True}
            # Держим "keeper" соединение открытым, иначе shared in-memory БД будет уничтожена
            # после закрытия первого connection (SQLite semantics).
            self._keeper_conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
            self._keeper_conn.row_factory = sqlite3.Row

        # КРИТИЧНО: Self-healing - автоматически создаем schema при инициализации
        # Это гарантирует, что billing_rates всегда существует, даже в тестах
        self.ensure_schema()
        self.seed_default_rates_if_empty()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._sqlite_connect_target, **self._sqlite_connect_kwargs)
        conn.row_factory = sqlite3.Row
        return conn
    
    def close(self) -> None:
        """
        Закрыть все соединения (для тестов и cleanup на Windows).
        
        Примечание: BillingService создает соединения по требованию и закрывает их сразу,
        но этот метод добавлен для совместимости и явного cleanup в тестах.
        На Windows SQLite может держать файл открытым, поэтому явное закрытие важно.
        """
        # BillingService использует соединения по требованию (_get_connection),
        # которые закрываются сразу после использования (conn.close()).
        # Этот метод добавлен для явного cleanup и совместимости с Windows.
        # Если в будущем добавим пул соединений, здесь будет их закрытие.
        try:
            if self._keeper_conn is not None:
                self._keeper_conn.close()
        except Exception:
            pass
    
    def ensure_schema(self) -> None:
        """Инициализировать таблицы для биллинга."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Таблица тарифов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_rates (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                metric TEXT NOT NULL,
                unit_price_minor INTEGER NOT NULL,
                currency TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                monthly_quota INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        
        # Добавляем поле monthly_quota если его нет (миграция)
        try:
            cur.execute("ALTER TABLE billing_rates ADD COLUMN monthly_quota INTEGER")
        except sqlite3.OperationalError:
            # Колонка уже существует
            pass
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_billing_rates_tenant_metric ON billing_rates(tenant_id, metric)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_billing_rates_active ON billing_rates(active)")
        
        # Partial UNIQUE indexes для поддержки UPSERT
        # Для default rates (tenant_id IS NULL) - UNIQUE по metric
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_rates_default_metric 
            ON billing_rates(metric) 
            WHERE tenant_id IS NULL
        """)
        # Для tenant-specific rates (tenant_id IS NOT NULL) - UNIQUE по (tenant_id, metric)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_rates_tenant_metric 
            ON billing_rates(tenant_id, metric) 
            WHERE tenant_id IS NOT NULL
        """)
        
        # Таблица использования
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_usage (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                artifact_id TEXT,
                event_type TEXT NOT NULL,
                metric TEXT NOT NULL,
                units REAL NOT NULL,
                unit_price_minor INTEGER NOT NULL,
                amount_minor INTEGER NOT NULL,
                currency TEXT NOT NULL,
                period TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_usage_event_id ON billing_usage(event_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_billing_usage_tenant_period ON billing_usage(tenant_id, period)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_billing_usage_period ON billing_usage(period)")
        
        # Таблица Kaspi payment profiles (для recurring billing)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_kaspi_profiles (
                tenant_id TEXT PRIMARY KEY,
                kaspi_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("База биллинга инициализирована: %s", self.db_path)
    
    def _init_database(self) -> None:
        """Алиас для ensure_schema (для обратной совместимости)."""
        self.ensure_schema()
    
    def resolve_rate(self, tenant_id: str, metric: str) -> Optional[Dict[str, Any]]:
        """
        Разрешить тариф для tenant и метрики.
        
        Приоритет:
        1) Tenant-specific активный тариф
        2) Default активный тариф (tenant_id IS NULL)
        3) None (бесплатно)
        
        Args:
            tenant_id: ID тенанта
            metric: Метрика (например, "invoice_extracted")
            
        Returns:
            Тариф или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Сначала ищем tenant-specific тариф
        cur.execute("""
            SELECT id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
            FROM billing_rates
            WHERE tenant_id = ? AND metric = ? AND active = 1
            ORDER BY created_at DESC
            LIMIT 1
        """, (tenant_id, metric))
        
        row = cur.fetchone()
        
        if not row:
            # Ищем default тариф (tenant_id IS NULL)
            cur.execute("""
                SELECT id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
                FROM billing_rates
                WHERE tenant_id IS NULL AND metric = ? AND active = 1
                ORDER BY created_at DESC
                LIMIT 1
            """, (metric,))
            
            row = cur.fetchone()
        
        conn.close()
        
        if row:
            return {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "metric": row["metric"],
                "unit_price_minor": row["unit_price_minor"],
                "currency": row["currency"],
                "active": bool(row["active"]),
                "monthly_quota": row["monthly_quota"] if "monthly_quota" in row.keys() else None,
                "created_at": row["created_at"]
            }
        
        return None
    
    def record_event_charge(
        self,
        tenant_id: str,
        event_id: str,
        artifact_id: Optional[str],
        event_type: str,
        metric: str,
        units: float,
        unit_price_minor: int,
        currency: str,
        period: str
    ) -> bool:
        """
        Записать использование (идемпотентно).
        
        Args:
            tenant_id: ID тенанта
            event_id: ID события (для идемпотентности)
            artifact_id: ID артефакта
            event_type: Тип события
            metric: Метрика
            units: Количество единиц
            unit_price_minor: Цена за единицу (в minor units)
            currency: Валюта
            period: Период (YYYY-MM)
            
        Returns:
            True если запись создана, False если уже существует (идемпотентность)
            
        Raises:
            PlanLimitExceededError: If plan limit would be exceeded
        """
        # Check plan limits before recording (soft enforcement)
        from app.billing.limits import check_plan_limit, LimitCheckResult
        try:
            from cyberplat.product.infrastructure.database import get_engine
            limit_result = check_plan_limit(
                tenant_id=tenant_id,
                metric=metric,
                increment=units,
                period=period,
                engine=get_engine(),
            )
            if not limit_result.allowed and limit_result.reason == "limit_exceeded":
                logger.warning(
                    f"Plan limit exceeded for tenant={tenant_id}, metric={metric}: "
                    f"used={limit_result.used}, limit={limit_result.limit}"
                )
                raise PlanLimitExceededError(
                    metric=metric,
                    limit=limit_result.limit,
                    used=limit_result.used,
                )
        except PlanLimitExceededError:
            raise
        except Exception as e:
            # Log but don't block on limit check failures
            logger.warning(f"Limit check failed for tenant={tenant_id}, metric={metric}: {e}")
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        usage_id = str(uuid.uuid4())
        amount_minor = int(units * unit_price_minor)
        created_at = datetime.now().isoformat()
        
        try:
            cur.execute("""
                INSERT INTO billing_usage (
                    id, tenant_id, event_id, artifact_id, event_type,
                    metric, units, unit_price_minor, amount_minor,
                    currency, period, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                usage_id,
                tenant_id,
                event_id,
                artifact_id,
                event_type,
                metric,
                units,
                unit_price_minor,
                amount_minor,
                currency,
                period,
                created_at
            ))
            
            conn.commit()
            conn.close()
            logger.debug(f"Записано использование: {metric} ({units} units) для tenant {tenant_id}")
            return True
            
        except sqlite3.IntegrityError as e:
            # UNIQUE constraint failed - уже начисляли
            conn.rollback()
            conn.close()
            logger.debug(f"Использование уже записано для event_id={event_id} (идемпотентность)")
            return False
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"Ошибка при записи использования: {e}", exc_info=True)
            raise
    
    def list_rates(self, tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Получить тарифы для tenant (tenant-specific + default).
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Словарь с ключами "tenant" и "default"
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Tenant-specific тарифы
        cur.execute("""
            SELECT id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
            FROM billing_rates
            WHERE tenant_id = ? AND active = 1
            ORDER BY metric
        """, (tenant_id,))
        
        tenant_rates = []
        for row in cur.fetchall():
            tenant_rates.append({
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "metric": row["metric"],
                "unit_price_minor": row["unit_price_minor"],
                "currency": row["currency"],
                "active": bool(row["active"]),
                "monthly_quota": row["monthly_quota"] if "monthly_quota" in row.keys() else None,
                "created_at": row["created_at"]
            })
        
        # Default тарифы
        cur.execute("""
            SELECT id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
            FROM billing_rates
            WHERE tenant_id IS NULL AND active = 1
            ORDER BY metric
        """)
        
        default_rates = []
        for row in cur.fetchall():
            default_rates.append({
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "metric": row["metric"],
                "unit_price_minor": row["unit_price_minor"],
                "currency": row["currency"],
                "active": bool(row["active"]),
                "monthly_quota": row["monthly_quota"] if "monthly_quota" in row.keys() else None,
                "created_at": row["created_at"]
            })
        
        conn.close()
        
        return {
            "tenant": tenant_rates,
            "default": default_rates
        }
    
    def upsert_rate(
        self,
        tenant_id: Optional[str],
        metric: str,
        unit_price_minor: int,
        currency: str,
        active: bool = True,
        monthly_quota: Optional[int] = None
    ) -> str:
        """
        Создать или обновить тариф (tenant-specific или default).
        
        Args:
            tenant_id: ID тенанта (None для default тарифов)
            metric: Метрика
            unit_price_minor: Цена за единицу (в minor units)
            currency: Валюта
            active: Активен ли тариф
            monthly_quota: Месячная квота (опционально)
            
        Returns:
            ID тарифа
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        rate_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        
        try:
            if tenant_id is None:
                # Default rate: проверяем существование и обновляем или создаем
                cur.execute("""
                    SELECT id FROM billing_rates
                    WHERE tenant_id IS NULL AND metric = ?
                    LIMIT 1
                """, (metric,))
                
                existing = cur.fetchone()
                
                if existing:
                    # Обновляем существующий default rate
                    cur.execute("""
                        UPDATE billing_rates
                        SET unit_price_minor = ?,
                            currency = ?,
                            active = ?,
                            monthly_quota = ?,
                            created_at = ?
                        WHERE id = ?
                    """, (
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at,
                        existing["id"]
                    ))
                    rate_id = existing["id"]
                else:
                    # Создаем новый default rate
                    cur.execute("""
                        INSERT INTO billing_rates (
                            id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rate_id,
                        None,  # tenant_id IS NULL для default
                        metric,
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at
                    ))
            else:
                # Tenant-specific rate: проверяем существование и обновляем или создаем
                cur.execute("""
                    SELECT id FROM billing_rates
                    WHERE tenant_id = ? AND metric = ?
                    LIMIT 1
                """, (tenant_id, metric))
                
                existing = cur.fetchone()
                
                if existing:
                    # Обновляем существующий tenant-specific rate
                    cur.execute("""
                        UPDATE billing_rates
                        SET unit_price_minor = ?,
                            currency = ?,
                            active = ?,
                            monthly_quota = ?,
                            created_at = ?
                        WHERE id = ?
                    """, (
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at,
                        existing["id"]
                    ))
                    rate_id = existing["id"]
                else:
                    # Создаем новый tenant-specific rate
                    cur.execute("""
                        INSERT INTO billing_rates (
                            id, tenant_id, metric, unit_price_minor, currency, active, monthly_quota, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rate_id,
                        tenant_id,
                        metric,
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at
                    ))
            
            conn.commit()
            conn.close()
            
        except sqlite3.IntegrityError:
            # Race condition: другой поток создал rate между SELECT и INSERT
            # Повторяем операцию (теперь rate должен существовать)
            conn.rollback()
            conn.close()
            
            # Повторяем операцию
            conn = self._get_connection()
            cur = conn.cursor()
            
            if tenant_id is None:
                cur.execute("""
                    SELECT id FROM billing_rates
                    WHERE tenant_id IS NULL AND metric = ?
                    LIMIT 1
                """, (metric,))
            else:
                cur.execute("""
                    SELECT id FROM billing_rates
                    WHERE tenant_id = ? AND metric = ?
                    LIMIT 1
                """, (tenant_id, metric))
            
            existing = cur.fetchone()
            
            if existing:
                # Обновляем существующий rate
                if tenant_id is None:
                    cur.execute("""
                        UPDATE billing_rates
                        SET unit_price_minor = ?,
                            currency = ?,
                            active = ?,
                            monthly_quota = ?,
                            created_at = ?
                        WHERE tenant_id IS NULL AND metric = ?
                    """, (
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at,
                        metric
                    ))
                else:
                    cur.execute("""
                        UPDATE billing_rates
                        SET unit_price_minor = ?,
                            currency = ?,
                            active = ?,
                            monthly_quota = ?,
                            created_at = ?
                        WHERE tenant_id = ? AND metric = ?
                    """, (
                        unit_price_minor,
                        currency,
                        1 if active else 0,
                        monthly_quota,
                        created_at,
                        tenant_id,
                        metric
                    ))
                rate_id = existing["id"]
            else:
                # Не должно произойти, но на всякий случай
                conn.close()
                raise sqlite3.IntegrityError("Rate не найден после IntegrityError")
            
            conn.commit()
            conn.close()
        
        logger.info(
            f"Создан/обновлен тариф: tenant={tenant_id}, metric={metric}, "
            f"price={unit_price_minor}, quota={monthly_quota}"
        )
        
        return rate_id
    
    def get_usage_for_metric(
        self,
        tenant_id: str,
        metric: str,
        period: str
    ) -> float:
        """
        Получить суммарное использование (units) для метрики за период.
        
        Args:
            tenant_id: ID тенанта
            metric: Метрика
            period: Период (YYYY-MM)
            
        Returns:
            Сумма units (float)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT SUM(units) as total_units
            FROM billing_usage
            WHERE tenant_id = ? AND metric = ? AND period = ?
        """, (tenant_id, metric, period))
        
        row = cur.fetchone()
        conn.close()
        
        if row and row["total_units"] is not None:
            return float(row["total_units"])
        
        return 0.0
    
    def check_quota(
        self,
        tenant_id: str,
        metric: str,
        period: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Проверить, не превышена ли квота для метрики.
        
        Args:
            tenant_id: ID тенанта
            metric: Метрика
            period: Период (YYYY-MM)
            
        Returns:
            (is_allowed, error_message)
            is_allowed: True если можно выполнить операцию, False если квота превышена
            error_message: Сообщение об ошибке, если квота превышена
        """
        # Получаем тариф (tenant-specific или default)
        rate = self.resolve_rate(tenant_id, metric)
        
        if not rate:
            # Нет тарифа - разрешаем (бесплатно)
            return True, None
        
        monthly_quota = rate.get("monthly_quota")
        
        # Если квота не установлена - разрешаем
        if monthly_quota is None:
            return True, None
        
        # Получаем текущее использование
        used_units = self.get_usage_for_metric(tenant_id, metric, period)
        
        # Проверяем квоту
        if used_units >= monthly_quota:
            return False, (
                f"Quota exceeded for {metric}: used={used_units}, quota={monthly_quota}. "
                f"Upgrade plan."
            )
        
        return True, None
    
    def get_usage(
        self,
        tenant_id: str,
        period: str
    ) -> Dict[str, Any]:
        """
        Получить использование за период.
        
        Args:
            tenant_id: ID тенанта
            period: Период (YYYY-MM)
            
        Returns:
            Словарь с usage lines и totals
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                id, tenant_id, event_id, artifact_id, event_type,
                metric, units, unit_price_minor, amount_minor,
                currency, period, created_at
            FROM billing_usage
            WHERE tenant_id = ? AND period = ?
            ORDER BY created_at ASC
        """, (tenant_id, period))
        
        lines = []
        total_amount_minor = 0
        currency = "USD"  # Default
        
        for row in cur.fetchall():
            line = {
                "id": row["id"],
                "event_id": row["event_id"],
                "artifact_id": row["artifact_id"],
                "event_type": row["event_type"],
                "metric": row["metric"],
                "units": row["units"],
                "unit_price_minor": row["unit_price_minor"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "created_at": row["created_at"]
            }
            lines.append(line)
            total_amount_minor += row["amount_minor"]
            currency = row["currency"]  # Берем валюту из последней записи
        
        conn.close()
        
        return {
            "tenant_id": tenant_id,
            "period": period,
            "currency": currency,
            "total_amount_minor": total_amount_minor,
            "lines": lines
        }
    
    def get_summary(
        self,
        tenant_id: str,
        from_period: str,
        to_period: str
    ) -> Dict[str, Any]:
        """
        Получить агрегированную сводку по периодам.
        
        Args:
            tenant_id: ID тенанта
            from_period: Начальный период (YYYY-MM)
            to_period: Конечный период (YYYY-MM)
            
        Returns:
            Словарь с агрегированными данными по периодам
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                period,
                currency,
                SUM(amount_minor) as total_amount_minor,
                COUNT(*) as line_count
            FROM billing_usage
            WHERE tenant_id = ? AND period >= ? AND period <= ?
            GROUP BY period, currency
            ORDER BY period ASC
        """, (tenant_id, from_period, to_period))
        
        periods = []
        grand_total_minor = 0
        currency = "USD"
        
        for row in cur.fetchall():
            period_data = {
                "period": row["period"],
                "currency": row["currency"],
                "total_amount_minor": row["total_amount_minor"],
                "line_count": row["line_count"]
            }
            periods.append(period_data)
            grand_total_minor += row["total_amount_minor"]
            currency = row["currency"]
        
        conn.close()
        
        return {
            "tenant_id": tenant_id,
            "from_period": from_period,
            "to_period": to_period,
            "currency": currency,
            "grand_total_amount_minor": grand_total_minor,
            "periods": periods
        }
    
    def get_invoice(
        self,
        tenant_id: str,
        period: str
    ) -> Dict[str, Any]:
        """
        Получить invoice (счёт) за период с агрегацией по метрикам.
        
        Args:
            tenant_id: ID тенанта
            period: Период (YYYY-MM)
            
        Returns:
            Словарь с агрегированными данными по метрикам и total
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем все usage строки за период
        cur.execute("""
            SELECT 
                metric, units, unit_price_minor, amount_minor, currency
            FROM billing_usage
            WHERE tenant_id = ? AND period = ?
            ORDER BY metric, created_at ASC
        """, (tenant_id, period))
        
        rows = cur.fetchall()
        conn.close()
        
        if not rows:
            return {
                "tenant_id": tenant_id,
                "period": period,
                "currency": "USD",
                "totals_by_metric": {},
                "total_amount_minor": 0
            }
        
        # Агрегируем по метрикам
        totals_by_metric = {}
        currencies = set()
        total_amount_minor = 0
        
        for row in rows:
            metric = row["metric"]
            units = row["units"]
            unit_price_minor = row["unit_price_minor"]
            amount_minor = row["amount_minor"]
            currency = row["currency"]
            
            currencies.add(currency)
            
            if metric not in totals_by_metric:
                totals_by_metric[metric] = {
                    "units": 0.0,
                    "unit_price_minor": unit_price_minor,
                    "amount_minor": 0
                }
            
            totals_by_metric[metric]["units"] += units
            totals_by_metric[metric]["amount_minor"] += amount_minor
            # Если unit_price_minor разный, берем последний (можно улучшить логику)
            totals_by_metric[metric]["unit_price_minor"] = unit_price_minor
            
            total_amount_minor += amount_minor
        
        # Проверка валют (должна быть одна)
        if len(currencies) > 1:
            logger.error(
                f"Обнаружены разные валюты в billing_usage для tenant={tenant_id}, period={period}: {currencies}"
            )
            raise ValueError(
                f"Multiple currencies found in billing_usage: {currencies}. "
                f"This indicates inconsistent rate configuration."
            )
        
        currency = currencies.pop() if currencies else "USD"
        
        return {
            "tenant_id": tenant_id,
            "period": period,
            "currency": currency,
            "totals_by_metric": totals_by_metric,
            "total_amount_minor": total_amount_minor
        }
    
    def seed_default_rates_if_empty(self) -> None:
        """Создать default тарифы, если их нет."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, есть ли уже default тарифы
        cur.execute("""
            SELECT COUNT(*) as count FROM billing_rates
            WHERE tenant_id IS NULL
        """)
        
        row = cur.fetchone()
        if row and row["count"] > 0:
            conn.close()
            logger.info("Default тарифы уже существуют, пропускаем bootstrapping")
            return
        
        # Создаем default тарифы
        default_rates = [
            ("document_upload", 5, "USD"),
            ("invoice_extracted", 25, "USD"),
            ("page_processed", 2, "USD"),
            ("payment_prepared", 5, "USD"),
            ("payment_ready", 10, "USD"),
            ("payment_invalid", 0, "USD"),  # Бесплатно
        ]
        
        created_at = datetime.now().isoformat()
        
        for metric, unit_price_minor, currency in default_rates:
            rate_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO billing_rates (
                    id, tenant_id, metric, unit_price_minor, currency, active, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                rate_id,
                None,  # tenant_id IS NULL для default
                metric,
                unit_price_minor,
                currency,
                1,
                created_at
            ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Создано {len(default_rates)} default тарифов")
    
    def get_quota_status(
        self,
        tenant_id: str,
        period: str
    ) -> Dict[str, Any]:
        """
        Получить статус квот по всем метрикам для tenant за период.
        
        Args:
            tenant_id: ID тенанта
            period: Период (YYYY-MM)
            
        Returns:
            Словарь с tenant_id, period, currency и metrics
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем все метрики с их использованием за период
        cur.execute("""
            SELECT 
                metric,
                SUM(units) as used_units,
                MAX(currency) as currency
            FROM billing_usage
            WHERE tenant_id = ? AND period = ?
            GROUP BY metric
        """, (tenant_id, period))
        
        usage_by_metric = {}
        currencies = set()
        
        for row in cur.fetchall():
            metric = row["metric"]
            used_units = float(row["used_units"]) if row["used_units"] is not None else 0.0
            currency = row["currency"]
            currencies.add(currency)
            usage_by_metric[metric] = {
                "used_units": used_units,
                "currency": currency
            }
        
        # Получаем все активные тарифы для tenant (tenant-specific + default)
        # Сначала tenant-specific
        cur.execute("""
            SELECT metric, unit_price_minor, currency, monthly_quota
            FROM billing_rates
            WHERE tenant_id = ? AND active = 1
        """, (tenant_id,))
        
        rates_by_metric = {}
        tenant_currencies = set()
        
        for row in cur.fetchall():
            metric = row["metric"]
            rates_by_metric[metric] = {
                "unit_price_minor": row["unit_price_minor"],
                "currency": row["currency"],
                "monthly_quota": row["monthly_quota"]
            }
            tenant_currencies.add(row["currency"])
        
        # Затем default тарифы (только для метрик, для которых нет tenant-specific)
        cur.execute("""
            SELECT metric, unit_price_minor, currency, monthly_quota
            FROM billing_rates
            WHERE tenant_id IS NULL AND active = 1
        """)
        
        for row in cur.fetchall():
            metric = row["metric"]
            if metric not in rates_by_metric:
                rates_by_metric[metric] = {
                    "unit_price_minor": row["unit_price_minor"],
                    "currency": row["currency"],
                    "monthly_quota": row["monthly_quota"]
                }
                tenant_currencies.add(row["currency"])
        
        conn.close()
        
        # Проверка валют
        if len(tenant_currencies) > 1:
            raise ValueError(
                f"Multiple currencies found in rates for tenant {tenant_id}: {tenant_currencies}. "
                f"This indicates inconsistent rate configuration."
            )
        
        # Определяем общую валюту
        currency = tenant_currencies.pop() if tenant_currencies else (currencies.pop() if currencies else "USD")
        
        # Собираем результат по всем метрикам
        metrics_result = {}
        
        # Обрабатываем метрики, для которых есть тарифы
        for metric, rate_info in rates_by_metric.items():
            used_units = usage_by_metric.get(metric, {}).get("used_units", 0.0)
            monthly_quota = rate_info["monthly_quota"]
            unit_price_minor = rate_info["unit_price_minor"]
            
            # Вычисляем remaining_units
            if monthly_quota is None:
                remaining_units = None
            else:
                remaining_units = max(0, int(monthly_quota - used_units))
            
            # is_exceeded
            is_exceeded = monthly_quota is not None and used_units >= monthly_quota
            
            # amount_minor (округление до int)
            amount_minor = int(used_units * unit_price_minor)
            
            metrics_result[metric] = {
                "used_units": used_units,
                "monthly_quota": monthly_quota,
                "remaining_units": remaining_units,
                "is_exceeded": is_exceeded,
                "unit_price_minor": unit_price_minor,
                "amount_minor": amount_minor
            }
        
        # Обрабатываем метрики, для которых есть usage, но нет тарифов
        for metric, usage_info in usage_by_metric.items():
            if metric not in metrics_result:
                used_units = usage_info["used_units"]
                # Нет тарифа - считаем бесплатно, но показываем usage
                metrics_result[metric] = {
                    "used_units": used_units,
                    "monthly_quota": None,
                    "remaining_units": None,
                    "is_exceeded": False,
                    "unit_price_minor": 0,
                    "amount_minor": 0
                }
        
        return {
            "tenant_id": tenant_id,
            "period": period,
            "currency": currency,
            "metrics": metrics_result
        }
    
    def reset_usage(
        self,
        tenant_id: str,
        period: str
    ) -> int:
        """
        Удалить все записи использования для tenant за период.
        
        Args:
            tenant_id: ID тенанта
            period: Период (YYYY-MM)
            
        Returns:
            Количество удалённых строк
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            DELETE FROM billing_usage
            WHERE tenant_id = ? AND period = ?
        """, (tenant_id, period))
        
        deleted_rows = cur.rowcount
        conn.commit()
        conn.close()
        
        logger.info(
            f"Удалено {deleted_rows} записей usage для tenant={tenant_id}, period={period}"
        )
        
        return deleted_rows
    
    def upsert_kaspi_profile(self, tenant_id: str, kaspi_token: str) -> None:
        """
        Создать или обновить Kaspi payment profile для tenant.
        
        Args:
            tenant_id: ID тенанта
            kaspi_token: Kaspi payment token (для recurring billing)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        updated_at = datetime.now().isoformat()
        
        cur.execute("""
            INSERT INTO billing_kaspi_profiles (
                tenant_id, kaspi_token, updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                kaspi_token = excluded.kaspi_token,
                updated_at = excluded.updated_at
        """, (tenant_id, kaspi_token, updated_at))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Kaspi profile обновлен для tenant={tenant_id}, kaspi_token={'***' if kaspi_token else None}")
    
    def get_kaspi_token(self, tenant_id: str) -> Optional[str]:
        """
        Получить Kaspi payment token для tenant.
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Kaspi token или None если не найден
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT kaspi_token
            FROM billing_kaspi_profiles
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return row["kaspi_token"]
        
        return None
