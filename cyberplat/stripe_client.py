"""Адаптер для Stripe SDK (для возможности мокирования в тестах)."""

import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Глобальная переменная для мокирования в тестах
_stripe_client = None


def get_stripe_client():
    """Получить Stripe клиент (реальный или мок)."""
    global _stripe_client
    
    if _stripe_client is not None:
        return _stripe_client
    
    # Проверяем, включен ли Stripe
    stripe_enabled = os.getenv("STRIPE_ENABLED", "0").strip() == "1"
    if not stripe_enabled:
        return None
    
    try:
        import stripe
        stripe_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not stripe_key:
            logger.warning("STRIPE_ENABLED=1, но STRIPE_SECRET_KEY не установлен")
            return None
        
        stripe.api_key = stripe_key
        return stripe
    except ImportError:
        logger.warning("stripe SDK не установлен, установите: pip install stripe")
        return None


def set_stripe_client(client):
    """Установить мок Stripe клиента (для тестов)."""
    global _stripe_client
    _stripe_client = client


def create_checkout_session(
    price_id: str,
    tenant_id: str,
    target_plan_id: str,
    success_url: str,
    cancel_url: str
) -> Optional[Dict[str, Any]]:
    """
    Создать Stripe Checkout Session.
    
    Args:
        price_id: Stripe Price ID
        tenant_id: ID тенанта
        target_plan_id: ID целевого плана
        success_url: URL для редиректа после успешной оплаты
        cancel_url: URL для редиректа при отмене
        
    Returns:
        Session объект или None
    """
    stripe = get_stripe_client()
    if not stripe:
        return None
    
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            metadata={
                "tenant_id": tenant_id,
                "target_plan_id": target_plan_id
            },
            subscription_data={
                "metadata": {
                    "tenant_id": tenant_id,
                    "target_plan_id": target_plan_id
                }
            },
            success_url=success_url,
            cancel_url=cancel_url
        )
        
        return {
            "id": session.id,
            "url": session.url
        }
    except Exception as e:
        logger.error(f"Ошибка при создании Stripe Checkout Session: {e}", exc_info=True)
        return None


def create_portal_session(
    customer_id: str,
    return_url: str
) -> Optional[Dict[str, Any]]:
    """
    Создать Stripe Customer Portal Session.
    
    Args:
        customer_id: Stripe Customer ID
        return_url: URL для редиректа после работы с portal
        
    Returns:
        Session объект или None
    """
    stripe = get_stripe_client()
    if not stripe:
        return None
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        
        return {
            "id": session.id,
            "url": session.url
        }
    except Exception as e:
        logger.error(f"Ошибка при создании Stripe Portal Session: {e}", exc_info=True)
        return None


def get_price_id_for_plan(plan_id: str) -> Optional[str]:
    """
    Получить Stripe Price ID для плана.
    
    Args:
        plan_id: ID плана (plan_pro, plan_enterprise)
        
    Returns:
        Stripe Price ID или None
    """
    # Маппинг планов на Stripe Price IDs (из ENV или конфига)
    price_mapping = {
        "plan_pro": os.getenv("STRIPE_PRICE_ID_PRO", "").strip(),
        "plan_enterprise": os.getenv("STRIPE_PRICE_ID_ENTERPRISE", "").strip()
    }
    
    return price_mapping.get(plan_id)
