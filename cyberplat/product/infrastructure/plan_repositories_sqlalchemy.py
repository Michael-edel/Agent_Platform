"""SQLAlchemy implementations для PlanRepository и TenantPlanRepository."""

import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_

from cyberplat.product.domain.interfaces import PlanRepository, TenantPlanRepository
from cyberplat.product.infrastructure.models import Plan, TenantPlan

logger = logging.getLogger(__name__)


class PlanRepositoryImpl(PlanRepository):
    """SQLAlchemy реализация PlanRepository."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Получить план по ID."""
        plan = self.session.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            return None
        
        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "quotas": json.loads(plan.quotas) if plan.quotas else {},
            "price_minor": plan.price_minor,
            "currency": plan.currency,
            "active": plan.active,
            "created_at": plan.created_at
        }
    
    def list_active_plans(self) -> List[Dict[str, Any]]:
        """Получить список активных планов."""
        plans = self.session.query(Plan).filter(Plan.active == True).all()
        
        return [
            {
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "quotas": json.loads(plan.quotas) if plan.quotas else {},
                "price_minor": plan.price_minor,
                "currency": plan.currency,
                "active": plan.active,
                "created_at": plan.created_at
            }
            for plan in plans
        ]


class TenantPlanRepositoryImpl(TenantPlanRepository):
    """SQLAlchemy реализация TenantPlanRepository."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_tenant_plan(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Получить план tenant."""
        tenant_plan = self.session.query(TenantPlan).filter(
            TenantPlan.tenant_id == tenant_id
        ).first()
        
        if not tenant_plan:
            return None
        
        return {
            "tenant_id": tenant_plan.tenant_id,
            "plan_id": tenant_plan.plan_id,
            "started_at": tenant_plan.started_at,
            "expires_at": tenant_plan.expires_at,
            "subscription_status": tenant_plan.subscription_status,
            "failed_charges": tenant_plan.failed_charges,
            "created_at": tenant_plan.created_at
        }
    
    def assign_plan(
        self,
        tenant_id: str,
        plan_id: str,
        expires_at: Optional[str] = None,
        subscription_status: Optional[str] = None,
        failed_charges: Optional[int] = None,
    ) -> bool:
        """Назначить план tenant (создать или обновить)."""
        try:
            now = datetime.now().isoformat()

            if subscription_status is None:
                subscription_status = "trial" if plan_id == "trial" else "active"
            if failed_charges is None:
                failed_charges = 0
            
            # Проверяем, существует ли уже запись
            existing = self.session.query(TenantPlan).filter(
                TenantPlan.tenant_id == tenant_id
            ).first()
            
            if existing:
                # Обновляем существующий план
                existing.plan_id = plan_id
                existing.started_at = now
                existing.expires_at = expires_at
                existing.subscription_status = subscription_status
                existing.failed_charges = failed_charges
            else:
                # Создаём новый
                tenant_plan = TenantPlan(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    started_at=now,
                    expires_at=expires_at,
                    subscription_status=subscription_status,
                    failed_charges=failed_charges,
                    created_at=now
                )
                self.session.add(tenant_plan)
            
            self.session.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to assign plan to tenant {tenant_id}: {e}", exc_info=True)
            self.session.rollback()
            return False
    
    def is_trial_expired(self, tenant_id: str) -> bool:
        """Проверить, истёк ли trial для tenant."""
        tenant_plan = self.session.query(TenantPlan).filter(
            TenantPlan.tenant_id == tenant_id
        ).first()
        
        if not tenant_plan:
            return False  # Нет плана = не trial
        
        # Если план не trial, то не истёк
        if tenant_plan.plan_id != "trial":
            return False
        
        # Если expires_at не установлен, то не истёк (не trial)
        if not tenant_plan.expires_at:
            return False
        
        # Проверяем, истёк ли срок
        try:
            expires_at = datetime.fromisoformat(tenant_plan.expires_at)
            now = datetime.now()
            return now > expires_at
        except Exception as e:
            logger.warning(f"Failed to parse expires_at for tenant {tenant_id}: {e}")
            return False

    def update_subscription_state(
        self,
        tenant_id: str,
        expires_at: Optional[str],
        subscription_status: str,
        failed_charges: int,
    ) -> bool:
        try:
            tp = self.session.query(TenantPlan).filter(TenantPlan.tenant_id == tenant_id).first()
            if not tp:
                return False
            tp.expires_at = expires_at
            tp.subscription_status = subscription_status
            tp.failed_charges = failed_charges
            self.session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update subscription state for tenant {tenant_id}: {e}", exc_info=True)
            self.session.rollback()
            return False

    def list_tenants_due_for_renewal(
        self,
        plan_ids: List[str],
        statuses: List[str],
        renew_before_iso: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        q = (
            self.session.query(TenantPlan)
            .filter(TenantPlan.plan_id.in_(plan_ids))
            .filter(TenantPlan.subscription_status.in_(statuses))
            .filter(
                (TenantPlan.expires_at == None)  # noqa: E711
                | (TenantPlan.expires_at <= renew_before_iso)
            )
            .order_by(TenantPlan.expires_at.asc().nullsfirst())
            .limit(limit)
        )
        res = []
        for tp in q.all():
            res.append(
                {
                    "tenant_id": tp.tenant_id,
                    "plan_id": tp.plan_id,
                    "started_at": tp.started_at,
                    "expires_at": tp.expires_at,
                    "subscription_status": tp.subscription_status,
                    "failed_charges": tp.failed_charges,
                }
            )
        return res
