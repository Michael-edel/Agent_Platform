"""Use case for renewing Kaspi subscriptions (recurring billing)."""

import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import PaymentProvider, SubscriptionRepository
from cyberplat.billing.application.types import RecurringResult

logger = logging.getLogger(__name__)


class RenewKaspiSubscriptionsUseCase:
    """Use case для продления Kaspi подписок через recurring billing."""
    
    def __init__(
        self,
        provider: PaymentProvider,
        subscription_repo: SubscriptionRepository
    ):
        """
        Args:
            provider: PaymentProvider для списания средств (KaspiPaymentProvider)
            subscription_repo: Repository для работы с подписками
        """
        self.provider = provider
        self.subscription_repo = subscription_repo
    
    def execute(self) -> RecurringResult:
        """
        Выполнить recurring charge для активных Kaspi подписок.
        
        Returns:
            RecurringResult с результатами выполнения
        """
        now = datetime.now()
        period_end_threshold = (now + timedelta(days=7)).isoformat()
        
        # Получаем активные подписки для продления
        subscriptions = self.subscription_repo.get_active_subscriptions_for_renewal(
            provider="kaspi",
            period_end_threshold=period_end_threshold
        )
        
        charged = 0
        failed = 0
        skipped = 0
        errors = []
        
        for sub in subscriptions:
            tenant_id = sub["tenant_id"]
            plan_id = sub["plan_id"]
            subscription_id = sub["subscription_id"]
            kaspi_token = sub["payment_token"]
            amount_minor = sub["price_minor"]
            currency = sub["currency"]
            current_period_end = sub["current_period_end"]
            
            # Проверяем, не нужно ли списывать (период закончился или скоро закончится)
            if current_period_end:
                period_end_dt = datetime.fromisoformat(current_period_end)
                if period_end_dt > now + timedelta(days=1):
                    # Период еще не закончился, пропускаем
                    skipped += 1
                    continue
            
            try:
                logger.info(f"Списание для tenant={tenant_id}, plan={plan_id}, amount={amount_minor} {currency}")
                
                # Списываем средства с токена
                charge_result = self.provider.charge_token(
                    token=kaspi_token,
                    amount_minor=amount_minor,
                    currency=currency,
                    tenant_id=tenant_id,
                    plan_id=plan_id
                )
                
                if not charge_result or charge_result.get("status") != "success":
                    # Списание не удалось
                    failed += 1
                    error_msg = f"Failed to charge tenant {tenant_id}: charge_result={charge_result}"
                    errors.append(error_msg)
                    logger.error(error_msg)
                    
                    # Создаем заказ с статусом failed (для аудита)
                    external_order_id = charge_result.get("external_order_id") if charge_result else None
                    if not external_order_id:
                        external_order_id = f"kaspi_failed_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    try:
                        # Используем entitlement_service через subscription_repo для создания заказа
                        if hasattr(self.subscription_repo, 'entitlement_service'):
                            failed_order_id = self.subscription_repo.entitlement_service.create_order(
                                tenant_id=tenant_id,
                                provider="kaspi",
                                plan_id=plan_id,
                                amount_minor=amount_minor,
                                currency=currency,
                                external_order_id=external_order_id
                            )
                            self.subscription_repo.entitlement_service.update_order_status(
                                order_id=failed_order_id,
                                status="failed",
                                external_order_id=external_order_id
                            )
                    except Exception as e:
                        logger.error(f"Ошибка при создании failed заказа: {e}", exc_info=True)
                    
                    # Отменяем подписку и откатываем на free plan
                    try:
                        self.subscription_repo.apply_plan(
                            tenant_id=tenant_id,
                            plan_id="plan_free",
                            period_start=now.isoformat(),
                            period_end=(now + timedelta(days=30)).isoformat(),
                            provider="kaspi",
                            provider_customer_id=None,
                            provider_subscription_id=subscription_id,
                            status="canceled"
                        )
                        logger.info(f"Подписка {subscription_id} отменена из-за неудачного списания")
                    except Exception as e:
                        logger.error(f"Ошибка при отмене подписки {subscription_id}: {e}", exc_info=True)
                    
                    continue
                
                # Списание успешно - создаем заказ и обрабатываем
                external_order_id = charge_result.get("external_order_id")
                
                # ИДЕМПОТЕНТНОСТЬ: Проверяем только по external_order_id (после вызова charge_token)
                # Это предотвращает создание дублей при повторной обработке одного и того же external_order_id
                if external_order_id:
                    if hasattr(self.subscription_repo, 'entitlement_service'):
                        conn_check = self.subscription_repo.entitlement_service._get_connection()
                        cur_check = conn_check.cursor()
                        cur_check.execute("""
                            SELECT id, status
                            FROM billing_orders
                            WHERE external_order_id = ?
                              AND provider = 'kaspi'
                            LIMIT 1
                        """, (external_order_id,))
                        existing_order = cur_check.fetchone()
                        conn_check.close()
                        
                        if existing_order:
                            logger.info(
                                f"Идемпотентность: заказ с external_order_id={external_order_id} уже существует "
                                f"(id={existing_order['id']}, status={existing_order['status']}), пропускаем создание"
                            )
                            # Продлеваем подписку даже если заказ уже существует (безопасно)
                            period_start = now.isoformat()
                            period_end = (now + timedelta(days=30)).isoformat()
                            self.subscription_repo.apply_plan(
                                tenant_id=tenant_id,
                                plan_id=plan_id,
                                period_start=period_start,
                                period_end=period_end,
                                provider="kaspi",
                                provider_customer_id=None,
                                provider_subscription_id=subscription_id,
                                status="active"
                            )
                            skipped += 1
                            continue
                
                # Создаем заказ
                if hasattr(self.subscription_repo, 'entitlement_service'):
                    order_id = self.subscription_repo.entitlement_service.create_order(
                        tenant_id=tenant_id,
                        provider="kaspi",
                        plan_id=plan_id,
                        amount_minor=amount_minor,
                        currency=currency,
                        external_order_id=external_order_id
                    )
                    
                    # Обновляем статус заказа на paid
                    self.subscription_repo.entitlement_service.update_order_status(
                        order_id=order_id,
                        status="paid",
                        external_order_id=external_order_id
                    )
                
                # Применяем план (продлеваем подписку)
                period_start = now.isoformat()
                period_end = (now + timedelta(days=30)).isoformat()
                
                self.subscription_repo.apply_plan(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    period_start=period_start,
                    period_end=period_end,
                    provider="kaspi",
                    provider_customer_id=None,
                    provider_subscription_id=subscription_id,
                    status="active"
                )
                
                charged += 1
                order_id_str = order_id if 'order_id' in locals() else 'N/A'
                logger.info(f"Успешно списано для tenant={tenant_id}, order_id={order_id_str}")
                
            except Exception as e:
                failed += 1
                error_msg = f"Exception charging tenant {tenant_id}: {e}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
        
        result = RecurringResult(
            charged=charged,
            failed=failed,
            skipped=skipped,
            errors=errors
        )
        
        logger.info(f"Kaspi recurring billing завершен: charged={charged}, failed={failed}, skipped={skipped}")
        
        return result
