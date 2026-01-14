"""Domain interfaces (ports) for billing."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime


class PaymentProvider(ABC):
    """Интерфейс для платежных провайдеров (Stripe, Kaspi, etc)."""
    
    @abstractmethod
    def create_checkout_session(
        self,
        amount_minor: int,
        currency: str,
        tenant_id: str,
        plan_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Создать checkout session.
        
        Returns:
            Dict с ключами:
            - checkout_url: URL для редиректа пользователя
            - session_id: ID сессии (для Stripe)
            - external_order_id: Внешний ID заказа (для Kaspi)
        """
        pass
    
    @abstractmethod
    def charge_token(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        tenant_id: str,
        plan_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Списать средства с токена (для recurring subscriptions).
        
        Returns:
            Dict с ключами:
            - external_order_id: ID транзакции у провайдера
            - status: "success" или "failed"
        """
        pass
    
    @abstractmethod
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        secret: str
    ) -> bool:
        """Проверить подпись webhook."""
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Имя провайдера (stripe, kaspi, etc)."""
        pass


class SubscriptionRepository(ABC):
    """Интерфейс для хранения подписок."""
    
    @abstractmethod
    def get_active_subscriptions_for_renewal(
        self,
        provider: str,
        period_end_threshold: str  # ISO format
    ) -> list:
        """
        Получить активные подписки для продления.
        
        Returns:
            List of dicts with: tenant_id, plan_id, subscription_id, 
            current_period_end, price_minor, currency, payment_token
        """
        pass
    
    @abstractmethod
    def get_subscription(
        self,
        tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить подписку по tenant_id."""
        pass
    
    @abstractmethod
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
        pass


class WebhookEventRepository(ABC):
    """Интерфейс для хранения webhook событий (идемпотентность)."""
    
    @abstractmethod
    def record_event_received(
        self,
        provider: str,
        event_id: str,
        raw_json: str,
        tenant_id: Optional[str] = None
    ) -> None:
        """Записать получение события (до обработки)."""
        pass
    
    @abstractmethod
    def is_event_processed(
        self,
        provider: str,
        event_id: str
    ) -> bool:
        """Проверить, обработано ли событие."""
        pass
    
    @abstractmethod
    def mark_event_processed(
        self,
        provider: str,
        event_id: str,
        tenant_id: Optional[str] = None
    ) -> None:
        """Пометить событие как обработанное."""
        pass
