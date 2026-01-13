"""Обработчик Kaspi webhook событий."""

import json
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class KaspiWebhookHandler:
    """Обработчик Kaspi webhook событий."""
    
    def __init__(
        self,
        entitlement_service,
        billing_service=None,
        webhook_secret: Optional[str] = None
    ):
        """
        Инициализировать обработчик.
        
        Args:
            entitlement_service: EntitlementService для применения планов
            billing_service: BillingService для сохранения Kaspi токенов (опционально)
            webhook_secret: Секрет для проверки подписи Kaspi
        """
        self.entitlement_service = entitlement_service
        self.billing_service = billing_service
        self.webhook_secret = webhook_secret
    
    def verify_signature(
        self,
        payload: bytes,
        signature: str
    ) -> bool:
        """
        Проверить подпись Kaspi webhook.
        
        Args:
            payload: Тело запроса (bytes)
            signature: Значение заголовка X-Kaspi-Signature
            
        Returns:
            True если подпись валидна
        """
        if not self.webhook_secret:
            logger.warning("Kaspi webhook secret не установлен, пропускаем проверку подписи")
            return False
        
        from cyberplat.kaspi_client import verify_webhook_signature
        return verify_webhook_signature(payload, signature, self.webhook_secret)
    
    def handle_event(self, event: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Обработать Kaspi событие.
        
        Args:
            event: Kaspi event объект
            
        Returns:
            (success, tenant_id, error_message)
        """
        event_type = event.get("type") or event.get("event_type")
        event_id = event.get("id") or event.get("event_id")
        # Kaspi может присылать либо external_order_id (свой ID), либо order_id (наш внутренний ID)
        order_id_from_event = event.get("order_id") or event.get("external_order_id")
        
        logger.info(f"Обработка Kaspi события: {event_type} (id={event_id}, order_id={order_id_from_event})")
        
        if not order_id_from_event:
            logger.warning(f"Kaspi событие {event_id} не содержит order_id, игнорируем")
            return False, None, "Missing order_id in event"
        
        # КРИТИЧНО: Ищем заказ по ОБОИМ полям (id ИЛИ external_order_id)
        # Kaspi может присылать либо наш внутренний order_id, либо свой external_order_id
        order = None
        
        # Сначала пробуем найти по external_order_id (если Kaspi присылает свой ID)
        order = self.entitlement_service.get_order_by_external_id(order_id_from_event)
        
        # Если не найден, пробуем найти по внутреннему id (если Kaspi присылает наш order_id)
        if not order:
            order = self.entitlement_service.get_order(order_id_from_event)
        
        if not order:
            logger.warning(
                f"Заказ не найден ни по external_order_id, ни по id: {order_id_from_event}. "
                f"Проверьте, что заказ был создан в checkout и что DB path одинаковый."
            )
            return False, None, f"Order not found: {order_id_from_event}"
        
        tenant_id = order["tenant_id"]
        plan_id = order["plan_id"]
        
        # Проверяем, не обработан ли уже заказ (идемпотентность)
        if order["status"] == "paid":
            logger.info(f"Заказ {order['id']} уже обработан (status=paid), игнорируем")
            return True, tenant_id, None
        
        # Обрабатываем разные типы событий
        if event_type in ["payment.success", "payment.paid", "order.paid"]:
            return self._handle_payment_paid(order, event)
        elif event_type in ["payment.failed", "order.failed"]:
            return self._handle_payment_failed(order, event)
        elif event_type in ["payment.canceled", "order.canceled"]:
            return self._handle_payment_canceled(order, event)
        else:
            logger.debug(f"Событие {event_type} не обрабатывается, игнорируем")
            return False, None, f"Event type {event_type} not handled"
    
    def _handle_payment_paid(
        self,
        order: Dict[str, Any],
        event: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать payment.paid."""
        order_id = order["id"]
        tenant_id = order["tenant_id"]
        plan_id = order["plan_id"]
        external_order_id = order["external_order_id"]
        
        # Сохраняем payment token если есть (для recurring subscriptions)
        # Kaspi может присылать токен в разных полях: kaspi_token, payment_token, token
        payment_token = event.get("kaspi_token") or event.get("payment_token") or event.get("token")
        if payment_token:
            try:
                # Используем BillingService для сохранения Kaspi токена (отдельная таблица)
                if self.billing_service:
                    self.billing_service.upsert_kaspi_profile(tenant_id, payment_token)
                else:
                    # Fallback: используем EntitlementService (legacy, для обратной совместимости)
                    logger.warning(f"BillingService не передан, используем EntitlementService для сохранения Kaspi токена")
                    self.entitlement_service.upsert_payment_profile(
                        tenant_id=tenant_id,
                        kaspi_token=payment_token
                    )
            except Exception as e:
                logger.error(f"Ошибка при сохранении payment token: {e}", exc_info=True)
        
        # Обновляем статус заказа
        self.entitlement_service.update_order_status(
            order_id=order_id,
            status="paid",
            external_order_id=external_order_id
        )
        
        # Применяем план к tenant
        now = datetime.now()
        period_start = now.isoformat()
        period_end = (now + timedelta(days=30)).isoformat()  # Месячный период
        
        try:
            self.entitlement_service.apply_plan_to_tenant(
                tenant_id=tenant_id,
                plan_id=plan_id,
                period_start=period_start,
                period_end=period_end,
                provider="kaspi",
                provider_customer_id=None,  # Kaspi может не иметь customer_id
                provider_subscription_id=external_order_id,
                status="active"
            )
            return True, tenant_id, None
        except Exception as e:
            logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
            return False, tenant_id, str(e)
    
    def _handle_payment_failed(
        self,
        order: Dict[str, Any],
        event: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать payment.failed."""
        order_id = order["id"]
        
        # Обновляем статус заказа
        self.entitlement_service.update_order_status(
            order_id=order_id,
            status="failed"
        )
        
        logger.info(f"Платеж по заказу {order_id} не прошел")
        return True, order["tenant_id"], None
    
    def _handle_payment_canceled(
        self,
        order: Dict[str, Any],
        event: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Обработать payment.canceled."""
        order_id = order["id"]
        
        # Обновляем статус заказа
        self.entitlement_service.update_order_status(
            order_id=order_id,
            status="canceled"
        )
        
        logger.info(f"Платеж по заказу {order_id} отменен")
        return True, order["tenant_id"], None
