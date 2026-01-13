"""Обработчик Stripe webhook событий."""

import json
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class StripeWebhookHandler:
    """Обработчик Stripe webhook событий."""
    
    def __init__(
        self,
        entitlement_service,
        webhook_secret: Optional[str] = None
    ):
        """
        Инициализировать обработчик.
        
        Args:
            entitlement_service: EntitlementService для применения планов
            webhook_secret: Секрет для проверки подписи Stripe
        """
        self.entitlement_service = entitlement_service
        self.webhook_secret = webhook_secret
    
    def verify_signature(
        self,
        payload: bytes,
        signature: str
    ) -> bool:
        """
        Проверить подпись Stripe webhook.
        
        Args:
            payload: Тело запроса (bytes)
            signature: Значение заголовка Stripe-Signature
            
        Returns:
            True если подпись валидна
        """
        if not self.webhook_secret:
            logger.warning("Stripe webhook secret не установлен, пропускаем проверку подписи")
            return False
        
        try:
            # Stripe signature format: timestamp,signature1 signature2 ...
            # Мы проверяем только первую подпись
            sig_parts = signature.split(",")
            timestamp = None
            signatures = []
            
            for part in sig_parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    if key == "t":
                        timestamp = value
                    elif key == "v1":
                        signatures.append(value)
            
            if not timestamp or not signatures:
                logger.error("Некорректный формат Stripe signature")
                return False
            
            # Создаем ожидаемую подпись
            signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
            expected_signature = hmac.new(
                self.webhook_secret.encode('utf-8'),
                signed_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Сравниваем с полученными подписями
            for sig in signatures:
                if hmac.compare_digest(expected_signature, sig):
                    return True
            
            logger.error("Stripe signature не совпадает")
            return False
            
        except Exception as e:
            logger.error(f"Ошибка при проверке Stripe signature: {e}", exc_info=True)
            return False
    
    def handle_event(self, event: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Обработать Stripe событие.
        
        Args:
            event: Stripe event объект
            
        Returns:
            (success, tenant_id, error_message)
        """
        event_type = event.get("type")
        event_id = event.get("id")
        event_data = event.get("data", {})
        event_object = event_data.get("object", {})
        
        logger.info(f"Обработка Stripe события: {event_type} (id={event_id})")
        
        # КРИТИЧНО: Проверяем идемпотентность ДО обработки
        # Это гарантирует, что даже если обработка упадет, повторный вызов не создаст дубликаты
        if event_id:
            # Проверяем, не обработано ли уже событие
            conn_check = self.entitlement_service._get_connection()
            cur_check = conn_check.cursor()
            cur_check.execute("""
                SELECT status, tenant_id
                FROM billing_webhook_events
                WHERE event_id = ? AND provider = 'stripe'
            """, (event_id,))
            existing = cur_check.fetchone()
            conn_check.close()
            
            if existing and existing["status"] == "processed":
                logger.info(f"Stripe событие {event_id} уже обработано, возвращаем идемпотентный результат")
                return True, existing["tenant_id"], None
        
        # Извлекаем tenant_id и plan_id из metadata
        metadata = event_object.get("metadata", {})
        tenant_id = metadata.get("tenant_id")
        plan_id = metadata.get("plan_id")
        
        if not tenant_id:
            logger.warning(f"Stripe событие {event_id} не содержит tenant_id в metadata, игнорируем")
            return False, None, "Missing tenant_id in metadata"
        
        if not plan_id:
            logger.warning(f"Stripe событие {event_id} не содержит plan_id в metadata, игнорируем")
            return False, None, "Missing plan_id in metadata"
        
        # КРИТИЧНО: Сохраняем event_id в idempotency ledger ДО обработки
        # Это гарантирует идемпотентность даже если обработка упадет
        if event_id:
            try:
                # Записываем событие как "received" (будет обновлено на "processed" после успешной обработки)
                self.entitlement_service.record_webhook_event(
                    provider="stripe",
                    event_id=event_id,
                    raw_json=json.dumps(event),
                    tenant_id=tenant_id
                )
            except Exception as e:
                logger.warning(f"Не удалось записать event_id для идемпотентности: {e}, продолжаем обработку")
        
        # Обрабатываем разные типы событий
        if event_type == "checkout.session.completed":
            return self._handle_checkout_completed(event_object, tenant_id, plan_id)
        elif event_type == "invoice.paid":
            return self._handle_invoice_paid(event_object, tenant_id, plan_id)
        elif event_type in ["customer.subscription.updated", "customer.subscription.deleted"]:
            return self._handle_subscription_updated(event, tenant_id, plan_id)
        else:
            logger.debug(f"Событие {event_type} не обрабатывается, игнорируем")
            return False, None, f"Event type {event_type} not handled"
    
    def _handle_checkout_completed(
        self,
        session: Dict[str, Any],
        tenant_id: str,
        plan_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать checkout.session.completed."""
        # Сохраняем customer_id если есть
        customer_id = session.get("customer")
        if customer_id:
            try:
                self.entitlement_service.upsert_payment_profile(
                    tenant_id=tenant_id,
                    stripe_customer_id=customer_id
                )
            except Exception as e:
                logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
        
        # Для checkout.session.completed используем subscription если есть
        subscription_id = session.get("subscription")
        
        if subscription_id:
            # Если есть subscription, ждем invoice.paid или subscription.updated
            logger.info(f"Checkout completed для tenant {tenant_id}, subscription={subscription_id}, ждем invoice.paid")
            return True, tenant_id, None
        
        # Если нет subscription (one-time payment), применяем план сразу
        # Используем текущий период
        now = datetime.now()
        period_start = now.isoformat()
        # Предполагаем месячный период (простое добавление 30 дней)
        from datetime import timedelta
        period_end = (now + timedelta(days=30)).isoformat()
        
        try:
            self.entitlement_service.apply_plan_to_tenant(
                tenant_id=tenant_id,
                plan_id=plan_id,
                period_start=period_start,
                period_end=period_end,
                provider="stripe",
                provider_customer_id=customer_id,
                status="active"
            )
            return True, tenant_id, None
        except Exception as e:
            logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
            return False, tenant_id, str(e)
    
    def _handle_invoice_paid(
        self,
        invoice: Dict[str, Any],
        tenant_id: str,
        plan_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать invoice.paid."""
        subscription_id = invoice.get("subscription")
        customer_id = invoice.get("customer")
        period_start = invoice.get("period_start")
        period_end = invoice.get("period_end")
        
        # Сохраняем customer_id если есть
        if customer_id:
            try:
                self.entitlement_service.upsert_payment_profile(
                    tenant_id=tenant_id,
                    stripe_customer_id=customer_id
                )
            except Exception as e:
                logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
        
        # Конвертируем Unix timestamp в ISO string
        if period_start:
            period_start = datetime.fromtimestamp(period_start).isoformat()
        else:
            period_start = datetime.now().isoformat()
        
        if period_end:
            period_end = datetime.fromtimestamp(period_end).isoformat()
        else:
            from datetime import timedelta
            period_end = (datetime.now() + timedelta(days=30)).isoformat()
        
        try:
            self.entitlement_service.apply_plan_to_tenant(
                tenant_id=tenant_id,
                plan_id=plan_id,
                period_start=period_start,
                period_end=period_end,
                provider="stripe",
                provider_customer_id=customer_id,
                provider_subscription_id=subscription_id,
                status="active"
            )
            return True, tenant_id, None
        except Exception as e:
            logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
            return False, tenant_id, str(e)
    
    def _handle_subscription_updated(
        self,
        event: Dict[str, Any],
        tenant_id: str,
        plan_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать customer.subscription.updated или deleted."""
        subscription = event.get("data", {}).get("object", {})
        status = subscription.get("status")
        subscription_id = subscription.get("id")
        customer_id = subscription.get("customer")
        
        # Сохраняем customer_id если есть
        if customer_id:
            try:
                self.entitlement_service.upsert_payment_profile(
                    tenant_id=tenant_id,
                    stripe_customer_id=customer_id
                )
            except Exception as e:
                logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
        
        current_period_start = subscription.get("current_period_start")
        current_period_end = subscription.get("current_period_end")
        
        # Конвертируем Unix timestamp в ISO string
        if current_period_start:
            period_start = datetime.fromtimestamp(current_period_start).isoformat()
        else:
            period_start = datetime.now().isoformat()
        
        if current_period_end:
            period_end = datetime.fromtimestamp(current_period_end).isoformat()
        else:
            from datetime import timedelta
            period_end = (datetime.now() + timedelta(days=30)).isoformat()
        
        # Если subscription canceled или past_due - откатываем на free plan
        if status in ["canceled", "past_due", "unpaid"]:
            plan_id = "plan_free"
            logger.info(f"Subscription {subscription_id} имеет статус {status}, откатываем на free plan")
        
        try:
            self.entitlement_service.apply_plan_to_tenant(
                tenant_id=tenant_id,
                plan_id=plan_id,
                period_start=period_start,
                period_end=period_end,
                provider="stripe",
                provider_customer_id=customer_id,
                provider_subscription_id=subscription_id,
                status=status
            )
            return True, tenant_id, None
        except Exception as e:
            logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
            return False, tenant_id, str(e)
