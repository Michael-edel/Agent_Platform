"""Use case: Process payment provider webhook event."""

import logging
import json
from typing import Optional, Tuple, Dict, Any, Callable

from cyberplat.billing.domain.interfaces import (
    PaymentProvider,
    WebhookEventRepository,
    SubscriptionRepository
)

logger = logging.getLogger(__name__)


class ProcessWebhookUseCase:
    """Use case для обработки webhook событий от платежных провайдеров."""
    
    def __init__(
        self,
        provider: PaymentProvider,
        event_repo: WebhookEventRepository,
        subscription_repo: SubscriptionRepository,
        event_handlers: Dict[str, Callable]  # event_type -> handler function
    ):
        self.provider = provider
        self.event_repo = event_repo
        self.subscription_repo = subscription_repo
        self.event_handlers = event_handlers
    
    def execute(
        self,
        event: Dict[str, Any],
        raw_payload: bytes,
        signature: str,
        webhook_secret: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Обработать webhook событие.
        
        Args:
            event: Парсированное событие от провайдера
            raw_payload: Сырой payload (для проверки подписи)
            signature: Подпись webhook
            webhook_secret: Секрет для проверки подписи
            
        Returns:
            (success, tenant_id, error_message)
        """
        # Проверяем подпись
        if not self.provider.verify_webhook_signature(
            raw_payload, signature, webhook_secret
        ):
            logger.warning(f"Invalid webhook signature from {self.provider.provider_name}")
            return False, None, "Invalid signature"
        
        # Извлекаем event_id и event_type
        event_id = event.get("id") or event.get("event_id")
        event_type = event.get("type") or event.get("event_type")
        
        if not event_id:
            logger.warning(f"Event missing ID from {self.provider.provider_name}")
            return False, None, "Missing event ID"
        
        if not event_type:
            logger.warning(f"Event missing type from {self.provider.provider_name}")
            return False, None, "Missing event type"
        
        # Проверяем идемпотентность
        if self.event_repo.is_event_processed(
            self.provider.provider_name,
            event_id
        ):
            logger.info(
                f"Event {event_id} ({event_type}) already processed, "
                f"skipping (idempotent response)"
            )
            # Возвращаем tenant_id из уже обработанного события если возможно
            tenant_id = None
            if hasattr(self.event_repo, 'get_event_tenant_id'):
                tenant_id = self.event_repo.get_event_tenant_id(
                    self.provider.provider_name,
                    event_id
                )
            return True, tenant_id, None
        
        # Записываем событие как полученное (до обработки)
        try:
            self.event_repo.record_event_received(
                provider=self.provider.provider_name,
                event_id=event_id,
                raw_json=json.dumps(event),
                tenant_id=None  # Будет установлен после обработки
            )
        except Exception as e:
            logger.warning(f"Failed to record event: {e}, continuing...")
        
        # Находим обработчик для типа события
        handler = self.event_handlers.get(event_type)
        if not handler:
            logger.debug(f"Event type {event_type} not handled, ignoring")
            return False, None, f"Event type {event_type} not handled"
        
        # Обрабатываем событие
        try:
            success, tenant_id, error = handler(event, self.subscription_repo)
            
            # Помечаем событие как обработанное
            if success and event_id:
                self.event_repo.mark_event_processed(
                    provider=self.provider.provider_name,
                    event_id=event_id,
                    tenant_id=tenant_id
                )
            
            return success, tenant_id, error
            
        except Exception as e:
            logger.error(f"Error processing event {event_id}: {e}", exc_info=True)
            return False, None, str(e)
