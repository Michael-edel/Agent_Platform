"""Сервис для управления планами, подписками и entitlements (лимиты/квоты)."""

import uuid
import logging
import sqlite3
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class EntitlementService:
    """Сервис для управления планами, подписками и entitlements."""
    
    def __init__(self, db_path: str = "platform.db"):
        self.db_path = db_path
        self._conn = None  # Для отслеживания открытых соединений (если нужно)
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def close(self) -> None:
        """
        Закрыть все соединения (для тестов и cleanup на Windows).
        
        Примечание: EntitlementService создает соединения по требованию и закрывает их сразу,
        но этот метод добавлен для совместимости и явного cleanup в тестах.
        На Windows SQLite может держать файл открытым, поэтому явное закрытие важно.
        """
        try:
            if hasattr(self, "_conn") and self._conn:
                self._conn.close()
                self._conn = None
        except Exception:
            pass
    
    def ensure_schema(self) -> None:
        """Инициализировать таблицы для планов и подписок."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Таблица планов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_plans (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                currency TEXT NOT NULL,
                price_minor INTEGER NOT NULL,
                period TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        
        # Таблица лимитов планов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_plan_limits (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                monthly_quota INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES billing_plans(id)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_plan_limits_plan_id ON billing_plan_limits(plan_id)")
        
        # Таблица подписок tenant'ов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_subscriptions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                provider_customer_id TEXT,
                provider_subscription_id TEXT,
                plan_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_period_start TEXT,
                current_period_end TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES billing_plans(id)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_id ON tenant_subscriptions(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_provider ON tenant_subscriptions(provider)")
        
        # Таблица webhook событий (для идемпотентности)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_webhook_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                tenant_id TEXT,
                raw_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            )
        """)
        
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_events_event_id ON billing_webhook_events(event_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhook_events_provider ON billing_webhook_events(provider)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON billing_webhook_events(status)")
        
        # Таблица payment profiles (Stripe customer_id, Kaspi token и т.д.)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_tenant_payment_profiles (
                tenant_id TEXT PRIMARY KEY,
                stripe_customer_id TEXT,
                kaspi_token TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Таблица заказов (Kaspi orders)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                amount_minor INTEGER NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                external_order_id TEXT,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                FOREIGN KEY (plan_id) REFERENCES billing_plans(id)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_tenant_id ON billing_orders(tenant_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_external_id ON billing_orders(external_order_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON billing_orders(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_provider ON billing_orders(provider)")
        
        conn.commit()
        conn.close()
        logger.info("База entitlements инициализирована: %s", self.db_path)
    
    def seed_default_plans_if_empty(self) -> None:
        """Создать default планы, если их нет."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, есть ли уже планы
        cur.execute("SELECT COUNT(*) as count FROM billing_plans")
        row = cur.fetchone()
        if row and row["count"] > 0:
            conn.close()
            logger.info("Планы уже существуют, пропускаем bootstrapping")
            return
        
        # Создаем default планы
        created_at = datetime.now().isoformat()
        
        # Free plan
        plan_free_id = "plan_free"
        cur.execute("""
            INSERT INTO billing_plans (id, name, currency, price_minor, period, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan_free_id, "Free Plan", "USD", 0, "monthly", 1, created_at))
        
        # Free plan limits (минимальные квоты)
        free_limits = [
            ("document_upload", 10),
            ("invoice_extracted", 5),
            ("page_processed", 50),
            ("payment_prepared", 10),
            ("payment_ready", 10),
        ]
        for metric, quota in free_limits:
            limit_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO billing_plan_limits (id, plan_id, metric, monthly_quota, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (limit_id, plan_free_id, metric, quota, created_at))
        
        # Pro plan
        plan_pro_id = "plan_pro"
        cur.execute("""
            INSERT INTO billing_plans (id, name, currency, price_minor, period, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan_pro_id, "Pro Plan", "USD", 2999, "monthly", 1, created_at))
        
        # Pro plan limits (высокие квоты)
        pro_limits = [
            ("document_upload", 1000),
            ("invoice_extracted", 500),
            ("page_processed", 5000),
            ("payment_prepared", 500),
            ("payment_ready", 500),
        ]
        for metric, quota in pro_limits:
            limit_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO billing_plan_limits (id, plan_id, metric, monthly_quota, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (limit_id, plan_pro_id, metric, quota, created_at))
        
        # Enterprise plan
        plan_enterprise_id = "plan_enterprise"
        cur.execute("""
            INSERT INTO billing_plans (id, name, currency, price_minor, period, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan_enterprise_id, "Enterprise Plan", "USD", 9999, "monthly", 1, created_at))
        
        # Enterprise plan limits (unlimited = NULL)
        enterprise_limits = [
            ("document_upload", None),  # NULL = unlimited
            ("invoice_extracted", None),
            ("page_processed", None),
            ("payment_prepared", None),
            ("payment_ready", None),
        ]
        for metric, quota in enterprise_limits:
            limit_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO billing_plan_limits (id, plan_id, metric, monthly_quota, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (limit_id, plan_enterprise_id, metric, quota, created_at))
        
        conn.commit()
        conn.close()
        logger.info("Создано 3 default плана: free, pro, enterprise")
    
    def get_plan_limits(self, plan_id: str) -> List[Dict[str, Any]]:
        """
        Получить лимиты плана.
        
        Args:
            plan_id: ID плана
            
        Returns:
            Список лимитов (metric, monthly_quota)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT metric, monthly_quota
            FROM billing_plan_limits
            WHERE plan_id = ?
        """, (plan_id,))
        
        limits = []
        for row in cur.fetchall():
            limits.append({
                "metric": row["metric"],
                "monthly_quota": row["monthly_quota"]
            })
        
        conn.close()
        return limits
    
    def apply_plan_to_tenant(
        self,
        tenant_id: str,
        plan_id: str,
        *,
        period_start: str,
        period_end: str,
        provider: str,
        provider_customer_id: Optional[str] = None,
        provider_subscription_id: Optional[str] = None,
        status: str = "active"
    ) -> str:
        """
        Применить план к tenant'у (обновить подписку и квоты).
        
        Args:
            tenant_id: ID тенанта
            plan_id: ID плана
            period_start: Начало периода (ISO timestamp)
            period_end: Конец периода (ISO timestamp)
            provider: Провайдер (stripe, kaspi, yoomoney)
            provider_customer_id: ID клиента у провайдера
            provider_subscription_id: ID подписки у провайдера
            status: Статус подписки (active, past_due, canceled, trialing)
            
        Returns:
            ID подписки
        """
        from cyberplat.billing_service import BillingService
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, есть ли уже подписка для tenant
        cur.execute("""
            SELECT id FROM tenant_subscriptions
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        row = cur.fetchone()
        subscription_id = row["id"] if row else str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        if row:
            # Обновляем существующую подписку
            cur.execute("""
                UPDATE tenant_subscriptions
                SET provider = ?,
                    provider_customer_id = ?,
                    provider_subscription_id = ?,
                    plan_id = ?,
                    status = ?,
                    current_period_start = ?,
                    current_period_end = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                provider,
                provider_customer_id,
                provider_subscription_id,
                plan_id,
                status,
                period_start,
                period_end,
                now,
                subscription_id
            ))
        else:
            # Создаем новую подписку
            cur.execute("""
                INSERT INTO tenant_subscriptions (
                    id, tenant_id, provider, provider_customer_id,
                    provider_subscription_id, plan_id, status,
                    current_period_start, current_period_end,
                    updated_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subscription_id,
                tenant_id,
                provider,
                provider_customer_id,
                provider_subscription_id,
                plan_id,
                status,
                period_start,
                period_end,
                now,
                now
            ))
        
        conn.commit()
        conn.close()
        
        # Получаем лимиты плана
        plan_limits = self.get_plan_limits(plan_id)
        
        # Обновляем billing_rates для tenant (квоты из плана)
        billing_service = BillingService(db_path=self.db_path)
        
        for limit in plan_limits:
            metric = limit["metric"]
            monthly_quota = limit["monthly_quota"]
            
            # Получаем текущий тариф для определения unit_price_minor и currency
            rate = billing_service.resolve_rate(tenant_id, metric)
            
            if rate:
                # Обновляем существующий тариф, добавляя/обновляя quota
                billing_service.upsert_rate(
                    tenant_id=tenant_id,
                    metric=metric,
                    unit_price_minor=rate["unit_price_minor"],
                    currency=rate["currency"],
                    active=True,
                    monthly_quota=monthly_quota
                )
            else:
                # Создаем новый тариф с quota (используем default цены)
                # Получаем default тариф для цены (tenant_id=None для default)
                # Используем внутренний метод для получения default rate
                conn = billing_service._get_connection()
                cur = conn.cursor()
                cur.execute("""
                    SELECT unit_price_minor, currency
                    FROM billing_rates
                    WHERE tenant_id IS NULL AND metric = ? AND active = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (metric,))
                default_row = cur.fetchone()
                conn.close()
                
                if default_row:
                    billing_service.upsert_rate(
                        tenant_id=tenant_id,
                        metric=metric,
                        unit_price_minor=default_row["unit_price_minor"],
                        currency=default_row["currency"],
                        active=True,
                        monthly_quota=monthly_quota
                    )
                else:
                    # Если нет default тарифа, используем дефолтные значения
                    logger.warning(
                        f"Нет default тарифа для metric={metric}, "
                        f"используем дефолтные значения для tenant={tenant_id}"
                    )
                    billing_service.upsert_rate(
                        tenant_id=tenant_id,
                        metric=metric,
                        unit_price_minor=0,  # Дефолтная цена
                        currency="USD",
                        active=True,
                        monthly_quota=monthly_quota
                    )
        
        logger.info(
            f"План {plan_id} применен к tenant {tenant_id}: "
            f"subscription_id={subscription_id}, status={status}"
        )
        
        return subscription_id
    
    def record_webhook_event(
        self,
        provider: str,
        event_id: str,
        raw_json: str,
        tenant_id: Optional[str] = None
    ) -> str:
        """
        Записать webhook событие (идемпотентно).
        
        Args:
            provider: Провайдер (stripe, kaspi, yoomoney)
            event_id: ID события (уникальный у провайдера)
            raw_json: Полный JSON payload
            tenant_id: ID тенанта (если определен)
            
        Returns:
            ID записи (новой или существующей)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Проверяем, есть ли уже событие
        cur.execute("""
            SELECT id, status FROM billing_webhook_events
            WHERE event_id = ?
        """, (event_id,))
        
        row = cur.fetchone()
        
        if row:
            # Событие уже существует
            conn.close()
            logger.debug(f"Webhook событие {event_id} уже обработано (id={row['id']}, status={row['status']})")
            return row["id"]
        
        # Создаем новую запись
        webhook_id = str(uuid.uuid4())
        received_at = datetime.now().isoformat()
        
        try:
            cur.execute("""
                INSERT INTO billing_webhook_events (
                    id, provider, event_id, received_at,
                    tenant_id, raw_json, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                webhook_id,
                provider,
                event_id,
                received_at,
                tenant_id,
                raw_json,
                "received"
            ))
            
            conn.commit()
            conn.close()
            logger.info(f"Записано webhook событие: {provider}/{event_id} (id={webhook_id})")
            return webhook_id
            
        except sqlite3.IntegrityError:
            # UNIQUE constraint failed - событие уже существует (race condition)
            conn.rollback()
            conn.close()
            # Повторно читаем
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM billing_webhook_events WHERE event_id = ?", (event_id,))
            row = cur.fetchone()
            conn.close()
            return row["id"] if row else webhook_id
    
    def mark_webhook_processed(
        self,
        webhook_id: str,
        tenant_id: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Отметить webhook событие как обработанное.
        
        Args:
            webhook_id: ID записи webhook события
            tenant_id: ID тенанта (если определен)
            error: Сообщение об ошибке (если была)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        processed_at = datetime.now().isoformat()
        status = "error" if error else "processed"
        
        cur.execute("""
            UPDATE billing_webhook_events
            SET processed_at = ?,
                tenant_id = ?,
                status = ?,
                error = ?
            WHERE id = ?
        """, (processed_at, tenant_id, status, error, webhook_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Webhook событие {webhook_id} отмечено как {status}")
    
    def mark_webhook_ignored(self, webhook_id: str, reason: str) -> None:
        """
        Отметить webhook событие как проигнорированное.
        
        Args:
            webhook_id: ID записи webhook события
            reason: Причина игнорирования
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        processed_at = datetime.now().isoformat()
        
        cur.execute("""
            UPDATE billing_webhook_events
            SET processed_at = ?,
                status = ?,
                error = ?
            WHERE id = ?
        """, (processed_at, "ignored", reason, webhook_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Webhook событие {webhook_id} проигнорировано: {reason}")
    
    def get_payment_profile(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить payment profile для tenant.
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Словарь с stripe_customer_id, kaspi_token или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT tenant_id, stripe_customer_id, kaspi_token, updated_at
            FROM billing_tenant_payment_profiles
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                "tenant_id": row["tenant_id"],
                "stripe_customer_id": row["stripe_customer_id"],
                "kaspi_token": row["kaspi_token"],
                "updated_at": row["updated_at"]
            }
        
        return None
    
    def upsert_payment_profile(
        self,
        tenant_id: str,
        stripe_customer_id: Optional[str] = None,
        kaspi_token: Optional[str] = None
    ) -> None:
        """
        Создать или обновить payment profile для tenant.
        
        Args:
            tenant_id: ID тенанта
            stripe_customer_id: Stripe customer ID (может быть None)
            kaspi_token: Kaspi payment token (может быть None)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем текущий профиль, чтобы не перезаписывать существующие значения
        cur.execute("""
            SELECT stripe_customer_id, kaspi_token
            FROM billing_tenant_payment_profiles
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        existing = cur.fetchone()
        
        # Если профиль существует, обновляем только переданные поля
        if existing:
            final_stripe_id = stripe_customer_id if stripe_customer_id is not None else existing["stripe_customer_id"]
            final_kaspi_token = kaspi_token if kaspi_token is not None else existing["kaspi_token"]
        else:
            final_stripe_id = stripe_customer_id
            final_kaspi_token = kaspi_token
        
        updated_at = datetime.now().isoformat()
        
        cur.execute("""
            INSERT INTO billing_tenant_payment_profiles (
                tenant_id, stripe_customer_id, kaspi_token, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                stripe_customer_id = excluded.stripe_customer_id,
                kaspi_token = excluded.kaspi_token,
                updated_at = excluded.updated_at
        """, (tenant_id, final_stripe_id, final_kaspi_token, updated_at))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Payment profile обновлен для tenant={tenant_id}, stripe_customer_id={final_stripe_id}, kaspi_token={'***' if final_kaspi_token else None}")
    
    def create_order(
        self,
        tenant_id: str,
        provider: str,
        plan_id: str,
        amount_minor: int,
        currency: str,
        external_order_id: Optional[str] = None
    ) -> str:
        """
        Создать заказ (order).
        
        Args:
            tenant_id: ID тенанта
            provider: Провайдер (kaspi, stripe, yoomoney)
            plan_id: ID плана
            amount_minor: Сумма в minor units (копейки)
            currency: Валюта (KZT, USD, RUB)
            external_order_id: Внешний ID заказа (от провайдера)
            
        Returns:
            ID заказа
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        order_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        
        cur.execute("""
            INSERT INTO billing_orders (
                id, tenant_id, provider, plan_id,
                amount_minor, currency, status,
                external_order_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_id,
            tenant_id,
            provider,
            plan_id,
            amount_minor,
            currency,
            "pending",
            external_order_id,
            created_at
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Создан заказ: {order_id} (tenant={tenant_id}, provider={provider}, plan={plan_id})")
        
        return order_id
    
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить заказ по ID.
        
        Args:
            order_id: ID заказа
            
        Returns:
            Словарь с данными заказа или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, tenant_id, provider, plan_id,
                   amount_minor, currency, status,
                   external_order_id, created_at, paid_at
            FROM billing_orders
            WHERE id = ?
        """, (order_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "provider": row["provider"],
                "plan_id": row["plan_id"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "status": row["status"],
                "external_order_id": row["external_order_id"],
                "created_at": row["created_at"],
                "paid_at": row["paid_at"]
            }
        
        return None
    
    def get_order_by_external_id(self, external_order_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить заказ по внешнему ID (от провайдера).
        
        Args:
            external_order_id: Внешний ID заказа
            
        Returns:
            Словарь с данными заказа или None
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, tenant_id, provider, plan_id,
                   amount_minor, currency, status,
                   external_order_id, created_at, paid_at
            FROM billing_orders
            WHERE external_order_id = ?
        """, (external_order_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "provider": row["provider"],
                "plan_id": row["plan_id"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "status": row["status"],
                "external_order_id": row["external_order_id"],
                "created_at": row["created_at"],
                "paid_at": row["paid_at"]
            }
        
        return None
    
    def update_order_status(
        self,
        order_id: str,
        status: str,
        external_order_id: Optional[str] = None
    ) -> None:
        """
        Обновить статус заказа.
        
        Args:
            order_id: ID заказа
            status: Новый статус (pending, paid, failed, canceled)
            external_order_id: Внешний ID заказа (если нужно обновить)
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        paid_at = datetime.now().isoformat() if status == "paid" else None
        
        if external_order_id:
            cur.execute("""
                UPDATE billing_orders
                SET status = ?,
                    external_order_id = ?,
                    paid_at = ?
                WHERE id = ?
            """, (status, external_order_id, paid_at, order_id))
        else:
            cur.execute("""
                UPDATE billing_orders
                SET status = ?,
                    paid_at = ?
                WHERE id = ?
            """, (status, paid_at, order_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Статус заказа {order_id} обновлен: {status}")
    
    def get_tenant_plan_info(self, tenant_id: str) -> Dict[str, Any]:
        """
        Получить информацию о плане и подписке для tenant.
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Словарь с plan и subscription информацией
        """
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Получаем подписку tenant'а
        cur.execute("""
            SELECT 
                s.id,
                s.plan_id,
                s.status,
                s.provider_subscription_id,
                p.name as plan_name
            FROM tenant_subscriptions s
            LEFT JOIN billing_plans p ON s.plan_id = p.id
            WHERE s.tenant_id = ?
            LIMIT 1
        """, (tenant_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                "plan": {
                    "id": row["plan_id"],
                    "name": row["plan_name"] or row["plan_id"]
                },
                "subscription": {
                    "status": row["status"],
                    "subscription_id": row["provider_subscription_id"]
                }
            }
        else:
            # Нет подписки - возвращаем free plan
            return {
                "plan": {
                    "id": "plan_free",
                    "name": "Free"
                },
                "subscription": {
                    "status": "none",
                    "subscription_id": None
                }
            }
    
    def save_kaspi_token(self, tenant_id: str, token: str) -> None:
        """
        Сохранить Kaspi payment token для tenant (proxy к BillingService).
        
        Args:
            tenant_id: ID тенанта
            token: Kaspi payment token
        """
        from cyberplat.billing_service import BillingService
        
        # Создаем BillingService с тем же db_path
        billing_service = BillingService(db_path=self.db_path)
        try:
            billing_service.upsert_kaspi_profile(tenant_id, token)
        finally:
            billing_service.close()  # КРИТИЧНО: закрываем соединения для Windows
    
    def get_kaspi_token(self, tenant_id: str) -> Optional[str]:
        """
        Получить Kaspi payment token для tenant (proxy к BillingService).
        
        Args:
            tenant_id: ID тенанта
            
        Returns:
            Kaspi token или None если не найден
        """
        from cyberplat.billing_service import BillingService
        
        # Создаем BillingService с тем же db_path
        billing_service = BillingService(db_path=self.db_path)
        try:
            return billing_service.get_kaspi_token(tenant_id)
        finally:
            billing_service.close()  # КРИТИЧНО: закрываем соединения для Windows
