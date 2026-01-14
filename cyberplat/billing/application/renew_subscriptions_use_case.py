"""General use case for renewing subscriptions (recurring billing) across providers."""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import PaymentProvider, SubscriptionRepository
from cyberplat.billing.application.types import RecurringResult

logger = logging.getLogger(__name__)


class RenewSubscriptionsUseCase:
    """Общий use case для продления подписок через recurring billing (Kaspi, Stripe, etc)."""
    
    def __init__(
        self,
        provider_name: str,
        subscription_repo: SubscriptionRepository,
        kaspi_provider: Optional[PaymentProvider] = None,
        stripe_provider: Optional[PaymentProvider] = None,
        as_of: Optional[datetime] = None
    ):
        """
        Args:
            provider_name: Имя провайдера ("kaspi", "stripe", etc)
            subscription_repo: Repository для работы с подписками
            kaspi_provider: PaymentProvider для Kaspi (опционально, требуется для provider="kaspi")
            stripe_provider: PaymentProvider для Stripe (опционально, для будущего использования)
            as_of: Дата/время для детерминированности в тестах (опционально, по умолчанию datetime.now())
        """
        self.provider_name = provider_name
        self.subscription_repo = subscription_repo
        self.kaspi_provider = kaspi_provider
        self.stripe_provider = stripe_provider
        self.as_of = as_of
    
    def execute(self) -> RecurringResult:
        """
        Выполнить recurring charge для активных подписок указанного провайдера.
        
        Returns:
            RecurringResult с результатами выполнения
        """
        if self.provider_name == "kaspi":
            return self._execute_kaspi()
        elif self.provider_name == "stripe":
            return self._execute_stripe()
        else:
            raise ValueError(f"Unsupported provider: {self.provider_name}")
    
    def _execute_kaspi(self) -> RecurringResult:
        """Выполнить recurring charge для Kaspi подписок."""
        if not self.kaspi_provider:
            raise ValueError("kaspi_provider is required for provider='kaspi'")
        
        # Делегируем в существующий Kaspi use case для сохранения поведения
        from cyberplat.billing.application.renew_kaspi_subscriptions_use_case import RenewKaspiSubscriptionsUseCase
        
        kaspi_use_case = RenewKaspiSubscriptionsUseCase(
            provider=self.kaspi_provider,
            subscription_repo=self.subscription_repo
        )
        
        # Вызываем execute() - поведение полностью сохраняется
        return kaspi_use_case.execute()
    
    def _execute_stripe(self) -> RecurringResult:
        """
        Выполнить recurring sync для Stripe подписок.
        
        Stripe сам списывает деньги через Subscriptions API.
        Наша задача - синхронизировать период подписки и обработать cancel/downgrade.
        """
        now = self.as_of if self.as_of else datetime.now()
        period_end_threshold = (now + timedelta(days=7)).isoformat()
        
        # Получаем активные Stripe подписки для синхронизации
        subscriptions = self.subscription_repo.get_active_subscriptions_for_renewal(
            provider="stripe",
            period_end_threshold=period_end_threshold
        )
        
        charged = 0
        failed = 0
        skipped = 0
        errors = []
        
        # Получаем Stripe клиент
        from cyberplat.stripe_client import get_stripe_client
        stripe = get_stripe_client()
        
        if not stripe:
            logger.warning("Stripe client not available, skipping Stripe recurring sync")
            return RecurringResult(
                charged=0,
                failed=0,
                skipped=0,
                errors=["Stripe client not available"]
            )
        
        for sub in subscriptions:
            tenant_id = sub["tenant_id"]
            plan_id = sub["plan_id"]
            subscription_id = sub["subscription_id"]
            stripe_subscription_id = sub.get("provider_subscription_id")
            current_period_end = sub.get("current_period_end")
            
            if not stripe_subscription_id:
                logger.warning(f"Stripe subscription {subscription_id} missing provider_subscription_id, skipping")
                skipped += 1
                continue
            
            # Проверяем, не нужно ли синхронизировать (период закончился или скоро закончится)
            if current_period_end:
                period_end_dt = datetime.fromisoformat(current_period_end)
                if period_end_dt > now + timedelta(days=1):
                    # Период еще не закончился, пропускаем
                    skipped += 1
                    continue
            
            try:
                logger.info(f"Синхронизация Stripe подписки для tenant={tenant_id}, subscription={stripe_subscription_id}")
                
                # Получаем актуальную информацию о подписке из Stripe
                try:
                    stripe_subscription = stripe.Subscription.retrieve(stripe_subscription_id)
                except Exception as e:
                    error_msg = f"Failed to retrieve Stripe subscription {stripe_subscription_id}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    failed += 1
                    continue
                
                stripe_status = stripe_subscription.status
                stripe_period_start = stripe_subscription.current_period_start
                stripe_period_end = stripe_subscription.current_period_end
                stripe_customer_id = stripe_subscription.customer
                
                # Конвертируем Unix timestamp в ISO string
                if stripe_period_start:
                    period_start_iso = datetime.fromtimestamp(stripe_period_start).isoformat()
                else:
                    period_start_iso = now.isoformat()
                
                if stripe_period_end:
                    period_end_iso = datetime.fromtimestamp(stripe_period_end).isoformat()
                else:
                    period_end_iso = (now + timedelta(days=30)).isoformat()
                
                # Проверяем статус подписки
                if stripe_status in ["canceled", "past_due", "unpaid", "incomplete_expired"]:
                    # Подписка отменена или не оплачена - downgrade на free plan
                    logger.info(f"Stripe subscription {stripe_subscription_id} имеет статус {stripe_status}, downgrade на free plan")
                    
                    try:
                        self.subscription_repo.apply_plan(
                            tenant_id=tenant_id,
                            plan_id="plan_free",
                            period_start=period_start_iso,
                            period_end=period_end_iso,
                            provider="stripe",
                            provider_customer_id=stripe_customer_id,
                            provider_subscription_id=stripe_subscription_id,
                            status="canceled"
                        )
                        failed += 1
                        logger.info(f"Подписка {subscription_id} отменена из-за статуса {stripe_status}")
                    except Exception as e:
                        error_msg = f"Ошибка при отмене подписки {subscription_id}: {e}"
                        errors.append(error_msg)
                        logger.error(error_msg, exc_info=True)
                        failed += 1
                    
                    continue
                
                if stripe_status == "active":
                    # Подписка активна - синхронизируем период
                    # Проверяем, изменился ли период (Stripe мог продлить)
                    needs_sync = False
                    if not current_period_end:
                        needs_sync = True
                    else:
                        current_period_end_dt = datetime.fromisoformat(current_period_end)
                        stripe_period_end_dt = datetime.fromtimestamp(stripe_period_end)
                        # Синхронизируем если период в Stripe отличается более чем на 1 день
                        if abs((stripe_period_end_dt - current_period_end_dt).total_seconds()) > 86400:
                            needs_sync = True
                    
                    if needs_sync:
                        try:
                            self.subscription_repo.apply_plan(
                                tenant_id=tenant_id,
                                plan_id=plan_id,
                                period_start=period_start_iso,
                                period_end=period_end_iso,
                                provider="stripe",
                                provider_customer_id=stripe_customer_id,
                                provider_subscription_id=stripe_subscription_id,
                                status="active"
                            )
                            charged += 1
                            logger.info(f"Синхронизирован период для tenant={tenant_id}, subscription={stripe_subscription_id}")
                        except Exception as e:
                            error_msg = f"Ошибка при синхронизации подписки {subscription_id}: {e}"
                            errors.append(error_msg)
                            logger.error(error_msg, exc_info=True)
                            failed += 1
                    else:
                        # Период уже синхронизирован
                        skipped += 1
                else:
                    # Неожиданный статус
                    logger.warning(f"Stripe subscription {stripe_subscription_id} имеет неожиданный статус: {stripe_status}")
                    skipped += 1
                
            except Exception as e:
                failed += 1
                error_msg = f"Exception syncing Stripe subscription for tenant {tenant_id}: {e}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
        
        result = RecurringResult(
            charged=charged,
            failed=failed,
            skipped=skipped,
            errors=errors
        )
        
        logger.info(f"Stripe recurring sync завершен: charged={charged}, failed={failed}, skipped={skipped}")
        
        return result
