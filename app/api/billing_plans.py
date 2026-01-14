"""Billing plans API endpoints."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from cyberplat.product.infrastructure.database import get_db_session
from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
    PlanRepositoryImpl,
    TenantPlanRepositoryImpl
)
from cyberplat.product.domain.interfaces import PlanRepository, TenantPlanRepository
from cyberplat.product.application.assign_trial_use_case import AssignTrialUseCase
from app.api.admin_auth import admin_auth

logger = logging.getLogger(__name__)

router = APIRouter()


# Dependency injection для репозиториев
def get_plan_repo(db: Session = Depends(get_db_session)) -> PlanRepository:
    """Dependency для получения PlanRepository."""
    return PlanRepositoryImpl(session=db)


def get_tenant_plan_repo(db: Session = Depends(get_db_session)) -> TenantPlanRepository:
    """Dependency для получения TenantPlanRepository."""
    return TenantPlanRepositoryImpl(session=db)


# Dependency для получения tenant_id из header
def get_tenant_id(x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")) -> str:
    """Dependency для валидации и получения tenant_id."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID заголовок обязателен")
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(status_code=400, detail=f"Некорректный tenant_id: '{x_tenant_id}'")
    
    return x_tenant_id


# DTOs
class UpgradePlanRequest(BaseModel):
    plan_id: str


class UpgradePlanResponse(BaseModel):
    success: bool
    tenant_id: str
    plan_id: str
    message: str


@router.post("/billing/upgrade", response_model=UpgradePlanResponse)
async def upgrade_plan(
    request: UpgradePlanRequest,
    tenant_id: str = Depends(get_tenant_id),
    tenant_plan_repo: TenantPlanRepository = Depends(get_tenant_plan_repo),
    plan_repo: PlanRepository = Depends(get_plan_repo),
    _admin_auth: bool = Depends(admin_auth),
):
    """
    Обновить план tenant (upgrade).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **plan_id**: ID плана (pro, enterprise)
    
    ⚠️ DEV/DEMO ONLY: без реальных платежей, просто обновляет план.
    В production используйте Kaspi/Stripe checkout + webhook.
    """
    # Получаем event_service для эмиссии событий
    # В FastAPI можно получить Request через Depends, но для простоты используем глобальный
    from fastapi import Request as FastAPIRequest
    from starlette.requests import Request as StarletteRequest
    
    # Пытаемся получить из app.state через dependency injection
    # Для MVP: используем глобальный event_service если доступен
    event_service = None
    try:
        # В реальном приложении можно использовать Request dependency
        # Для MVP: используем глобальный event_service
        from app.main import event_service as global_event_service
        event_service = global_event_service
    except:
        pass
    
    # Валидируем, что план существует и активен
    plan = plan_repo.get_plan(request.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {request.plan_id} not found")
    
    if not plan.get("active"):
        raise HTTPException(status_code=400, detail=f"Plan {request.plan_id} is not active")
    
    # Обновляем план tenant (expires_at = null для paid планов)
    success = tenant_plan_repo.assign_plan(
        tenant_id=tenant_id,
        plan_id=request.plan_id,
        expires_at=None  # Paid планы не истекают
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to upgrade plan")
    
    # Эмитим событие
    if event_service:
        try:
            event_service.emit(
                event_type="billing.plan.upgraded",
                tenant_id=tenant_id,
                artifact_id=None,
                payload={
                    "plan_id": request.plan_id,
                    "plan_name": plan.get("name"),
                    "previous_plan_id": None  # Можно расширить для отслеживания предыдущего плана
                }
            )
        except Exception as e:
            logger.warning(f"Failed to emit billing.plan.upgraded event: {e}")
    
    logger.info(f"Plan upgraded for tenant {tenant_id}: {request.plan_id}")
    
    return UpgradePlanResponse(
        success=True,
        tenant_id=tenant_id,
        plan_id=request.plan_id,
        message=f"Plan upgraded to {plan.get('name')}"
    )
