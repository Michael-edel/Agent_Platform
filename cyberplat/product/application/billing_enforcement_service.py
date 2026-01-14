"""Billing enforcement service для проверки квот перед платными операциями."""

import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Исключение при превышении квоты."""
    
    def __init__(
        self,
        metric: str,
        period: str,
        used_units: float,
        monthly_quota: int,
        operation: str,
        upgrade_url: Optional[str] = None
    ):
        self.metric = metric
        self.period = period
        self.used_units = used_units
        self.monthly_quota = monthly_quota
        self.operation = operation
        self.upgrade_url = upgrade_url
        
        super().__init__(
            f"Quota exceeded for {metric}: used={used_units}, quota={monthly_quota}"
        )


class TrialExpiredError(Exception):
    """Исключение при истёкшем trial."""
    
    def __init__(self, tenant_id: str, expires_at: str):
        self.tenant_id = tenant_id
        self.expires_at = expires_at
        super().__init__(f"Trial expired for tenant {tenant_id}")


class SubscriptionPastDueError(Exception):
    """Исключение при просроченной оплате (past_due)."""

    def __init__(self, tenant_id: str, expires_at: Optional[str] = None, failed_charges: Optional[int] = None):
        self.tenant_id = tenant_id
        self.expires_at = expires_at
        self.failed_charges = failed_charges
        super().__init__(f"Subscription past due for tenant {tenant_id}")


class SubscriptionCanceledError(Exception):
    """Исключение при отмененной подписке (canceled)."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        super().__init__(f"Subscription canceled for tenant {tenant_id}")


class BillingEnforcementService:
    """Сервис для enforcement квот (paywall)."""
    
    def __init__(
        self,
        billing_service,  # BillingService
        event_service,  # EventService
        tenant_plan_repo=None,  # TenantPlanRepository (опционально, для планов)
        plan_repo=None,  # PlanRepository (опционально, для планов)
        entitlement_service=None  # EntitlementService (опционально, для upgrade_url)
    ):
        self.billing_service = billing_service
        self.event_service = event_service
        self.tenant_plan_repo = tenant_plan_repo
        self.plan_repo = plan_repo
        self.entitlement_service = entitlement_service
        
        # Feature flags
        self.enabled = os.getenv("BILLING_ENFORCEMENT_ENABLED", "0").strip() == "1"
        self.mode = os.getenv("BILLING_ENFORCEMENT_MODE", "block").strip()  # block | warn
        self.period_source = os.getenv("BILLING_PERIOD_SOURCE", "now").strip()  # now
    
    def enforce(
        self,
        tenant_id: str,
        required_metrics: Dict[str, float],
        operation_name: str,
        period: Optional[str] = None
    ) -> None:
        """
        Проверить квоты перед выполнением платной операции.
        
        Args:
            tenant_id: ID тенанта
            required_metrics: Словарь {metric_name: units} - требуемые метрики
            operation_name: Название операции (для логирования)
            period: Период (YYYY-MM). Если None, используется текущий период
            
        Raises:
            QuotaExceededError: Если квота превышена и mode=block
            TrialExpiredError: Если trial истёк
        """
        # Если enforcement выключен, ничего не делаем
        if not self.enabled:
            logger.debug(f"Billing enforcement disabled, allowing operation: {operation_name}")
            return
        
        # Проверяем, истёк ли trial (если используется система планов)
        if self.tenant_plan_repo:
            tenant_plan = self.tenant_plan_repo.get_tenant_plan(tenant_id)
            if tenant_plan:
                status = tenant_plan.get("subscription_status") or ("trial" if tenant_plan.get("plan_id") == "trial" else "active")
                expires_at = tenant_plan.get("expires_at")
                failed_charges = tenant_plan.get("failed_charges")

                # canceled always blocks paid operations
                if status == "canceled":
                    raise SubscriptionCanceledError(tenant_id=tenant_id)

                # past_due blocks paid operations
                # also treat expired paid subscription as past_due (until cron renews or cancels)
                if status == "past_due":
                    raise SubscriptionPastDueError(
                        tenant_id=tenant_id, expires_at=expires_at, failed_charges=failed_charges
                    )
                if tenant_plan.get("plan_id") in ("pro", "enterprise") and expires_at:
                    try:
                        if datetime.now() > datetime.fromisoformat(expires_at):
                            raise SubscriptionPastDueError(
                                tenant_id=tenant_id, expires_at=expires_at, failed_charges=failed_charges
                            )
                    except ValueError:
                        pass

            if self.tenant_plan_repo.is_trial_expired(tenant_id):
                # Trial истёк - всегда блокируем
                expires_at = tenant_plan.get("expires_at") if tenant_plan else None
                
                # Эмитим событие
                try:
                    self.event_service.emit(
                        event_type="billing.trial.expired",
                        tenant_id=tenant_id,
                        artifact_id=None,
                        payload={
                            "expires_at": expires_at,
                            "operation": operation_name
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit billing.trial.expired event: {e}")
                
                # Всегда блокируем при истёкшем trial
                raise TrialExpiredError(tenant_id=tenant_id, expires_at=expires_at or "")
        
        # Определяем период
        if not period:
            if self.period_source == "now":
                now = datetime.now()
                period = f"{now.year}-{now.month:02d}"
            else:
                raise ValueError(f"Unknown BILLING_PERIOD_SOURCE: {self.period_source}")
        
        # Получаем статус квот (из планов или из billing_rates)
        quota_status = self._get_quota_status(tenant_id=tenant_id, period=period)
        
        # Проверяем каждую требуемую метрику
        exceeded_metrics = []
        
        for metric_name, required_units in required_metrics.items():
            metric_status = quota_status.get("metrics", {}).get(metric_name)
            
            if not metric_status:
                # Нет квоты для этой метрики - разрешаем
                logger.debug(
                    f"No quota for metric {metric_name}, allowing operation: {operation_name}"
                )
                continue
            
            used_units = metric_status.get("used_units", 0.0)
            monthly_quota = metric_status.get("monthly_quota")
            
            # Если квота не установлена (None) - разрешаем (unlimited)
            if monthly_quota is None:
                logger.debug(
                    f"No monthly_quota for metric {metric_name} (unlimited), allowing operation: {operation_name}"
                )
                continue
            
            # Проверяем, не превысит ли операция квоту
            if used_units + required_units > monthly_quota:
                exceeded_metrics.append({
                    "metric": metric_name,
                    "used_units": used_units,
                    "required_units": required_units,
                    "monthly_quota": monthly_quota,
                    "would_exceed": used_units + required_units
                })
        
        # Если есть превышенные квоты
        if exceeded_metrics:
            # Получаем upgrade_url (если доступно)
            upgrade_url = None
            if self.entitlement_service:
                try:
                    # Пытаемся получить upgrade URL из entitlement service
                    # В MVP: просто None, можно расширить позже
                    pass
                except Exception:
                    pass
            
            # Формируем информацию о первой превышенной метрике (для ошибки)
            first_exceeded = exceeded_metrics[0]
            
            # Логируем
            logger.warning(
                f"billing_enforcement_blocked",
                extra={
                    "tenant_id": tenant_id,
                    "metric": first_exceeded["metric"],
                    "period": period,
                    "operation": operation_name,
                    "used_units": first_exceeded["used_units"],
                    "required_units": first_exceeded["required_units"],
                    "monthly_quota": first_exceeded["monthly_quota"]
                }
            )
            
            # Эмитим событие
            try:
                self.event_service.emit(
                    event_type="billing.quota.exceeded",
                    tenant_id=tenant_id,
                    artifact_id=None,
                    payload={
                        "metric": first_exceeded["metric"],
                        "period": period,
                        "used_units": first_exceeded["used_units"],
                        "required_units": first_exceeded["required_units"],
                        "monthly_quota": first_exceeded["monthly_quota"],
                        "operation": operation_name,
                        "all_exceeded_metrics": exceeded_metrics
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to emit billing.quota.exceeded event: {e}")
            
            # В зависимости от mode
            if self.mode == "block":
                # Блокируем операцию
                raise QuotaExceededError(
                    metric=first_exceeded["metric"],
                    period=period,
                    used_units=first_exceeded["used_units"],
                    monthly_quota=first_exceeded["monthly_quota"],
                    operation=operation_name,
                    upgrade_url=upgrade_url
                )
            elif self.mode == "warn":
                # Только предупреждаем, не блокируем
                logger.warning(
                    f"Quota exceeded for {first_exceeded['metric']} "
                    f"(used={first_exceeded['used_units']}, quota={first_exceeded['monthly_quota']}), "
                    f"but mode=warn, allowing operation: {operation_name}"
                )
                # Продолжаем выполнение
            else:
                logger.error(f"Unknown BILLING_ENFORCEMENT_MODE: {self.mode}, defaulting to block")
                raise QuotaExceededError(
                    metric=first_exceeded["metric"],
                    period=period,
                    used_units=first_exceeded["used_units"],
                    monthly_quota=first_exceeded["monthly_quota"],
                    operation=operation_name,
                    upgrade_url=upgrade_url
                )
    
    def _get_quota_status(self, tenant_id: str, period: str) -> Dict[str, Any]:
        """
        Получить статус квот для tenant (из планов или из billing_rates).
        
        Приоритет: планы > billing_rates
        """
        # Если используется система планов, получаем квоты из плана
        if self.tenant_plan_repo and self.plan_repo:
            tenant_plan = self.tenant_plan_repo.get_tenant_plan(tenant_id)
            if tenant_plan:
                plan = self.plan_repo.get_plan(tenant_plan["plan_id"])
                if plan:
                    # Получаем usage из billing_service
                    usage = self.billing_service.get_usage(tenant_id=tenant_id, period=period)
                    
                    # Формируем quota_status из плана
                    plan_quotas = plan.get("quotas", {})
                    metrics_result = {}
                    currency = plan.get("currency") or "USD"
                    
                    # Для каждой метрики из плана
                    for metric_name, monthly_quota in plan_quotas.items():
                        # Получаем used_units из usage
                        used_units = 0.0
                        for line in usage.get("lines", []):
                            if line.get("metric") == metric_name:
                                used_units += line.get("units", 0.0)
                        
                        # Вычисляем remaining_units
                        if monthly_quota is None:
                            remaining_units = None
                        else:
                            remaining_units = max(0, int(monthly_quota - used_units))
                        
                        # is_exceeded
                        is_exceeded = monthly_quota is not None and used_units >= monthly_quota
                        
                        metrics_result[metric_name] = {
                            "used_units": used_units,
                            "monthly_quota": monthly_quota,
                            "remaining_units": remaining_units,
                            "is_exceeded": is_exceeded,
                            "unit_price_minor": 0,  # Из плана не берём цену
                            "amount_minor": 0
                        }
                    
                    return {
                        "tenant_id": tenant_id,
                        "period": period,
                        "currency": currency,
                        "metrics": metrics_result
                    }
        
        # Fallback на billing_rates (старая система)
        return self.billing_service.get_quota_status(tenant_id=tenant_id, period=period)
