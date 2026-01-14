"""Inbox API endpoint for email ingestion status."""

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class InboxEmailItemDTO(BaseModel):
    """DTO для одного email item в inbox."""
    
    received_at: str  # ISO timestamp (created_at из job)
    from_email: str
    to_email: str
    subject: str
    attachment_filename: str
    attachment_size: int
    document_artifact_id: str
    job_id: str
    job_status: str  # queued|processing|done|failed|dead|none
    attempts: int
    next_run_at: Optional[str] = None
    invoice_artifact_id: Optional[str] = None
    error: Optional[str] = None


class InboxEmailListResponse(BaseModel):
    """Response для списка inbox emails."""
    
    items: List[InboxEmailItemDTO]
    cursor: Optional[str] = None  # Для pagination: "timestamp|job-id"


@router.get("/inbox/emails")
async def list_inbox_emails(
    request: Request,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    limit: int = 50,
    cursor: Optional[str] = None
):
    """
    Получить список последних входящих писем/вложений и их статусов обработки.
    
    Tenant-scoped: показывает только данные текущего tenant.
    
    Args:
        x_tenant_id: ID тенанта (обязательный заголовок)
        limit: Максимальное количество записей (default: 50)
        cursor: Cursor для pagination (опционально, формат: "timestamp|job-id")
        
    Returns:
        InboxEmailListResponse с списком items
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    # Получаем email_ocr_job_repo
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.repositories_sqlalchemy import EmailOcrJobRepositoryImpl
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        email_ocr_job_repo = EmailOcrJobRepositoryImpl(session=session)
        
        # Получаем jobs для inbox
        jobs = email_ocr_job_repo.list_inbox_emails(
            tenant_id=x_tenant_id,
            limit=limit,
            cursor=cursor
        )
        
        # Преобразуем jobs в DTO
        items = []
        for job in jobs:
            # Определяем job_status
            job_status = job.get("status", "none")
            
            item = InboxEmailItemDTO(
                received_at=job.get("created_at", ""),
                from_email=job.get("email_from", "") or "",
                to_email=job.get("email_to", "") or "",
                subject=job.get("email_subject", "") or "",
                attachment_filename=job.get("attachment_filename", "") or "",
                attachment_size=job.get("attachment_size", 0) or 0,
                document_artifact_id=job.get("document_artifact_id", ""),
                job_id=job.get("id", ""),
                job_status=job_status,
                attempts=job.get("attempts", 0),
                next_run_at=job.get("next_run_at"),
                invoice_artifact_id=job.get("invoice_artifact_id"),
                error=job.get("last_error")
            )
            items.append(item)
        
        # Cursor для следующей страницы (из последнего item)
        next_cursor = None
        if items and len(items) == limit:
            last_item = items[-1]
            next_cursor = f"{last_item.received_at}|{last_item.job_id}"
        
        return InboxEmailListResponse(
            items=items,
            cursor=next_cursor
        )
        
    finally:
        session.close()
