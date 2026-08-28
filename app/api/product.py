"""Product/UI layer API router with dependency injection."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.database import get_db_session
from cyberplat.product.infrastructure.repositories_sqlalchemy import (
    ArtifactStateRepositoryImpl,
    ExportRepositoryImpl
)
from cyberplat.product.domain.interfaces import (
    ArtifactStateRepository,
    ExportRepository
)
from cyberplat.product.application.dto import (
    DocumentDTO,
    InvoiceDTO,
    DocumentDetailDTO,
    InvoiceDetailDTO,
    DocumentsListResponse,
    InvoicesListResponse,
    ConfirmInvoiceResponse,
    ExportInvoiceRequest,
    ExportInvoiceResponse
)
from cyberplat.product.application.get_documents_use_case import GetDocumentsUseCase
from cyberplat.product.application.get_invoices_use_case import GetInvoicesUseCase
from cyberplat.product.application.get_document_detail_use_case import GetDocumentDetailUseCase
from cyberplat.product.application.get_invoice_detail_use_case import GetInvoiceDetailUseCase
from cyberplat.product.application.run_ocr_use_case import RunOCRUseCase
from cyberplat.product.application.confirm_invoice_use_case import ConfirmInvoiceUseCase
from cyberplat.product.application.export_invoice_use_case import ExportInvoiceUseCase
from app.security.auth import require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


# Dependency injection для репозиториев
def get_artifact_state_repo(db: Session = Depends(get_db_session)) -> ArtifactStateRepository:
    """Dependency для получения ArtifactStateRepository."""
    return ArtifactStateRepositoryImpl(session=db)


def get_export_repo(db: Session = Depends(get_db_session)) -> ExportRepository:
    """Dependency для получения ExportRepository."""
    return ExportRepositoryImpl(session=db)


# Dependency для получения tenant_id из header
def get_tenant_id(x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")) -> str:
    """Dependency для валидации и получения tenant_id."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID заголовок обязателен")
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(status_code=400, detail=f"Некорректный tenant_id: '{x_tenant_id}'")
    
    return x_tenant_id




@router.get("/documents", response_model=DocumentsListResponse)
async def get_documents(
    tenant_id: str = Depends(get_tenant_id),
    ui_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo)
):
    """
    Получить список документов для UI.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **ui_status**: Фильтр по UI статусу (опционально: 'pending', 'processing', 'completed', 'error')
    - **limit**: Лимит результатов (по умолчанию 100)
    - **offset**: Смещение для пагинации (по умолчанию 0)
    
    Возвращает UI-проекции документов (без raw OCR JSON).
    """
    use_case = GetDocumentsUseCase(artifact_state_repo=artifact_state_repo)
    items = use_case.execute(
        tenant_id=tenant_id,
        ui_status=ui_status,
        limit=limit,
        offset=offset
    )
    
    # Преобразуем в DTOs
    document_dtos = [DocumentDTO(**item) for item in items]
    
    return DocumentsListResponse(
        items=document_dtos,
        total=len(document_dtos),  # TODO: добавить count query для точного total
        limit=limit,
        offset=offset
    )


# Dependencies для получения legacy services из app.state
def get_artifact_service(request: Request):
    """Dependency для получения ArtifactService из app.state (legacy, пока не переведён на SQLAlchemy)."""
    artifact_service = getattr(request.app.state, "artifact_service", None)
    if not artifact_service:
        raise HTTPException(status_code=500, detail="Artifact service not initialized")
    return artifact_service


def get_agent_registry(request: Request):
    """Dependency для получения AgentRegistry из app.state."""
    agent_registry = getattr(request.app.state, "agent_registry", None)
    if not agent_registry:
        raise HTTPException(status_code=500, detail="Agent registry not initialized")
    return agent_registry


@router.get("/documents/{document_id}", response_model=DocumentDetailDTO)
async def get_document_detail(
    document_id: str,
    tenant_id: str = Depends(get_tenant_id),
    _: None = Depends(require_roles("accountant", "system")),
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo),
    artifact_service = Depends(get_artifact_service)
):
    """
    Получить детальную информацию о документе.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **document_id**: ID артефакта документа
    """
    if not artifact_service:
        raise HTTPException(status_code=500, detail="Artifact service not initialized")
    
    use_case = GetDocumentDetailUseCase(
        artifact_state_repo=artifact_state_repo,
        artifact_service=artifact_service
    )
    
    result = use_case.execute(tenant_id=tenant_id, artifact_id=document_id)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    
    return DocumentDetailDTO(**result)


@router.post("/documents/{document_id}/run-ocr")
async def run_ocr_on_document(
    document_id: str,
    tenant_id: str = Depends(get_tenant_id),
    _: None = Depends(require_roles("accountant", "system")),
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo),
    request: Request = None  # Для получения services
):
    """
    Запустить OCR на документе (trigger doc_agent).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **document_id**: ID артефакта документа
    """
    from fastapi import Request
    from cyberplat.base_agent import AgentContext
    
    # Получаем services из app.state (legacy, пока не переведены на SQLAlchemy)
    if not request:
        raise HTTPException(status_code=500, detail="Request not available")
    artifact_service = getattr(request.app.state, "artifact_service", None)
    agent_registry = getattr(request.app.state, "agent_registry", None)
    billing_service = getattr(request.app.state, "billing_service", None)
    event_service = getattr(request.app.state, "event_service", None)
    entitlement_service = getattr(request.app.state, "entitlement_service", None)
    
    if not artifact_service or not agent_registry:
        raise HTTPException(status_code=500, detail="Services not initialized")
    
    # Проверка квот (enforcement)
    try:
        from cyberplat.product.application.billing_enforcement_service import (
            BillingEnforcementService,
            QuotaExceededError,
            TrialExpiredError,
            SubscriptionPastDueError,
            SubscriptionCanceledError,
        )
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
            TenantPlanRepositoryImpl,
            PlanRepositoryImpl,
        )
        from sqlalchemy.orm import Session
        
        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        try:
            tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
            plan_repo = PlanRepositoryImpl(session=session)
            billing_enforcement = BillingEnforcementService(
                billing_service=billing_service,
                event_service=event_service,
                tenant_plan_repo=tenant_plan_repo,
                plan_repo=plan_repo,
                entitlement_service=entitlement_service
            )
        finally:
            session.close()
        
        # Для MVP: page_processed=1 (можно расширить позже для реального количества страниц)
        billing_enforcement.enforce(
            tenant_id=tenant_id,
            required_metrics={
                "invoice_extracted": 1.0,
                "page_processed": 1.0
            },
            operation_name="run_ocr"
        )
    except Exception as e:
        if isinstance(e, TrialExpiredError):
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": "Пробный период истёк",
                    "trial_expired": True,
                    "expires_at": getattr(e, "expires_at", None),
                    "upgrade_url": "/billing/upgrade",
                },
            )
        if isinstance(e, SubscriptionPastDueError):
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": "Оплата просрочена",
                    "subscription_status": "past_due",
                    "expires_at": getattr(e, "expires_at", None),
                    "failed_charges": getattr(e, "failed_charges", None),
                    "upgrade_url": "/api/v1/billing/checkout/kaspi",
                },
            )
        if isinstance(e, SubscriptionCanceledError):
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": "Подписка отменена",
                    "subscription_status": "canceled",
                    "upgrade_url": "/api/v1/billing/checkout/kaspi",
                },
            )
        if isinstance(e, QuotaExceededError):
            # 402 Payment Required
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": "Квота превышена",
                    "metric": e.metric,
                    "period": e.period,
                    "used_units": e.used_units,
                    "monthly_quota": e.monthly_quota,
                    "operation": e.operation,
                    "upgrade_url": e.upgrade_url
                }
            )
        # Другие ошибки - пробрасываем дальше
        raise
    
    doc_agent = agent_registry.get("doc_agent")
    if not doc_agent:
        raise HTTPException(status_code=404, detail="doc_agent not found")
    
    use_case = RunOCRUseCase(
        artifact_state_repo=artifact_state_repo,
        doc_agent=doc_agent,
        artifact_service=artifact_service
    )
    
    try:
        success, invoice_artifact_id, error_message = await use_case.execute(
            tenant_id=tenant_id,
            artifact_id=document_id
        )
    except Exception as e:
        from cyberplat.product.application.billing_enforcement_service import QuotaExceededError
        if isinstance(e, QuotaExceededError):
            # 402 Payment Required
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": "Квота превышена",
                    "metric": e.metric,
                    "period": e.period,
                    "used_units": e.used_units,
                    "monthly_quota": e.monthly_quota,
                    "operation": e.operation,
                    "upgrade_url": e.upgrade_url
                }
            )
        # Другие ошибки - пробрасываем дальше
        raise
    
    from app.main import AgentRunResponse
    
    if success:
        return AgentRunResponse(
            success=True,
            artifact_id=invoice_artifact_id,
            tenant_id=tenant_id,
            error=None
        )
    else:
        return AgentRunResponse(
            success=False,
            artifact_id=None,
            tenant_id=tenant_id,
            error=error_message
        )


@router.get("/invoices", response_model=InvoicesListResponse)
async def get_invoices(
    tenant_id: str = Depends(get_tenant_id),
    ui_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo),
    artifact_service = Depends(get_artifact_service)
):
    """
    Получить список инвойсов для UI.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **ui_status**: Фильтр по UI статусу (опционально: 'pending', 'confirmed', 'exported', 'error')
    - **limit**: Лимит результатов (по умолчанию 100)
    - **offset**: Смещение для пагинации (по умолчанию 0)
    
    Возвращает UI-проекции инвойсов (без raw OCR JSON).
    """
    
    use_case = GetInvoicesUseCase(artifact_state_repo=artifact_state_repo)
    items = use_case.execute(
        tenant_id=tenant_id,
        ui_status=ui_status,
        limit=limit,
        offset=offset
    )
    
    # Преобразуем в DTOs (извлекаем invoice-specific поля из artifact.data)
    invoice_dtos = []
    
    for item in items:
        # Получаем artifact для извлечения invoice-specific полей
        artifact = artifact_service.get_artifact(item["id"]) if artifact_service else None
        data = artifact.get("data", {}) if artifact else {}
        
        # Извлекаем структурированные поля (не raw JSON)
        invoice_dto = InvoiceDTO(
            id=item["id"],
            kind=item["kind"],
            source=item["source"],
            tenant_id=item["tenant_id"],
            created_at=item["created_at"],
            state=item["state"],
            invoice_number=data.get("invoice_number") or data.get("number"),
            total_amount=data.get("total_amount") or data.get("total"),
            supplier_name=data.get("supplier", {}).get("name") if isinstance(data.get("supplier"), dict) else data.get("supplier_name"),
            date=data.get("date") or data.get("invoice_date")
        )
        invoice_dtos.append(invoice_dto)
    
    return InvoicesListResponse(
        items=invoice_dtos,
        total=len(invoice_dtos),  # TODO: добавить count query для точного total
        limit=limit,
        offset=offset
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailDTO)
async def get_invoice_detail(
    invoice_id: str,
    tenant_id: str = Depends(get_tenant_id),
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo),
    artifact_service = Depends(get_artifact_service)
):
    """
    Получить детальную информацию об инвойсе.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **invoice_id**: ID артефакта инвойса
    """
    
    use_case = GetInvoiceDetailUseCase(
        artifact_state_repo=artifact_state_repo,
        artifact_service=artifact_service
    )
    
    result = use_case.execute(tenant_id=tenant_id, artifact_id=invoice_id)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    
    return InvoiceDetailDTO(**result)


@router.post("/invoices/{invoice_id}/confirm", response_model=ConfirmInvoiceResponse)
async def confirm_invoice(
    invoice_id: str,
    tenant_id: str = Depends(get_tenant_id),
    _: None = Depends(require_roles("accountant", "system")),
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo)
):
    """
    Подтвердить инвойс (mark as confirmed).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **invoice_id**: ID артефакта инвойса
    """
    use_case = ConfirmInvoiceUseCase(artifact_state_repo=artifact_state_repo)
    success, error_message = use_case.execute(tenant_id=tenant_id, artifact_id=invoice_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=error_message or f"Invoice {invoice_id} not found")
    
    return ConfirmInvoiceResponse(
        success=True,
        artifact_id=invoice_id,
        message="Invoice confirmed"
    )


@router.post("/invoices/{invoice_id}/export", response_model=ExportInvoiceResponse)
async def export_invoice(
    invoice_id: str,
    export_request: ExportInvoiceRequest,
    tenant_id: str = Depends(get_tenant_id),
    _: None = Depends(require_roles("accountant", "system")),
    artifact_state_repo: ArtifactStateRepository = Depends(get_artifact_state_repo),
    export_repo: ExportRepository = Depends(get_export_repo)
):
    """
    Экспортировать инвойс (Excel/JSON/etc).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **invoice_id**: ID артефакта инвойса
    - **export_type**: Тип экспорта ('excel', 'json', '1c', etc.)
    - **export_config**: Конфигурация экспорта (опционально)
    """
    use_case = ExportInvoiceUseCase(
        artifact_state_repo=artifact_state_repo,
        export_repo=export_repo
    )
    
    success, export_id, file_id, error_message = use_case.execute(
        tenant_id=tenant_id,
        artifact_id=invoice_id,
        export_type=export_request.export_type,
        export_config=export_request.export_config
    )
    
    if not success:
        # Если export_id создан, но генерация не удалась - возвращаем ответ с ошибкой
        if export_id:
            return ExportInvoiceResponse(
                success=False,
                export_id=export_id,
                artifact_id=invoice_id,
                export_type=export_request.export_type,
                status="failed",
                file_id=None,
                download_url=None,
                message=error_message or "Export failed"
            )
        else:
            # Если не удалось создать export - 404
            raise HTTPException(status_code=404, detail=error_message or f"Invoice {invoice_id} not found")
    
    # Определяем финальный статус
    final_status = "completed" if success else "failed"
    
    return ExportInvoiceResponse(
        success=success,
        export_id=export_id,
        artifact_id=invoice_id,
        export_type=export_request.export_type,
        status=final_status,
        file_id=file_id,
        download_url=(f"/files/{file_id}" if file_id else None),
        message="Export completed" if success else (error_message or "Export failed"),
    )
