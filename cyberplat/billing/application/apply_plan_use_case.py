"""Use case: Apply plan to tenant."""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import SubscriptionRepository

logger = logging.getLogger(__name__)


class ApplyPlanUseCase:
    """Use case для применения плана к тенанту."""
    
    def __init__(self, subscription_repo: SubscriptionRepository):
        self.subscription_repo = subscription_repo
    
    def execute(
        self,
        tenant_id: str,
        plan_id: str,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
        provider: str = "stripe",
        provider_customer_id: Optional[str] = None,
        provider_subscription_id: Optional[str] = None,
        status: str = "active"
    ) -> None:
        """
        Применить план к тенанту.
        
        Args:
            tenant_id: ID тенанта
            plan_id: ID плана
            period_start: Начало периода (ISO format). Если None, используется текущее время.
            period_end: Конец периода (ISO format). Если None, вычисляется period_start + 30 дней.
            provider: Провайдер платежей (stripe, kaspi)
            provider_customer_id: ID клиента у провайдера
            provider_subscription_id: ID подписки у провайдера
            status: Статус подписки (active, canceled, etc)
        """
        # Вычисляем период если не указан
        if not period_start:
            period_start = datetime.now().isoformat()
        
        if not period_end:
            period_start_dt = datetime.fromisoformat(period_start)
            period_end = (period_start_dt + timedelta(days=30)).isoformat()
        
        # Применяем план через репозиторий
        self.subscription_repo.apply_plan(
            tenant_id=tenant_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            provider=provider,
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            status=status
        )
        
        logger.info(
            f"Plan applied: tenant={tenant_id}, plan={plan_id}, "
            f"provider={provider}, status={status}"
        )
