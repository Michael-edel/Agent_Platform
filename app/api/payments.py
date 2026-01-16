"""API роутер для работы с платёжными поручениями."""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Query
from pydantic import BaseModel

from cyberplat.payments.payment_service import PaymentService, PaymentNotFoundError, InvalidApprovalError
from cyberplat.case_service import CaseService

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic модели
class CreatePaymentOrderRequest(BaseModel):
    amount: float
    beneficiary_name: str
    beneficiary_account_iban: str
    purpose: str
    created_by_role: str
    case_id: Optional[str] = None
    source_invoice_id: Optional[str] = None
    currency: str = "KZT"
    beneficiary_iin_bin: Optional[str] = None
    beneficiary_bank_bic: Optional[str] = None


class PaymentOrderResponse(BaseModel):
    id: str
    tenant_id: str
    case_id: Optional[str]
    source_invoice_id: Optional[str]
    amount: float
    currency: str
    beneficiary_name: str
    beneficiary_iin_bin: Optional[str]
    beneficiary_bank_bic: Optional[str]
    beneficiary_account_iban: str
    purpose: str
    status: str
    created_by_role: str
    created_at: str
    updated_at: str
    approved_at: Optional[str]
    rejected_at: Optional[str]
    exported_at: Optional[str]


class PaymentOrdersListResponse(BaseModel):
    items: List[PaymentOrderResponse]
    total: int


class SubmitForApprovalResponse(BaseModel):
    success: bool
    message: str


class ApproveRequest(BaseModel):
    role: str
    comment: Optional[str] = None
    decided_by: Optional[str] = None


class ApproveResponse(BaseModel):
    success: bool
    message: str


class RejectRequest(BaseModel):
    role: str
    comment: Optional[str] = None
    decided_by: Optional[str] = None


class RejectResponse(BaseModel):
    success: bool
    message: str


class ExportRequest(BaseModel):
    format: str  # csv или onec


class ExportResponse(BaseModel):
    success: bool
    message: str
    export_id: Optional[str] = None
    file_id: Optional[str] = None
    file_path: Optional[str] = None


class PaymentPolicyResponse(BaseModel):
    tenant_id: str
    enabled: bool
    thresholds: List[dict]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PaymentPolicyRequest(BaseModel):
    enabled: bool
    thresholds: List[dict]  # [{"max": 500000, "roles": ["accountant"]}, ...]


# Dependencies
def get_payment_service() -> PaymentService:
    """Получить экземпляр PaymentService."""
    return PaymentService()


def get_case_service() -> CaseService:
    """Получить экземпляр CaseService."""
    return CaseService()


@router.post("/payments/orders", response_model=PaymentOrderResponse, status_code=201)
async def create_payment_order(
    request: CreatePaymentOrderRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Создать платёжное поручение.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        # Проверяем case_id если указан
        if request.case_id:
            case = case_service.get_case(request.case_id, tenant_id=x_tenant_id)
            if not case:
                raise HTTPException(status_code=404, detail="Кейс не найден")
        
        order_id = payment_service.create_payment_order(
            tenant_id=x_tenant_id,
            amount=request.amount,
            beneficiary_name=request.beneficiary_name,
            beneficiary_account_iban=request.beneficiary_account_iban,
            purpose=request.purpose,
            created_by_role=request.created_by_role,
            case_id=request.case_id,
            source_invoice_id=request.source_invoice_id,
            currency=request.currency,
            beneficiary_iin_bin=request.beneficiary_iin_bin,
            beneficiary_bank_bic=request.beneficiary_bank_bic
        )
        
        order = payment_service.get_payment_order(order_id, tenant_id=x_tenant_id)
        if not order:
            raise HTTPException(status_code=500, detail="Ошибка при создании платёжного поручения")
        
        return PaymentOrderResponse(**order)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при создании платёжного поручения: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при создании платёжного поручения: {str(e)}")


@router.get("/payments/orders/{order_id}", response_model=PaymentOrderResponse)
async def get_payment_order(
    order_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Получить платёжное поручение по ID.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        order = payment_service.get_payment_order(order_id, tenant_id=x_tenant_id)
        if not order:
            raise HTTPException(status_code=404, detail="Платёжное поручение не найдено")
        return PaymentOrderResponse(**order)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении платёжного поручения: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении платёжного поручения: {str(e)}")


@router.get("/payments/orders", response_model=PaymentOrdersListResponse)
async def list_payment_orders(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    case_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Получить список платёжных поручений.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        orders = payment_service.list_payment_orders(
            tenant_id=effective_tenant_id,
            status=status,
            case_id=case_id
        )
        return PaymentOrdersListResponse(
            items=[PaymentOrderResponse(**order) for order in orders],
            total=len(orders)
        )
    except Exception as e:
        logger.error(f"Ошибка при получении списка платёжных поручений: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении списка: {str(e)}")


@router.post("/payments/orders/{order_id}/submit", response_model=SubmitForApprovalResponse, status_code=200)
async def submit_for_approval(
    order_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Отправить платёжное поручение на согласование.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        payment_service.submit_for_approval(order_id, x_tenant_id)
        
        # Получаем обновлённый order для проверки case_id
        order = payment_service.get_payment_order(order_id, tenant_id=x_tenant_id)
        if order and order.get("case_id"):
            # Создаём задачу в кейсе
            try:
                case_id = order["case_id"]
                case = case_service.get_case(case_id, tenant_id=x_tenant_id)
                if case:
                    # Определяем роль для задачи (первый required_role из approvals)
                    approvals = payment_service.get_approvals(order_id, tenant_id=x_tenant_id)
                    if approvals:
                        required_role = approvals[0]["required_role"]
                        case_service.add_task(
                            case_id=case_id,
                            step_key=case.get("current_step") or "approval",
                            title="Согласовать платеж",
                            assignee_role=required_role
                        )
                        
                        # Логируем событие
                        conn = case_service._get_connection()
                        case_service._log_event(
                            conn,
                            case_id,
                            "payment_submitted",
                            {
                                "payment_order_id": order_id,
                                "amount": order["amount"],
                                "currency": order["currency"]
                            }
                        )
                        conn.commit()
                        conn.close()
            except Exception as e:
                logger.warning(f"Ошибка при создании задачи в кейсе: {e}")
        
        return SubmitForApprovalResponse(
            success=True,
            message="Платёжное поручение отправлено на согласование"
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при отправке на согласование: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при отправке на согласование: {str(e)}")


@router.post("/payments/orders/{order_id}/approve", response_model=ApproveResponse, status_code=200)
async def approve_payment_order(
    order_id: str,
    request: ApproveRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Одобрить шаг согласования платёжного поручения.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        payment_service.approve(
            order_id=order_id,
            tenant_id=x_tenant_id,
            role=request.role,
            comment=request.comment,
            decided_by=request.decided_by
        )
        
        # Получаем обновлённый order
        order = payment_service.get_payment_order(order_id, tenant_id=x_tenant_id)
        if order and order.get("case_id") and order["status"] == "approved":
            # Все шаги одобрены - создаём задачу на экспорт
            try:
                case_id = order["case_id"]
                case_service.add_task(
                    case_id=case_id,
                    step_key=case.get("current_step") or "export",
                    title="Выгрузить платеж",
                    assignee_role="accountant"
                )
                
                # Логируем событие
                conn = case_service._get_connection()
                case_service._log_event(
                    conn,
                    case_id,
                    "payment_approved",
                    {
                        "payment_order_id": order_id,
                        "amount": order["amount"]
                    }
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Ошибка при создании задачи экспорта в кейсе: {e}")
        
        return ApproveResponse(
            success=True,
            message="Шаг согласования одобрен"
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при одобрении: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при одобрении: {str(e)}")


@router.post("/payments/orders/{order_id}/reject", response_model=RejectResponse, status_code=200)
async def reject_payment_order(
    order_id: str,
    request: RejectRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Отклонить платёжное поручение.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        payment_service.reject(
            order_id=order_id,
            tenant_id=x_tenant_id,
            role=request.role,
            comment=request.comment,
            decided_by=request.decided_by
        )
        
        # Логируем событие в кейсе
        order = payment_service.get_payment_order(order_id, tenant_id=x_tenant_id)
        if order and order.get("case_id"):
            try:
                case_id = order["case_id"]
                conn = case_service._get_connection()
                case_service._log_event(
                    conn,
                    case_id,
                    "payment_rejected",
                    {
                        "payment_order_id": order_id,
                        "role": request.role,
                        "comment": request.comment
                    }
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Ошибка при записи события в кейс: {e}")
        
        return RejectResponse(
            success=True,
            message="Платёжное поручение отклонено"
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при отклонении: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при отклонении: {str(e)}")


@router.post("/payments/orders/{order_id}/export", response_model=ExportResponse, status_code=200)
async def export_payment_order(
    order_id: str,
    request: ExportRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Экспортировать платёжное поручение.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    if request.format not in {"csv", "onec"}:
        raise HTTPException(status_code=400, detail="Формат должен быть 'csv' или 'onec'")
    
    try:
        result = payment_service.export(
            order_id=order_id,
            tenant_id=x_tenant_id,
            format=request.format
        )
        
        # Метрики
        try:
            from cyberplat.observability.metrics import payment_export_total, METRICS_ENABLED
            if METRICS_ENABLED and payment_export_total:
                payment_export_total.labels(format=request.format, status="succeeded").inc()
        except Exception:
            pass
        
        return ExportResponse(
            success=True,
            message=f"Платёжное поручение экспортировано в {request.format}",
            export_id=result.get("export_id"),
            file_id=result.get("file_id"),
            file_path=result.get("file_path")
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при экспорте: {e}", exc_info=True)
        
        # Метрики ошибки
        try:
            from cyberplat.observability.metrics import payment_export_total, METRICS_ENABLED
            if METRICS_ENABLED and payment_export_total:
                payment_export_total.labels(format=request.format, status="failed").inc()
        except Exception:
            pass
        
        raise HTTPException(status_code=500, detail=f"Ошибка при экспорте: {str(e)}")


@router.get("/payments/policy", response_model=PaymentPolicyResponse)
async def get_payment_policy(
    tenant_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Получить политику согласования для tenant.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        policy = payment_service.get_payment_policy(effective_tenant_id)
        if not policy:
            raise HTTPException(status_code=404, detail="Политика согласования не найдена")
        return PaymentPolicyResponse(**policy)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении политики: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении политики: {str(e)}")


@router.put("/payments/policy", response_model=PaymentPolicyResponse)
async def upsert_payment_policy(
    request: PaymentPolicyRequest,
    tenant_id: Optional[str] = Query(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Создать или обновить политику согласования.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        payment_service.upsert_payment_policy(
            tenant_id=effective_tenant_id,
            enabled=request.enabled,
            thresholds=request.thresholds
        )
        
        policy = payment_service.get_payment_policy(effective_tenant_id)
        if not policy:
            raise HTTPException(status_code=500, detail="Ошибка при сохранении политики")
        return PaymentPolicyResponse(**policy)
    except Exception as e:
        logger.error(f"Ошибка при сохранении политики: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении политики: {str(e)}")
