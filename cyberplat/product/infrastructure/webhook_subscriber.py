"""Webhook subscriber для создания deliveries при эмиссии событий."""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def create_webhook_subscriber(webhook_repo, webhook_delivery_repo):
    """
    Создать subscriber для создания webhook deliveries.
    
    Args:
        webhook_repo: WebhookRepository
        webhook_delivery_repo: WebhookDeliveryRepository
        
    Returns:
        Subscriber function для EventService
    """
    
    # События, которые должны создавать webhook deliveries
    # Примечание: invoice.ready может быть эмитировано как payment.ready (из payment_agent)
    # Для совместимости поддерживаем оба варианта
    WEBHOOK_EVENTS = {
        "invoice.ready",  # Invoice готов (может быть эмитировано как payment.ready)
        "payment.ready",  # Payment готов (эмитится payment_agent) - маппим на invoice.ready
        "invoice.failed",  # Invoice не удалось создать
        "payment.invalid",  # Payment invalid (эмитится payment_agent) - маппим на invoice.failed
        "email.ocr.completed",  # Эмитится process_email_ocr_jobs_use_case
        "email.ocr.dead",  # Эмитится process_email_ocr_jobs_use_case
        "invoice.confirmed",  # Эмитится при confirm invoice
        "billing.quota.exceeded",  # Paywall: квота превышена
        "billing.trial.expired",  # Trial истёк
        "billing.plan.upgraded",  # План обновлён
        "billing.payment.failed",  # Платеж не прошел/отменен
        "billing.subscription.renewed",
        "billing.subscription.past_due",
        "billing.subscription.canceled",
    }
    
    def webhook_subscriber(
        event_id: str,
        event_type: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]],
        created_at: str
    ):
        """
        Subscriber для создания webhook deliveries.
        
        Вызывается EventService при эмиссии события.
        """
        # Маппим события для совместимости
        # payment.ready → invoice.ready (для webhooks)
        # payment.invalid → invoice.failed (для webhooks)
        # billing.quota.exceeded → quota.exceeded (для webhooks)
        # billing.trial.expired → trial.expired (для webhooks)
        # billing.plan.upgraded → plan.upgraded (для webhooks)
        # billing.payment.failed → payment.failed (для webhooks)
        # billing.subscription.renewed → subscription.renewed
        # billing.subscription.past_due → subscription.past_due
        # billing.subscription.canceled → subscription.canceled
        event_type_mapped = event_type
        if event_type == "payment.ready":
            event_type_mapped = "invoice.ready"
        elif event_type == "payment.invalid":
            event_type_mapped = "invoice.failed"
        elif event_type == "billing.quota.exceeded":
            event_type_mapped = "quota.exceeded"
        elif event_type == "billing.trial.expired":
            event_type_mapped = "trial.expired"
        elif event_type == "billing.plan.upgraded":
            event_type_mapped = "plan.upgraded"
        elif event_type == "billing.payment.failed":
            event_type_mapped = "payment.failed"
        elif event_type == "billing.subscription.renewed":
            event_type_mapped = "subscription.renewed"
        elif event_type == "billing.subscription.past_due":
            event_type_mapped = "subscription.past_due"
        elif event_type == "billing.subscription.canceled":
            event_type_mapped = "subscription.canceled"
        
        # Проверяем, нужно ли создавать delivery для этого события
        if event_type not in WEBHOOK_EVENTS:
            return
        
        try:
            # Получаем активные webhooks для tenant, подписанные на маппированное событие
            webhooks = webhook_repo.list_active_webhooks(
                tenant_id=tenant_id,
                event_type=event_type_mapped
            )
            
            if not webhooks:
                logger.debug(f"No active webhooks for event {event_type} (tenant={tenant_id})")
                return
            
            # Формируем безопасный payload (без raw PDF/OCR JSON)
            safe_payload = _create_safe_payload(
                event_type=event_type_mapped,  # Используем маппированное событие
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                original_payload=payload,
                created_at=created_at
            )
            
            # Создаём delivery для каждого webhook
            for webhook in webhooks:
                try:
                    delivery_id = webhook_delivery_repo.create_delivery(
                        webhook_id=webhook["id"],
                        event_type=event_type_mapped,  # Используем маппированное событие
                        payload=safe_payload,
                        max_retries=5
                    )
                    logger.info(
                        f"Created webhook delivery: {delivery_id} "
                        f"(webhook={webhook['id']}, event={event_type}, tenant={tenant_id})"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to create webhook delivery for webhook {webhook['id']}: {e}",
                        exc_info=True
                    )
                    # Продолжаем для других webhooks
                    
        except Exception as e:
            logger.error(
                f"Error in webhook subscriber for event {event_type}: {e}",
                exc_info=True
            )
            # Не падаем, чтобы не сломать основной поток
    
    return webhook_subscriber


def _create_safe_payload(
    event_type: str,
    tenant_id: str,
    artifact_id: Optional[str],
    original_payload: Optional[Dict[str, Any]],
    created_at: str
) -> Dict[str, Any]:
    """
    Создать безопасный payload для webhook (без raw PDF/OCR JSON).
    
    Включает только:
    - event_type
    - tenant_id
    - artifact_id (если есть)
    - timestamps
    - статусы и IDs
    - НЕ включает: raw OCR JSON, PDF content, base64
    """
    safe_payload = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "timestamp": created_at
    }
    
    if artifact_id:
        safe_payload["artifact_id"] = artifact_id
    
    # Добавляем безопасные поля из original_payload
    if original_payload:
        # Разрешаем только безопасные поля
        safe_fields = {
            "job_id",
            "document_artifact_id",
            "invoice_artifact_id",
            "attempts",
            "status",
            "error",  # Короткое сообщение об ошибке (без контента)
            "next_run_at",
            "reason",
            "plan_id",
            "provider",
            "expires_at",
            "previous_expires_at",
            "failed_charges",
        }
        
        for key, value in original_payload.items():
            if key in safe_fields:
                safe_payload[key] = value
    
    return safe_payload
