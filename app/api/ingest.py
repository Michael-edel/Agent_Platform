"""Email ingestion API endpoint."""

import logging
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends

from app.api.admin_auth import admin_auth
from cyberplat.product.infrastructure.database import get_sessionmaker

# Импорты ниже нужны не для runtime логики endpoint,
# а для совместимости с unit-тестами, которые патчат эти атрибуты модуля.
from cyberplat.artifact_service import ArtifactService  # noqa: F401
from cyberplat.event_service import EventService  # noqa: F401
from cyberplat.agent_registry import AgentRegistry  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ingest/email")
async def ingest_email(
    request: Request,
    payload: Dict[str, Any]
):
    """
    Email ingestion endpoint.
    
    Принимает webhook от email provider (SendGrid/Mailgun/SES) с входящими email.
    Обрабатывает PDF вложения и создаёт document artifacts.
    
    Payload format (provider-agnostic):
    {
        "from": "sender@example.com",
        "to": "invoices+tenant-1@yourapp.ai",
        "subject": "Invoice",
        "attachments": [
            {
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
                "content": "base64_encoded_content",
                "size": 12345
            }
        ]
    }
    
    Tenant ID определяется из email адреса получателя:
    - Формат: invoices+tenant-1@yourapp.ai
    - Извлекается часть после + и до @
    
    Returns:
        {
            "success": bool,
            "tenant_id": str | None,
            "processed_attachments": int,
            "created_artifacts": List[str],
            "errors": List[str]
        }
    """
    # Получаем сервисы из app.state
    artifact_service = getattr(request.app.state, "artifact_service", None)
    event_service = getattr(request.app.state, "event_service", None)
    storage_service = getattr(request.app.state, "storage_service", None)
    
    if not artifact_service or not event_service or not storage_service:
        raise HTTPException(
            status_code=500,
            detail="Services not initialized"
        )

    # Получаем artifact_state_repo и email_ocr_job_repo
    from cyberplat.product.infrastructure.repositories_sqlalchemy import (
        ArtifactStateRepositoryImpl,
        EmailOcrJobRepositoryImpl
    )
    from sqlalchemy.orm import Session

    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()

    try:
        artifact_state_repo = ArtifactStateRepositoryImpl(session=session)
        email_ocr_job_repo = EmailOcrJobRepositoryImpl(session=session)

        # Создаём email parser
        from cyberplat.product.infrastructure.email_parser import EmailPayloadParser
        email_parser = EmailPayloadParser()

        # Создаём billing enforcement service (если доступен)
        billing_enforcement = None
        billing_service = getattr(request.app.state, "billing_service", None)
        entitlement_service = getattr(request.app.state, "entitlement_service", None)

        if billing_service and event_service:
            from cyberplat.product.application.billing_enforcement_service import BillingEnforcementService

            billing_enforcement = BillingEnforcementService(
                billing_service=billing_service,
                event_service=event_service,
                entitlement_service=entitlement_service,
            )

        # Создаём use case
        from cyberplat.product.application.email_ingest_use_case import EmailIngestUseCase

        use_case = EmailIngestUseCase(
            artifact_service=artifact_service,
            event_service=event_service,
            storage_service=storage_service,
            artifact_state_repo=artifact_state_repo,
            email_parser=email_parser,
            email_ocr_job_repo=email_ocr_job_repo,  # Для auto-OCR
            billing_enforcement=billing_enforcement,  # Для проверки квот
            max_attachment_size_mb=10,
        )

        # Выполняем use case
        result = use_case.execute(payload)
        
        # Если не удалось определить tenant_id, возвращаем 400
        if not result.get("tenant_id"):
            raise HTTPException(
                status_code=400,
                detail=result.get("errors", ["Could not resolve tenant_id from email address"])[0]
            )
        
        # Если есть ошибки, но хотя бы один artifact создан, возвращаем 207 (Multi-Status)
        if result.get("errors") and result.get("created_artifacts"):
            return {
                "status": "partial_success",
                **result
            }
        
        # Если успешно
        if result.get("success"):
            response = {
                "status": "success",
                **result
            }
            
            # MVP: best-effort попытка обработать один job синхронно (если auto-OCR включен)
            # Это не блокирует ingest, но может ускорить обработку
            import os
            auto_ocr_enabled = os.getenv("EMAIL_AUTO_OCR_ENABLED", "0").strip() == "1"
            if auto_ocr_enabled and result.get("created_artifacts"):
                try:
                    from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase
                    from cyberplat.agent_registry import AgentRegistry
                    
                    agent_registry = getattr(request.app.state, "agent_registry", None)
                    if agent_registry:
                        doc_agent = agent_registry.get("doc_agent")
                        if doc_agent:
                            process_use_case = ProcessEmailOcrJobsUseCase(
                                email_ocr_job_repo=email_ocr_job_repo,
                                artifact_state_repo=artifact_state_repo,
                                artifact_service=artifact_service,
                                doc_agent=doc_agent,
                                event_service=event_service
                            )
                            # Пытаемся обработать один job (best-effort)
                            dispatch_result = process_use_case.execute(limit=1)
                            if dispatch_result.get("succeeded", 0) > 0:
                                response["auto_ocr_dispatched"] = True
                                logger.info("Auto-OCR job dispatched immediately after email ingest")
                except Exception as dispatch_error:
                    # Не падаем, если dispatch не удался (job останется queued)
                    logger.warning(f"Best-effort OCR dispatch failed: {dispatch_error}")
            
            return response
        
        # Если полный провал
        raise HTTPException(
            status_code=400,
            detail="; ".join(result.get("errors", ["Email ingestion failed"]))
        )
        
    finally:
        session.close()


@router.post("/ingest/email/ocr/dispatch")
async def dispatch_email_ocr_jobs(
    request: Request,
    limit: int = 10,
    _admin_auth: bool = Depends(admin_auth)
):
    """
    Dispatch email OCR jobs for processing.
    
    Admin-only endpoint. Требует X-Admin-Key заголовок (если ADMIN_API_KEY задан).
    
    Этот endpoint должен вызываться периодически (например, через cron) для обработки
    email OCR jobs в очереди.
    
    Args:
        limit: Максимальное количество jobs для обработки за один вызов (default: 10)
        _admin_auth: Admin authentication (dependency)
        
    Returns:
        {
            "processed": int,
            "succeeded": int,
            "failed": int,
            "dead": int,
            "skipped_due_to_limit": int,
            "errors": List[str]
        }
    """
    artifact_service = getattr(request.app.state, "artifact_service", None)
    event_service = getattr(request.app.state, "event_service", None)
    agent_registry = getattr(request.app.state, "agent_registry", None)
    
    if not artifact_service or not event_service or not agent_registry:
        raise HTTPException(
            status_code=500,
            detail="Services not initialized"
        )
    
    doc_agent = agent_registry.get("doc_agent")
    if not doc_agent:
        raise HTTPException(
            status_code=500,
            detail="doc_agent not found"
        )
    
    # Получаем repositories
    from cyberplat.product.infrastructure.repositories_sqlalchemy import (
        ArtifactStateRepositoryImpl,
        EmailOcrJobRepositoryImpl
    )
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        artifact_state_repo = ArtifactStateRepositoryImpl(session=session)
        email_ocr_job_repo = EmailOcrJobRepositoryImpl(session=session)
        
        # Получаем параметры из env
        import os
        max_retries = int(os.getenv("EMAIL_AUTO_OCR_MAX_RETRIES", "5"))
        retry_base_seconds = int(os.getenv("EMAIL_AUTO_OCR_RETRY_BASE_SECONDS", "10"))
        retry_max_seconds = int(os.getenv("EMAIL_AUTO_OCR_RETRY_MAX_SECONDS", "600"))
        processing_timeout_seconds = int(os.getenv("EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS", "900"))
        max_concurrent_jobs = int(os.getenv("EMAIL_OCR_MAX_CONCURRENT_JOBS", "3"))
        
        # Создаём billing enforcement service (если доступен)
        billing_enforcement = None
        billing_service = getattr(request.app.state, "billing_service", None)
        entitlement_service = getattr(request.app.state, "entitlement_service", None)
        
        if billing_service and event_service:
            from cyberplat.product.application.billing_enforcement_service import BillingEnforcementService
            billing_enforcement = BillingEnforcementService(
                billing_service=billing_service,
                event_service=event_service,
                entitlement_service=entitlement_service
            )
        
        # Создаём use case
        from cyberplat.product.application.process_email_ocr_jobs_use_case import ProcessEmailOcrJobsUseCase
        use_case = ProcessEmailOcrJobsUseCase(
            email_ocr_job_repo=email_ocr_job_repo,
            artifact_state_repo=artifact_state_repo,
            artifact_service=artifact_service,
            doc_agent=doc_agent,
            event_service=event_service,
            billing_enforcement=billing_enforcement,  # Для проверки квот
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
            processing_timeout_seconds=processing_timeout_seconds,
            max_concurrent_jobs=max_concurrent_jobs
        )
        
        # Выполняем use case
        result = use_case.execute(limit=limit)
        
        return result
        
    finally:
        session.close()
