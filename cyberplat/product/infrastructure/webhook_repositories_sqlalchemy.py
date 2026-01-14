"""SQLAlchemy-based repository implementations for webhooks."""

import uuid
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text

from cyberplat.product.domain.interfaces import WebhookRepository, WebhookDeliveryRepository
from cyberplat.product.infrastructure.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)


class WebhookRepositoryImpl(WebhookRepository):
    """SQLAlchemy-based repository implementation для webhooks."""
    
    def __init__(self, session: Session):
        self.session = session
        # Сохраняем session для доступа из use case
    
    def create_webhook(
        self,
        tenant_id: str,
        url: str,
        events: List[str],
        secret: str
    ) -> str:
        """Создать webhook."""
        webhook_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        webhook = Webhook(
            id=webhook_id,
            tenant_id=tenant_id,
            url=url,
            events=json.dumps(events),
            secret=secret,
            active=True,
            created_at=now
        )
        
        self.session.add(webhook)
        self.session.commit()
        
        logger.info(f"Created webhook: {webhook_id} (tenant={tenant_id}, url={url})")
        return webhook_id
    
    def get_webhook(
        self,
        tenant_id: str,
        webhook_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить webhook по ID."""
        webhook = self.session.query(Webhook).filter(
            and_(
                Webhook.id == webhook_id,
                Webhook.tenant_id == tenant_id
            )
        ).first()
        
        if not webhook:
            return None
        
        return {
            "id": webhook.id,
            "tenant_id": webhook.tenant_id,
            "url": webhook.url,
            "events": json.loads(webhook.events),
            "secret": webhook.secret,
            "active": webhook.active,
            "created_at": webhook.created_at
        }
    
    def list_webhooks(
        self,
        tenant_id: str,
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Получить список webhooks для tenant."""
        query = self.session.query(Webhook).filter(Webhook.tenant_id == tenant_id)
        
        if active_only:
            query = query.filter(Webhook.active == True)
        
        webhooks = query.order_by(Webhook.created_at.desc()).all()
        
        results = []
        for webhook in webhooks:
            results.append({
                "id": webhook.id,
                "tenant_id": webhook.tenant_id,
                "url": webhook.url,
                "events": json.loads(webhook.events),
                "secret": webhook.secret,
                "active": webhook.active,
                "created_at": webhook.created_at
            })
        
        return results
    
    def list_active_webhooks(
        self,
        tenant_id: str,
        event_type: str
    ) -> List[Dict[str, Any]]:
        """
        Получить активные webhooks для tenant, которые подписаны на event_type.
        
        Returns:
            List of webhooks with events containing event_type
        """
        # Используем raw SQL для поиска в JSON массиве events
        # SQLite: JSON_EXTRACT или LIKE для простоты
        # PostgreSQL: @> или ? оператор
        
        # Для MVP используем простой подход: загружаем все активные webhooks и фильтруем в Python
        webhooks = self.session.query(Webhook).filter(
            and_(
                Webhook.tenant_id == tenant_id,
                Webhook.active == True
            )
        ).all()
        
        results = []
        for webhook in webhooks:
            events = json.loads(webhook.events)
            if event_type in events:
                results.append({
                    "id": webhook.id,
                    "tenant_id": webhook.tenant_id,
                    "url": webhook.url,
                    "events": events,
                    "secret": webhook.secret,
                    "active": webhook.active,
                    "created_at": webhook.created_at
                })
        
        return results
    
    def update_webhook(
        self,
        tenant_id: str,
        webhook_id: str,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        active: Optional[bool] = None
    ) -> bool:
        """Обновить webhook."""
        webhook = self.session.query(Webhook).filter(
            and_(
                Webhook.id == webhook_id,
                Webhook.tenant_id == tenant_id
            )
        ).first()
        
        if not webhook:
            return False
        
        if url is not None:
            webhook.url = url
        if events is not None:
            webhook.events = json.dumps(events)
        if active is not None:
            webhook.active = active
        
        self.session.commit()
        return True
    
    def rotate_secret(
        self,
        tenant_id: str,
        webhook_id: str,
        new_secret: str
    ) -> bool:
        """Обновить secret для webhook."""
        webhook = self.session.query(Webhook).filter(
            and_(
                Webhook.id == webhook_id,
                Webhook.tenant_id == tenant_id
            )
        ).first()
        
        if not webhook:
            return False
        
        webhook.secret = new_secret
        self.session.commit()
        
        logger.info(f"Rotated secret for webhook: {webhook_id} (tenant={tenant_id})")
        return True
    
    def delete_webhook(
        self,
        tenant_id: str,
        webhook_id: str
    ) -> bool:
        """Удалить webhook."""
        webhook = self.session.query(Webhook).filter(
            and_(
                Webhook.id == webhook_id,
                Webhook.tenant_id == tenant_id
            )
        ).first()
        
        if not webhook:
            return False
        
        self.session.delete(webhook)
        self.session.commit()
        
        logger.info(f"Deleted webhook: {webhook_id} (tenant={tenant_id})")
        return True
    
    def get_webhook_by_id(
        self,
        webhook_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить webhook по ID (без tenant_id проверки, для внутреннего использования).
        
        Используется dispatcher для получения webhook по delivery.webhook_id.
        """
        webhook = self.session.query(Webhook).filter(Webhook.id == webhook_id).first()
        
        if not webhook:
            return None
        
        return {
            "id": webhook.id,
            "tenant_id": webhook.tenant_id,
            "url": webhook.url,
            "events": json.loads(webhook.events),
            "secret": webhook.secret,
            "active": webhook.active,
            "created_at": webhook.created_at
        }
    
    def get_webhook_by_id(
        self,
        webhook_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить webhook по ID (без tenant_id проверки, для внутреннего использования).
        
        Используется dispatcher для получения webhook по delivery.webhook_id.
        """
        webhook = self.session.query(Webhook).filter(Webhook.id == webhook_id).first()
        
        if not webhook:
            return None
        
        return {
            "id": webhook.id,
            "tenant_id": webhook.tenant_id,
            "url": webhook.url,
            "events": json.loads(webhook.events),
            "secret": webhook.secret,
            "active": webhook.active,
            "created_at": webhook.created_at
        }


class WebhookDeliveryRepositoryImpl(WebhookDeliveryRepository):
    """SQLAlchemy-based repository implementation для webhook deliveries."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create_delivery(
        self,
        webhook_id: str,
        event_type: str,
        payload: Dict[str, Any],
        max_retries: int = 5
    ) -> str:
        """
        Создать delivery (или вернуть существующий по idempotency).
        
        Idempotency: один event для одного webhook → один delivery.
        Для простоты MVP: создаём новый delivery каждый раз.
        В production можно добавить idempotency_key на основе webhook_id + event_type + payload hash.
        """
        delivery_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        delivery = WebhookDelivery(
            id=delivery_id,
            webhook_id=webhook_id,
            event_type=event_type,
            payload=json.dumps(payload),
            status="queued",
            attempts=0,
            max_retries=max_retries,
            next_run_at=now,  # Готов к немедленной отправке
            created_at=now
        )
        
        self.session.add(delivery)
        self.session.commit()
        
        logger.info(f"Created webhook delivery: {delivery_id} (webhook={webhook_id}, event={event_type})")
        return delivery_id
    
    def get_pending_deliveries(
        self,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получить deliveries готовые к отправке (status in (queued, failed) AND next_run_at <= now).
        
        Returns:
            List of deliveries ordered by next_run_at ASC
        """
        now = datetime.now().isoformat()
        
        # Используем raw SQL для эффективного запроса
        query = text("""
            SELECT * FROM webhook_deliveries
            WHERE status IN ('queued', 'failed')
            AND (next_run_at IS NULL OR next_run_at <= :now)
            ORDER BY next_run_at ASC NULLS FIRST
            LIMIT :limit
        """)
        
        result = self.session.execute(query, {"now": now, "limit": limit})
        rows = result.fetchall()
        
        deliveries = []
        for row in rows:
            delivery_dict = {
                "id": row.id,
                "webhook_id": row.webhook_id,
                "event_type": row.event_type,
                "payload": json.loads(row.payload),
                "status": row.status,
                "attempts": row.attempts,
                "max_retries": row.max_retries,
                "last_error": row.last_error,
                "next_run_at": row.next_run_at,
                "created_at": row.created_at,
                "sent_at": row.sent_at
            }
            deliveries.append(delivery_dict)
        
        return deliveries
    
    def mark_sent(
        self,
        delivery_id: str
    ) -> bool:
        """Отметить delivery как успешно отправленный."""
        delivery = self.session.query(WebhookDelivery).filter(
            WebhookDelivery.id == delivery_id
        ).first()
        
        if not delivery:
            return False
        
        now = datetime.now().isoformat()
        delivery.status = "sent"
        delivery.sent_at = now
        
        self.session.commit()
        return True
    
    def mark_failed(
        self,
        delivery_id: str,
        error_message: str,
        next_run_at: str,
        attempts: int
    ) -> bool:
        """Отметить delivery как failed и запланировать retry."""
        delivery = self.session.query(WebhookDelivery).filter(
            WebhookDelivery.id == delivery_id
        ).first()
        
        if not delivery:
            return False
        
        delivery.status = "failed"
        delivery.last_error = error_message[:500] if error_message else None
        delivery.next_run_at = next_run_at
        delivery.attempts = attempts
        
        self.session.commit()
        return True
    
    def mark_dead(
        self,
        delivery_id: str,
        error_message: str
    ) -> bool:
        """Отметить delivery как dead (превышен max_retries)."""
        delivery = self.session.query(WebhookDelivery).filter(
            WebhookDelivery.id == delivery_id
        ).first()
        
        if not delivery:
            return False
        
        delivery.status = "dead"
        delivery.last_error = error_message[:500] if error_message else None
        
        self.session.commit()
        return True
    
    def get_delivery(
        self,
        delivery_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить delivery по ID."""
        delivery = self.session.query(WebhookDelivery).filter(
            WebhookDelivery.id == delivery_id
        ).first()
        
        if not delivery:
            return None
        
        return {
            "id": delivery.id,
            "webhook_id": delivery.webhook_id,
            "event_type": delivery.event_type,
            "payload": json.loads(delivery.payload),
            "status": delivery.status,
            "attempts": delivery.attempts,
            "max_retries": delivery.max_retries,
            "last_error": delivery.last_error,
            "next_run_at": delivery.next_run_at,
            "created_at": delivery.created_at,
            "sent_at": delivery.sent_at
        }
