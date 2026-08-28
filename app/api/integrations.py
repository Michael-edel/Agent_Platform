"""API роутер для работы с интеграциями."""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Query, Request
from pydantic import BaseModel

from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.onec_client import OneCClient, OneCAuthError, OneCTransportError
from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.payments.payment_service import (
    PaymentService,
    PaymentNotFoundError,
    PaymentState,
    InvalidIntegrationConfirmationError,
)
from cyberplat.event_service import EventService
from app.security.auth import require_roles, get_actor

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic модели
class OneCSettingsResponse(BaseModel):
    tenant_id: str
    enabled: bool
    base_url: str
    auth_type: str
    timeout_seconds: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class OneCSettingsRequest(BaseModel):
    enabled: bool
    base_url: str
    auth_type: str = "token"
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    timeout_seconds: int = 10


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


class IntegrationJobResponse(BaseModel):
    id: str
    tenant_id: str
    case_id: Optional[str]
    provider: str
    job_type: str
    status: str
    attempt: int
    max_attempts: int
    last_error_ru: Optional[str]
    last_error_code: Optional[str]
    created_at: str
    updated_at: str


class IntegrationJobsListResponse(BaseModel):
    items: List[IntegrationJobResponse]
    total: int


# Dependencies
def get_settings_service() -> OneCSettingsService:
    """Получить экземпляр OneCSettingsService."""
    return OneCSettingsService()


def get_job_service() -> IntegrationJobService:
    """Получить экземпляр IntegrationJobService."""
    return IntegrationJobService()


def get_payment_service() -> PaymentService:
    return PaymentService()


def get_event_service() -> EventService:
    return EventService()


class OneCPaymentSummaryTimeline(BaseModel):
    created_at: Optional[str] = None
    approved_at: Optional[str] = None
    reconciled_at: Optional[str] = None


class OneCPaymentSummaryResponse(BaseModel):
    payment_id: str
    tenant_id: str
    state: str
    amount: float
    currency: str
    document_id: Optional[str] = None
    case_id: Optional[str] = None
    timeline: OneCPaymentSummaryTimeline


class OneCConfirmRequest(BaseModel):
    payment_id: str
    external_id: Optional[str] = None
    result: str  # CONFIRMED|REJECTED
    confirmed_at: str  # YYYY-MM-DD
    reason: Optional[str] = None


class OneCConfirmResponse(BaseModel):
    success: bool
    payment_id: str
    new_state: str
    message: str


@router.get("/integrations/1c/payments/{payment_id}/summary", response_model=OneCPaymentSummaryResponse)
async def get_onec_payment_summary(
    payment_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
):
    """Read-only payment summary for 1C (trust bridge)."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")

    order = payment_service.get_payment_order(payment_id, tenant_id=x_tenant_id)
    if not order:
        raise HTTPException(status_code=404, detail="Платёжное поручение не найдено")

    # derive timestamps from timeline events (source of truth)
    timeline_items = payment_service.get_payment_timeline(tenant_id=x_tenant_id, payment_id=payment_id)
    created_at = None
    approved_at = None
    reconciled_at = None
    for it in timeline_items:
        if it.get("event") == "payment.created" and not created_at:
            created_at = it.get("at")
        if it.get("event") == "payment.approved" and not approved_at:
            approved_at = it.get("at")
        if it.get("event") == "payment.reconciled" and not reconciled_at:
            reconciled_at = it.get("at")

    try:
        state = PaymentState(order["status"]).name
    except Exception:
        state = (order.get("status") or "").upper() or "UNKNOWN"

    return OneCPaymentSummaryResponse(
        payment_id=payment_id,
        tenant_id=order["tenant_id"],
        state=state,
        amount=float(order["amount"]),
        currency=order.get("currency") or "KZT",
        document_id=order.get("source_invoice_id"),
        case_id=order.get("case_id"),
        timeline=OneCPaymentSummaryTimeline(
            created_at=created_at or order.get("created_at"),
            approved_at=approved_at or order.get("approved_at"),
            reconciled_at=reconciled_at,
        ),
    )


@router.post("/integrations/1c/payments/confirm", response_model=OneCConfirmResponse, status_code=200)
async def confirm_onec_payment(
    request: OneCConfirmRequest,
    http_request: Request,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    event_service: EventService = Depends(get_event_service),
    _: None = Depends(require_roles("system", "accountant")),
):
    """Inbound confirmation from 1C (idempotent, minimal side-effects)."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")

    if request.result not in {"CONFIRMED", "REJECTED"}:
        raise HTTPException(status_code=400, detail="result должен быть CONFIRMED|REJECTED")
    if request.result == "REJECTED" and (not request.reason or not request.reason.strip()):
        raise HTTPException(status_code=400, detail="reason обязателен для REJECTED")

    try:
        from datetime import datetime as _dt

        _dt.strptime(request.confirmed_at, "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="confirmed_at должен быть в формате YYYY-MM-DD")

    external_id_part = (request.external_id or "").strip()
    idempotency_key = f"1c_confirm:{x_tenant_id}:{request.payment_id}:{external_id_part}:{request.result}"
    event_type = "payment.sent" if request.result == "CONFIRMED" else "payment.sent_failed"

    # Idempotency by existing event
    if event_service.has_event_with_idempotency_key(
        tenant_id=x_tenant_id,
        artifact_id=request.payment_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
    ):
        order = payment_service.get_payment_order(request.payment_id, tenant_id=x_tenant_id) or {}
        try:
            state = PaymentState(order.get("status")).name
        except Exception:
            state = (order.get("status") or "").upper() or "UNKNOWN"
        return OneCConfirmResponse(
            success=True,
            payment_id=request.payment_id,
            new_state=state,
            message="Подтверждение уже обработано (идемпотентно)",
        )

    try:
        actor = get_actor(http_request)
        new_state = payment_service.apply_1c_confirmation(
            tenant_id=x_tenant_id,
            payment_id=request.payment_id,
            external_id=request.external_id,
            result=request.result,
            confirmed_at=request.confirmed_at,
            reason=request.reason,
            idempotency_key=idempotency_key,
            actor_role=actor.role,
            actor_subject=actor.subject,
        )
        return OneCConfirmResponse(
            success=True,
            payment_id=request.payment_id,
            new_state=new_state,
            message="Подтверждение принято",
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidIntegrationConfirmationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Ошибка при обработке подтверждения 1С: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке подтверждения 1С: {str(e)}")

@router.get("/integrations/onec/settings", response_model=OneCSettingsResponse)
async def get_onec_settings(
    tenant_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    settings_service: OneCSettingsService = Depends(get_settings_service)
):
    """
    Получить настройки интеграции 1С для tenant.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    settings = settings_service.get_settings(effective_tenant_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Настройки интеграции 1С не найдены")
    
    # Не возвращаем секреты
    return OneCSettingsResponse(
        tenant_id=settings["tenant_id"],
        enabled=settings["enabled"],
        base_url=settings["base_url"],
        auth_type=settings["auth_type"],
        timeout_seconds=settings["timeout_seconds"],
        created_at=settings["created_at"],
        updated_at=settings["updated_at"]
    )


@router.put("/integrations/onec/settings", response_model=OneCSettingsResponse)
async def upsert_onec_settings(
    request: OneCSettingsRequest,
    tenant_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    settings_service: OneCSettingsService = Depends(get_settings_service),
    _: None = Depends(require_roles("system")),
):
    """
    Создать или обновить настройки интеграции 1С.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        settings_service.upsert_settings(
            tenant_id=effective_tenant_id,
            enabled=request.enabled,
            base_url=request.base_url,
            auth_type=request.auth_type,
            token=request.token,
            username=request.username,
            password=request.password,
            timeout_seconds=request.timeout_seconds
        )
        
        settings = settings_service.get_settings(effective_tenant_id)
        return OneCSettingsResponse(
            tenant_id=settings["tenant_id"],
            enabled=settings["enabled"],
            base_url=settings["base_url"],
            auth_type=settings["auth_type"],
            timeout_seconds=settings["timeout_seconds"],
            created_at=settings["created_at"],
            updated_at=settings["updated_at"]
        )
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек 1С: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении настроек: {str(e)}")


@router.post("/integrations/onec/test-connection", response_model=TestConnectionResponse)
async def test_onec_connection(
    tenant_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    settings_service: OneCSettingsService = Depends(get_settings_service),
    _: None = Depends(require_roles("system")),
):
    """
    Проверить соединение с 1С.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    settings = settings_service.get_settings(effective_tenant_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Настройки интеграции 1С не найдены")
    
    if not settings["enabled"]:
        return TestConnectionResponse(
            success=False,
            message="Интеграция 1С отключена"
        )
    
    try:
        client = OneCClient(
            base_url=settings["base_url"],
            auth_type=settings["auth_type"],
            token=settings.get("token"),
            username=settings.get("username"),
            password=settings.get("password"),
            timeout=settings["timeout_seconds"]
        )
        
        success = client.ping()
        if success:
            return TestConnectionResponse(
                success=True,
                message="Соединение с 1С успешно"
            )
        else:
            return TestConnectionResponse(
                success=False,
                message="Не удалось подключиться к 1С"
            )
    except OneCAuthError:
        return TestConnectionResponse(
            success=False,
            message="Ошибка аутентификации в 1С"
        )
    except OneCTransportError as e:
        return TestConnectionResponse(
            success=False,
            message=f"Ошибка транспорта: {str(e)[:200]}"
        )
    except Exception as e:
        logger.error(f"Ошибка при проверке соединения с 1С: {e}", exc_info=True)
        return TestConnectionResponse(
            success=False,
            message=f"Ошибка: {str(e)[:200]}"
        )


@router.get("/integrations/jobs", response_model=IntegrationJobsListResponse)
async def list_integration_jobs(
    tenant_id: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    job_service: IntegrationJobService = Depends(get_job_service)
):
    """
    Получить список jobs интеграций.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        jobs = job_service.list_jobs(
            tenant_id=effective_tenant_id,
            provider=provider,
            status=status
        )
        
        return IntegrationJobsListResponse(
            items=[IntegrationJobResponse(**job) for job in jobs],
            total=len(jobs)
        )
    except Exception as e:
        logger.error(f"Ошибка при получении списка jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении списка jobs: {str(e)}")
