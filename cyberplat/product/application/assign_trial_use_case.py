"""Use case для автоматического назначения trial плана при первом использовании tenant."""

import logging
import os
from typing import Optional
from datetime import datetime, timedelta

from cyberplat.product.domain.interfaces import TenantPlanRepository, PlanRepository

logger = logging.getLogger(__name__)


class AssignTrialUseCase:
    """Use case для назначения trial плана."""
    
    def __init__(
        self,
        tenant_plan_repo: TenantPlanRepository,
        plan_repo: PlanRepository
    ):
        self.tenant_plan_repo = tenant_plan_repo
        self.plan_repo = plan_repo
        
        # Feature flag
        self.trial_days = int(os.getenv("TRIAL_DAYS", "14"))
    
    def execute(self, tenant_id: str) -> bool:
        """
        Назначить trial план tenant (если ещё не назначен).
        
        Args:
            tenant_id: ID tenant
            
        Returns:
            True если trial назначен (или уже был назначен), False при ошибке
        """
        # Проверяем, есть ли уже план у tenant
        existing_plan = self.tenant_plan_repo.get_tenant_plan(tenant_id)
        if existing_plan:
            # План уже назначен
            logger.debug(f"Tenant {tenant_id} already has plan: {existing_plan['plan_id']}")
            return True
        
        # Проверяем, что trial план существует
        trial_plan = self.plan_repo.get_plan("trial")
        if not trial_plan:
            logger.error("Trial plan not found in database")
            return False
        
        if not trial_plan.get("active"):
            logger.warning("Trial plan is not active")
            return False
        
        # Вычисляем expires_at
        now = datetime.now()
        expires_at = now + timedelta(days=self.trial_days)
        
        # Назначаем trial план
        success = self.tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="trial",
            expires_at=expires_at.isoformat()
        )
        
        if success:
            logger.info(
                f"Trial plan assigned to tenant {tenant_id}, expires_at={expires_at.isoformat()}"
            )
        else:
            logger.error(f"Failed to assign trial plan to tenant {tenant_id}")
        
        return success
