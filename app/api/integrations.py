"""API роутер для работы с интеграциями."""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Query
from pydantic import BaseModel

from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.onec_client import OneCClient, OneCAuthError, OneCTransportError
from cyberplat.integrations.integration_job_service import IntegrationJobService

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
    settings_service: OneCSettingsService = Depends(get_settings_service)
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
    settings_service: OneCSettingsService = Depends(get_settings_service)
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
