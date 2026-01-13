"""Адаптер для Kaspi API (для возможности мокирования в тестах)."""

import os
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Глобальная переменная для мокирования в тестах
_kaspi_client = None


def get_kaspi_client():
    """Получить Kaspi клиент (реальный или мок)."""
    global _kaspi_client
    
    if _kaspi_client is not None:
        return _kaspi_client
    
    # Проверяем, включен ли Kaspi
    kaspi_enabled = os.getenv("KASPI_ENABLED", "0").strip() == "1"
    if not kaspi_enabled:
        return None
    
    # В реальной реализации здесь будет инициализация Kaspi SDK
    # Пока возвращаем объект-заглушку
    return {
        "enabled": True,
        "api_key": os.getenv("KASPI_API_KEY", "").strip(),
        "merchant_id": os.getenv("KASPI_MERCHANT_ID", "").strip(),
        "webhook_secret": os.getenv("KASPI_WEBHOOK_SECRET", "").strip()
    }


def set_kaspi_client(client):
    """Установить мок Kaspi клиента (для тестов)."""
    global _kaspi_client
    _kaspi_client = client


def create_checkout_session(
    amount_minor: int,
    currency: str,
    tenant_id: str,
    plan_id: str,
    success_url: str,
    cancel_url: str,
    order_id: str
) -> Optional[Dict[str, Any]]:
    """
    Создать Kaspi Checkout Session (hosted checkout).
    
    Args:
        amount_minor: Сумма в minor units (копейки)
        currency: Валюта (KZT, USD, RUB)
        tenant_id: ID тенанта
        plan_id: ID целевого плана
        success_url: URL для редиректа после успешной оплаты
        cancel_url: URL для редиректа при отмене
        order_id: Внутренний ID заказа
        
    Returns:
        Session объект с checkout_url и external_order_id или None
    """
    kaspi = get_kaspi_client()
    if not kaspi:
        return None
    
    try:
        # В реальной реализации здесь будет вызов Kaspi API
        # Пока используем заглушку для демонстрации
        
        # Генерируем external_order_id (в реальности Kaspi вернет его)
        external_order_id = f"kaspi_{order_id}"
        
        # В реальности здесь будет HTTP запрос к Kaspi API:
        # response = requests.post(
        #     f"https://api.kaspi.kz/v1/checkout/sessions",
        #     headers={"Authorization": f"Bearer {kaspi['api_key']}"},
        #     json={
        #         "amount": amount_minor,
        #         "currency": currency,
        #         "merchant_id": kaspi["merchant_id"],
        #         "success_url": success_url,
        #         "cancel_url": cancel_url,
        #         "metadata": {
        #             "tenant_id": tenant_id,
        #             "plan_id": plan_id,
        #             "order_id": order_id
        #         }
        #     }
        # )
        # checkout_url = response.json()["checkout_url"]
        # external_order_id = response.json()["order_id"]
        
        # Заглушка для демонстрации
        base_url = os.getenv("KASPI_CHECKOUT_BASE_URL", "https://checkout.kaspi.kz")
        checkout_url = f"{base_url}/pay/{external_order_id}"
        
        return {
            "checkout_url": checkout_url,
            "external_order_id": external_order_id
        }
    except Exception as e:
        logger.error(f"Ошибка при создании Kaspi Checkout Session: {e}", exc_info=True)
        return None


def charge_token(
    token: str,
    amount_minor: int,
    currency: str,
    tenant_id: str,
    plan_id: str
) -> Optional[Dict[str, Any]]:
    """
    Списать средства с Kaspi токена (для recurring subscriptions).
    
    Args:
        token: Kaspi payment token
        amount_minor: Сумма в minor units
        currency: Валюта
        tenant_id: ID тенанта
        plan_id: ID плана
        
    Returns:
        Результат списания с external_order_id или None при ошибке
    """
    kaspi = get_kaspi_client()
    if not kaspi:
        return None
    
    try:
        # В реальной реализации здесь будет вызов Kaspi API для списания
        # Пока используем заглушку
        
        # В реальности:
        # response = requests.post(
        #     f"https://api.kaspi.kz/v1/payments/charge",
        #     headers={"Authorization": f"Bearer {kaspi['api_key']}"},
        #     json={
        #         "token": token,
        #         "amount": amount_minor,
        #         "currency": currency,
        #         "metadata": {
        #             "tenant_id": tenant_id,
        #             "plan_id": plan_id
        #         }
        #     }
        # )
        # external_order_id = response.json()["order_id"]
        # status = response.json()["status"]  # "success" or "failed"
        
        # Заглушка для демонстрации
        external_order_id = f"kaspi_charge_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        return {
            "external_order_id": external_order_id,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Ошибка при списании с Kaspi токена: {e}", exc_info=True)
        return None


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str
) -> bool:
    """
    Проверить подпись Kaspi webhook.
    
    Args:
        payload: Тело запроса (bytes)
        signature: Значение заголовка X-Kaspi-Signature
        secret: Секрет для проверки подписи
        
    Returns:
        True если подпись валидна
    """
    if not secret:
        logger.warning("Kaspi webhook secret не установлен, пропускаем проверку подписи")
        return False
    
    try:
        # В реальной реализации здесь будет проверка подписи по спецификации Kaspi
        # Обычно это HMAC-SHA256
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)
    except Exception as e:
        logger.error(f"Ошибка при проверке Kaspi signature: {e}", exc_info=True)
        return False
