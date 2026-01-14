"""Kaspi-specific webhook event handlers (application logic extracted from webhook handler)."""

import logging
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import SubscriptionRepository

logger = logging.getLogger(__name__)


def handle_kaspi_payment_paid(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Kaspi payment.paid событие.
    
    Args:
        event: Kaspi event object (contains order_id, kaspi_token, etc)
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    
    Note: This handler expects the order to be fetched and passed in event metadata,
    as Kaspi events reference orders by ID.
    """
    # Извлекаем order из metadata (должен быть установлен до вызова handler)
    order = event.get("_order")
    
    if not order:
        logger.error("Kaspi payment.paid handler requires order in event metadata")
        return False, None, "Missing order in event metadata"
    
    order_id = order["id"]
    tenant_id = order["tenant_id"]
    plan_id = order["plan_id"]
    external_order_id = order.get("external_order_id")
    
    # Проверяем, не обработан ли уже заказ
    if order["status"] == "paid":
        logger.info(f"Order {order_id} already processed (status=paid), ignoring")
        return True, tenant_id, None
    
    # Сохраняем payment token если есть (для recurring subscriptions)
    payment_token = (
        event.get("kaspi_token") or 
        event.get("payment_token") or 
        event.get("token")
    )
    
    if payment_token:
        try:
            # Используем BillingService для сохранения Kaspi токена (если доступен через subscription_repo)
            billing_service = None
            if hasattr(subscription_repo, 'entitlement_service'):
                billing_service = getattr(subscription_repo.entitlement_service, '_billing_service', None)
            
            if billing_service:
                billing_service.upsert_kaspi_profile(tenant_id, payment_token)
            elif hasattr(subscription_repo, 'entitlement_service'):
                # Fallback: используем EntitlementService
                subscription_repo.entitlement_service.upsert_payment_profile(
                    tenant_id=tenant_id,
                    kaspi_token=payment_token
                )
        except Exception as e:
            logger.error(f"Ошибка при сохранении payment token: {e}", exc_info=True)
    
    # Обновляем статус заказа
    if hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.update_order_status(
                order_id=order_id,
                status="paid",
                external_order_id=external_order_id
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заказа: {e}", exc_info=True)
    
    # Применяем план к tenant
    now = datetime.now()
    period_start = now.isoformat()
    period_end = (now + timedelta(days=30)).isoformat()  # Месячный период
    
    try:
        subscription_repo.apply_plan(
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


def handle_kaspi_payment_failed(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Kaspi payment.failed событие.
    
    Args:
        event: Kaspi event object
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    """
    order = event.get("_order")
    
    if not order:
        logger.error("Kaspi payment.failed handler requires order in event metadata")
        return False, None, "Missing order in event metadata"
    
    order_id = order["id"]
    tenant_id = order["tenant_id"]
    
    # Обновляем статус заказа
    if hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.update_order_status(
                order_id=order_id,
                status="failed"
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заказа: {e}", exc_info=True)
    
    logger.info(f"Payment failed for order {order_id}, tenant {tenant_id}")
    return True, tenant_id, None


def handle_kaspi_payment_canceled(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Kaspi payment.canceled событие.
    
    Args:
        event: Kaspi event object
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    """
    order = event.get("_order")
    
    if not order:
        logger.error("Kaspi payment.canceled handler requires order in event metadata")
        return False, None, "Missing order in event metadata"
    
    order_id = order["id"]
    tenant_id = order["tenant_id"]
    
    # Обновляем статус заказа
    if hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.update_order_status(
                order_id=order_id,
                status="canceled"
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заказа: {e}", exc_info=True)
    
    logger.info(f"Payment canceled for order {order_id}, tenant {tenant_id}")
    return True, tenant_id, None


def create_kaspi_event_handlers(entitlement_service) -> Dict[str, callable]:
    """
    Создать словарь обработчиков Kaspi событий.
    
    Args:
        entitlement_service: EntitlementService for order operations
        
    Returns:
        Dict mapping event_type -> handler function
    """
    def wrap_handler_with_order_lookup(handler_func):
        """Wrapper that looks up order before calling handler."""
        def wrapped(event: Dict[str, Any], subscription_repo: SubscriptionRepository):
            # Извлекаем order_id из события
            order_id_from_event = event.get("order_id") or event.get("external_order_id")
            
            if not order_id_from_event:
                logger.warning("Kaspi event missing order_id")
                return False, None, "Missing order_id in event"
            
            # Ищем заказ по ОБОИМ полям (id ИЛИ external_order_id)
            order = entitlement_service.get_order_by_external_id(order_id_from_event)
            if not order:
                order = entitlement_service.get_order(order_id_from_event)
            
            if not order:
                logger.warning(f"Order not found: {order_id_from_event}")
                return False, None, f"Order not found: {order_id_from_event}"
            
            # Добавляем order в event metadata для handler
            event["_order"] = order
            
            return handler_func(event, subscription_repo)
        
        return wrapped
    
    return {
        "payment.success": wrap_handler_with_order_lookup(handle_kaspi_payment_paid),
        "payment.paid": wrap_handler_with_order_lookup(handle_kaspi_payment_paid),
        "order.paid": wrap_handler_with_order_lookup(handle_kaspi_payment_paid),
        "payment.failed": wrap_handler_with_order_lookup(handle_kaspi_payment_failed),
        "order.failed": wrap_handler_with_order_lookup(handle_kaspi_payment_failed),
        "payment.canceled": wrap_handler_with_order_lookup(handle_kaspi_payment_canceled),
        "order.canceled": wrap_handler_with_order_lookup(handle_kaspi_payment_canceled)
    }
