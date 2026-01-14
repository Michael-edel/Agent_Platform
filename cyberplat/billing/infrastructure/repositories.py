"""Repository adapters wrapping existing services."""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import (
    SubscriptionRepository,
    WebhookEventRepository
)

logger = logging.getLogger(__name__)


class EntitlementSubscriptionRepository(SubscriptionRepository):
    """Repository adapter wrapping EntitlementService."""
    
    def __init__(self, entitlement_service, billing_service=None):
        """
        Args:
            entitlement_service: EntitlementService instance
            billing_service: BillingService instance (опционально, для Kaspi токенов)
        """
        self.entitlement_service = entitlement_service
        # Сохраняем billing_service для доступа из handlers (для Kaspi токенов)
        if billing_service:
            # Сохраняем как атрибут entitlement_service для доступа из handlers
            self.entitlement_service._billing_service = billing_service
    
    def get_active_subscriptions_for_renewal(
        self,
        provider: str,
        period_end_threshold: str  # ISO format
    ) -> List[Dict[str, Any]]:
        """
        Получить активные подписки для продления.
        
        Этот метод оборачивает существующую логику из kaspi_recurring.py
        для получения подписок, требующих продления.
        
        Для Kaspi: возвращает подписки с kaspi_token
        Для Stripe: возвращает подписки с provider_subscription_id (без kaspi_token)
        """
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        # Конвертируем threshold в формат для SQLite
        now = datetime.now()
        now_sql = now.strftime("%Y-%m-%d %H:%M:%S")
        
        if provider == "kaspi":
            # Получаем активные подписки с payment tokens (Kaspi)
            cur.execute("""
                SELECT 
                    s.id as subscription_id,
                    s.tenant_id,
                    s.plan_id,
                    s.current_period_end,
                    p.price_minor,
                    p.currency,
                    kp.kaspi_token,
                    s.provider_subscription_id,
                    s.provider_customer_id
                FROM tenant_subscriptions s
                INNER JOIN billing_plans p ON s.plan_id = p.id
                LEFT JOIN billing_kaspi_profiles kp ON kp.tenant_id = s.tenant_id
                WHERE s.provider = ?
                  AND s.status = 'active'
                  AND kp.kaspi_token IS NOT NULL
                  AND (s.current_period_end IS NULL 
                       OR julianday(replace(substr(s.current_period_end,1,19),'T',' ')) < julianday(?))
            """, (provider, now_sql))
        elif provider == "stripe":
            # Получаем активные подписки Stripe (без kaspi_token)
            cur.execute("""
                SELECT 
                    s.id as subscription_id,
                    s.tenant_id,
                    s.plan_id,
                    s.current_period_end,
                    p.price_minor,
                    p.currency,
                    NULL as kaspi_token,
                    s.provider_subscription_id,
                    s.provider_customer_id
                FROM tenant_subscriptions s
                INNER JOIN billing_plans p ON s.plan_id = p.id
                WHERE s.provider = ?
                  AND s.status = 'active'
                  AND s.provider_subscription_id IS NOT NULL
                  AND (s.current_period_end IS NULL 
                       OR julianday(replace(substr(s.current_period_end,1,19),'T',' ')) < julianday(?))
            """, (provider, now_sql))
        else:
            conn.close()
            return []
        
        subscriptions = []
        for row in cur.fetchall():
            subscriptions.append({
                "subscription_id": row["subscription_id"],
                "tenant_id": row["tenant_id"],
                "plan_id": row["plan_id"],
                "current_period_end": row["current_period_end"],
                "price_minor": row["price_minor"],
                "currency": row["currency"],
                "payment_token": row["kaspi_token"],  # None для Stripe
                "provider_subscription_id": row["provider_subscription_id"],
                "provider_customer_id": row["provider_customer_id"]
            })
        
        conn.close()
        return subscriptions
    
    def get_subscription(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Получить подписку по tenant_id."""
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, tenant_id, provider, provider_customer_id,
                   provider_subscription_id, plan_id, status,
                   current_period_start, current_period_end,
                   updated_at, created_at
            FROM tenant_subscriptions
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "provider": row["provider"],
            "provider_customer_id": row["provider_customer_id"],
            "provider_subscription_id": row["provider_subscription_id"],
            "plan_id": row["plan_id"],
            "status": row["status"],
            "current_period_start": row["current_period_start"],
            "current_period_end": row["current_period_end"],
            "updated_at": row["updated_at"],
            "created_at": row["created_at"]
        }
    
    def apply_plan(
        self,
        tenant_id: str,
        plan_id: str,
        period_start: str,  # ISO format
        period_end: str,  # ISO format
        provider: str,
        provider_customer_id: Optional[str],
        provider_subscription_id: Optional[str],
        status: str
    ) -> None:
        """Применить план к тенанту."""
        self.entitlement_service.apply_plan_to_tenant(
            tenant_id=tenant_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            provider=provider,
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            status=status
        )


class EntitlementWebhookEventRepository(WebhookEventRepository):
    """Repository adapter wrapping EntitlementService for webhook events."""
    
    def __init__(self, entitlement_service):
        """
        Args:
            entitlement_service: EntitlementService instance
        """
        self.entitlement_service = entitlement_service
    
    def record_event_received(
        self,
        provider: str,
        event_id: str,
        raw_json: str,
        tenant_id: Optional[str] = None
    ) -> None:
        """Записать получение события."""
        self.entitlement_service.record_webhook_event(
            provider=provider,
            event_id=event_id,
            raw_json=raw_json,
            tenant_id=tenant_id
        )
    
    def is_event_processed(
        self,
        provider: str,
        event_id: str
    ) -> bool:
        """Проверить, обработано ли событие."""
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT status
            FROM billing_webhook_events
            WHERE event_id = ? AND provider = ?
        """, (event_id, provider))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return False
        
        return row["status"] == "processed"
    
    def get_event_tenant_id(
        self,
        provider: str,
        event_id: str
    ) -> Optional[str]:
        """Получить tenant_id из уже обработанного события (для идемпотентного ответа)."""
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT tenant_id
            FROM billing_webhook_events
            WHERE event_id = ? AND provider = ? AND status = 'processed'
        """, (event_id, provider))
        
        row = cur.fetchone()
        conn.close()
        
        if row and row["tenant_id"]:
            return row["tenant_id"]
        
        return None
    
    def mark_event_processed(
        self,
        provider: str,
        event_id: str,
        tenant_id: Optional[str] = None
    ) -> None:
        """Пометить событие как обработанное."""
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        now = datetime.now().isoformat()
        
        cur.execute("""
            UPDATE billing_webhook_events
            SET status = 'processed',
                processed_at = ?,
                tenant_id = COALESCE(?, tenant_id)
            WHERE event_id = ? AND provider = ?
        """, (now, tenant_id, event_id, provider))
        
        conn.commit()
        conn.close()
    
    def mark_event_ignored(
        self,
        provider: str,
        event_id: str,
        reason: Optional[str] = None
    ) -> None:
        """Пометить событие как проигнорированное."""
        # Обновляем через event_id напрямую
        conn = self.entitlement_service._get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE billing_webhook_events
            SET status = 'ignored',
                error_message = ?
            WHERE event_id = ? AND provider = ?
        """, (reason, event_id, provider))
        
        conn.commit()
        conn.close()
