"""Use case: Dispatch webhook deliveries (safe retries with HMAC signature)."""

import logging
import os
import hmac
import hashlib
import json
import random
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.security.outbound import OutboundDestinationError, validate_public_webhook_url

logger = logging.getLogger(__name__)


class DispatchWebhooksUseCase:
    """Use case для отправки webhook deliveries с retries и HMAC подписью."""
    
    def __init__(
        self,
        webhook_repo,  # WebhookRepository
        webhook_delivery_repo,  # WebhookDeliveryRepository
        event_service,  # EventService
        max_retries: int = 5,
        retry_base_seconds: int = 10,
        retry_max_seconds: int = 600,
        http_timeout_seconds: int = 30
    ):
        self.webhook_repo = webhook_repo
        self.webhook_delivery_repo = webhook_delivery_repo
        self.event_service = event_service
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.http_timeout_seconds = http_timeout_seconds
    
    def execute(
        self,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Отправить webhook deliveries.
        
        Args:
            limit: Максимальное количество deliveries для обработки за один вызов
            
        Returns:
            {
                "processed": int,
                "sent": int,
                "failed": int,
                "dead": int,
                "errors": List[str]
            }
        """
        # Получаем deliveries готовые к отправке
        deliveries = self.webhook_delivery_repo.get_pending_deliveries(limit=limit)
        
        if not deliveries:
            logger.debug("No webhook deliveries ready for dispatch")
            return {
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "dead": 0,
                "errors": []
            }
        
        logger.info(f"Dispatching {len(deliveries)} webhook deliveries")
        
        processed = 0
        sent = 0
        failed = 0
        dead = 0
        errors = []
        
        for delivery in deliveries:
            try:
                result = self._send_delivery(delivery)
                
                if result["status"] == "sent":
                    sent += 1
                elif result["status"] == "failed":
                    failed += 1
                elif result["status"] == "dead":
                    dead += 1
                elif result["status"] == "skipped":
                    # Delivery уже обрабатывается или sent
                    continue
                
                processed += 1
                
                if result.get("error"):
                    errors.append(f"Delivery {delivery['id']}: {result['error']}")
                    
            except Exception as e:
                error_msg = f"Unexpected error dispatching delivery {delivery.get('id', 'unknown')}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)
                failed += 1
        
        logger.info(
            f"Webhook deliveries dispatch completed: "
            f"processed={processed}, sent={sent}, failed={failed}, dead={dead}"
        )
        
        return {
            "processed": processed,
            "sent": sent,
            "failed": failed,
            "dead": dead,
            "errors": errors
        }
    
    def _send_delivery(
        self,
        delivery: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Отправить один delivery.
        
        Returns:
            {
                "status": "sent" | "failed" | "dead" | "skipped",
                "error": str | None
            }
        """
        delivery_id = delivery["id"]
        webhook_id = delivery["webhook_id"]
        event_type = delivery["event_type"]
        payload = delivery["payload"]
        attempts = delivery["attempts"]
        max_retries = delivery.get("max_retries", self.max_retries)
        
        # Получаем webhook для URL и secret
        # Для получения tenant_id нужно найти webhook
        # Упростим: получим webhook через delivery.webhook_id
        # Но в delivery нет tenant_id, поэтому нужно получить webhook
        
        # Получаем webhook (нужно найти tenant_id, но для этого нужен webhook)
        # Для MVP: получаем webhook через raw query или добавляем tenant_id в delivery
        # Пока используем простой подход: получаем webhook по ID
        
        # Получаем webhook
        webhook = self.webhook_repo.get_webhook_by_id(webhook_id)
        
        if not webhook:
            error_message = f"Webhook {webhook_id} not found"
            logger.error(error_message)
            return {"status": "dead", "error": error_message}
        
        if not webhook.get("active"):
            logger.debug(f"Webhook {webhook_id} is not active, skipping delivery")
            return {"status": "skipped", "error": None}
        
        url = webhook["url"]
        try:
            validate_public_webhook_url(url)
        except OutboundDestinationError as exc:
            error_message = f"Unsafe webhook URL: {exc}"
            self.webhook_delivery_repo.mark_dead(delivery_id, error_message)
            return {"status": "dead", "error": error_message}
        secret = webhook["secret"]
        tenant_id = webhook["tenant_id"]
        
        try:
            # Вычисляем HMAC-SHA256 подпись
            payload_json = json.dumps(payload, sort_keys=True)
            signature = self._compute_signature(payload_json, secret)
            
            # Отправляем HTTP POST
            with httpx.Client(timeout=self.http_timeout_seconds) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={
                        "X-Signature": signature,
                        "Content-Type": "application/json",
                        "User-Agent": "CyberPlat-Webhook/1.0"
                    }
                )
                
                # Проверяем статус ответа
                if 200 <= response.status_code < 300:
                    # Успешно отправлено
                    self.webhook_delivery_repo.mark_sent(delivery_id)
                    
                    # Эмитим событие
                    try:
                        self.event_service.emit(
                            event_type="webhook.sent",
                            tenant_id=tenant_id,
                            artifact_id=payload.get("artifact_id"),
                            payload={
                                "delivery_id": delivery_id,
                                "webhook_id": webhook_id,
                                "event_type": event_type,
                                "status_code": response.status_code
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to emit webhook.sent event: {e}")
                    
                    logger.info(
                        f"Webhook delivery sent: {delivery_id} "
                        f"(webhook={webhook_id}, event={event_type}, status={response.status_code})"
                    )
                    
                    return {"status": "sent", "error": None}
                else:
                    # Ошибка HTTP
                    error_message = f"HTTP {response.status_code}: {response.text[:200]}"
                    raise Exception(error_message)
                    
        except Exception as e:
            error_message = str(e)
            error_message_short = error_message[:500] if len(error_message) > 500 else error_message
            
            new_attempts = attempts + 1
            
            # Проверяем, не превышен ли max_retries
            if new_attempts >= max_retries:
                # Отмечаем delivery как dead
                self.webhook_delivery_repo.mark_dead(
                    delivery_id=delivery_id,
                    error_message=error_message_short
                )
                
                # Эмитим событие
                try:
                    self.event_service.emit(
                        event_type="webhook.dead",
                        tenant_id=tenant_id,
                        artifact_id=payload.get("artifact_id"),
                        payload={
                            "delivery_id": delivery_id,
                            "webhook_id": webhook_id,
                            "event_type": event_type,
                            "attempts": new_attempts,
                            "error": error_message_short
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit webhook.dead event: {e}")
                
                logger.error(
                    f"Webhook delivery dead after {new_attempts} attempts: "
                    f"delivery_id={delivery_id}, error={error_message_short}"
                )
                
                return {"status": "dead", "error": error_message_short}
            
            # Вычисляем next_run_at с exponential backoff + jitter
            next_run_at = self._calculate_next_run_at(new_attempts)
            
            # Отмечаем delivery как failed и планируем retry
            self.webhook_delivery_repo.mark_failed(
                delivery_id=delivery_id,
                error_message=error_message_short,
                next_run_at=next_run_at,
                attempts=new_attempts
            )
            
            # Эмитим событие
            try:
                self.event_service.emit(
                    event_type="webhook.failed",
                    tenant_id=tenant_id,
                    artifact_id=payload.get("artifact_id"),
                    payload={
                        "delivery_id": delivery_id,
                        "webhook_id": webhook_id,
                        "event_type": event_type,
                        "attempts": new_attempts,
                        "error": error_message_short,
                        "next_run_at": next_run_at
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to emit webhook.failed event: {e}")
            
            logger.warning(
                f"Webhook delivery failed (attempt {new_attempts}/{max_retries}): "
                f"delivery_id={delivery_id}, error={error_message_short}, next_run_at={next_run_at}"
            )
            
            return {"status": "failed", "error": error_message_short}
    
    def _compute_signature(self, payload: str, secret: str) -> str:
        """
        Вычислить HMAC-SHA256 подпись для payload.
        
        Args:
            payload: JSON payload (строка)
            secret: Secret key для подписи
            
        Returns:
            Hex string подписи (64 символа)
        """
        signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    def _calculate_next_run_at(self, attempts: int) -> str:
        """
        Вычислить next_run_at с exponential backoff + jitter.
        
        Args:
            attempts: Номер попытки (1-based)
            
        Returns:
            ISO format timestamp string
        """
        # Exponential backoff: base * 2^(attempts-1)
        backoff_seconds = self.retry_base_seconds * (2 ** (attempts - 1))
        
        # Ограничиваем максимумом
        backoff_seconds = min(backoff_seconds, self.retry_max_seconds)
        
        # Добавляем jitter (±20%)
        jitter_range = backoff_seconds * 0.2
        jitter = random.uniform(-jitter_range, jitter_range)
        backoff_seconds = max(1, backoff_seconds + jitter)  # Минимум 1 секунда
        
        # Вычисляем next_run_at
        next_run_dt = datetime.now() + timedelta(seconds=backoff_seconds)
        
        return next_run_dt.isoformat()
