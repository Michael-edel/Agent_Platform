"""Webhooks API endpoints (tenant API + admin dispatch)."""

import logging
import secrets
import os
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from pydantic import BaseModel, HttpUrl

from app.api.admin_auth import admin_auth
from app.security.auth import require_roles
from app.security.outbound import OutboundDestinationError, validate_public_webhook_url
from cyberplat.product.infrastructure.database import get_sessionmaker
from cyberplat.event_service import EventService  # noqa: F401  (для unit-тестов, которые патчат модуль)

logger = logging.getLogger(__name__)

router = APIRouter()


# DTOs для tenant API
class CreateWebhookRequest(BaseModel):
    url: HttpUrl
    events: List[str]


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: List[str]
    active: bool
    created_at: str


class WebhookListResponse(BaseModel):
    webhooks: List[WebhookResponse]


# Tenant API endpoints
@router.get("/webhooks")
async def list_webhooks(
    request: Request,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID")
):
    """
    Получить список webhooks для tenant.
    
    Tenant-scoped: показывает только webhooks текущего tenant.
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import WebhookRepositoryImpl
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        
        webhooks = webhook_repo.list_webhooks(
            tenant_id=x_tenant_id,
            active_only=False  # Показываем все (включая неактивные)
        )
        
        return WebhookListResponse(
            webhooks=[
                WebhookResponse(
                    id=w["id"],
                    url=w["url"],
                    events=w["events"],
                    active=w["active"],
                    created_at=w["created_at"]
                )
                for w in webhooks
            ]
        )
        
    finally:
        session.close()


@router.post("/webhooks")
async def create_webhook(
    request: Request,
    webhook_data: CreateWebhookRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    _: None = Depends(require_roles("system")),
):
    """
    Создать webhook для tenant.
    
    Генерирует случайный secret для HMAC подписи.
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    try:
        validate_public_webhook_url(str(webhook_data.url))
    except OutboundDestinationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Валидация events
    valid_events = {
        "invoice.ready",
        "invoice.failed",
        "email.ocr.completed",
        "email.ocr.dead",
        "invoice.confirmed"
    }
    
    for event in webhook_data.events:
        if event not in valid_events:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event type: {event}. Valid events: {', '.join(sorted(valid_events))}"
            )
    
    # Генерируем secret
    secret = secrets.token_urlsafe(32)
    
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import WebhookRepositoryImpl
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        
        webhook_id = webhook_repo.create_webhook(
            tenant_id=x_tenant_id,
            url=str(webhook_data.url),
            events=webhook_data.events,
            secret=secret
        )
        
        webhook = webhook_repo.get_webhook(
            tenant_id=x_tenant_id,
            webhook_id=webhook_id
        )
        
        if not webhook:
            raise HTTPException(status_code=500, detail="Failed to retrieve created webhook")
        
        return WebhookResponse(
            id=webhook["id"],
            url=webhook["url"],
            events=webhook["events"],
            active=webhook["active"],
            created_at=webhook["created_at"]
        )
        
    finally:
        session.close()


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    request: Request,
    webhook_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    _: None = Depends(require_roles("system")),
):
    """
    Удалить webhook.
    
    Tenant-scoped: можно удалить только свой webhook.
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import WebhookRepositoryImpl
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        
        success = webhook_repo.delete_webhook(
            tenant_id=x_tenant_id,
            webhook_id=webhook_id
        )
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail="Webhook not found"
            )
        
        return {"success": True, "message": "Webhook deleted"}
        
    finally:
        session.close()


@router.post("/webhooks/{webhook_id}/rotate-secret")
async def rotate_webhook_secret(
    request: Request,
    webhook_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    _: None = Depends(require_roles("system")),
):
    """
    Обновить secret для webhook (rotate).
    
    Генерирует новый secret и возвращает его (только один раз).
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    # Генерируем новый secret
    new_secret = secrets.token_urlsafe(32)
    
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import WebhookRepositoryImpl
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        
        success = webhook_repo.rotate_secret(
            tenant_id=x_tenant_id,
            webhook_id=webhook_id,
            new_secret=new_secret
        )
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail="Webhook not found"
            )
        
        # Возвращаем новый secret (только один раз, в production лучше через отдельный endpoint)
        return {
            "success": True,
            "secret": new_secret,
            "message": "Secret rotated. Save this secret - it will not be shown again."
        }
        
    finally:
        session.close()


# Admin dispatch endpoint
@router.post("/webhooks/dispatch")
async def dispatch_webhooks(
    request: Request,
    limit: int = 10,
    _admin_auth: bool = Depends(admin_auth)
):
    """
    Dispatch webhook deliveries for processing.
    
    Admin-only endpoint. Требует X-Admin-Key заголовок (если ADMIN_API_KEY задан).
    
    Этот endpoint должен вызываться периодически (например, через cron) для отправки
    webhook deliveries.
    
    Args:
        limit: Максимальное количество deliveries для обработки за один вызов (default: 10)
        _admin_auth: Admin authentication (dependency)
        
    Returns:
        {
            "processed": int,
            "sent": int,
            "failed": int,
            "dead": int,
            "errors": List[str]
        }
    """
    event_service = getattr(request.app.state, "event_service", None)
    
    if not event_service:
        raise HTTPException(
            status_code=500,
            detail="EventService not initialized"
        )
    
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import (
        WebhookRepositoryImpl,
        WebhookDeliveryRepositoryImpl
    )
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        webhook_delivery_repo = WebhookDeliveryRepositoryImpl(session=session)
        
        # Получаем параметры из env
        max_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "5"))
        retry_base_seconds = int(os.getenv("WEBHOOK_RETRY_BASE_SECONDS", "10"))
        retry_max_seconds = int(os.getenv("WEBHOOK_RETRY_MAX_SECONDS", "600"))
        http_timeout_seconds = int(os.getenv("WEBHOOK_HTTP_TIMEOUT_SECONDS", "30"))
        
        # Создаём use case
        from cyberplat.product.application.dispatch_webhooks_use_case import DispatchWebhooksUseCase
        use_case = DispatchWebhooksUseCase(
            webhook_repo=webhook_repo,
            webhook_delivery_repo=webhook_delivery_repo,
            event_service=event_service,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
            http_timeout_seconds=http_timeout_seconds
        )
        
        # Выполняем use case
        result = use_case.execute(limit=limit)
        
        return result
        
    finally:
        session.close()
