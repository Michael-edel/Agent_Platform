"""Stripe-specific webhook event handlers (application logic extracted from webhook handler)."""

import logging
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta

from cyberplat.billing.domain.interfaces import SubscriptionRepository
from cyberplat.product.infrastructure.database import get_engine
from cyberplat.product.infrastructure.models import BillingJob, UsageInvoice
from sqlalchemy import select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def handle_stripe_checkout_completed(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Stripe checkout.session.completed событие.
    
    Args:
        event: Stripe event object (полный event, из которого извлекается data.object)
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    """
    # Извлекаем session из event.data.object
    session = event.get("data", {}).get("object", {})
    
    # Извлекаем metadata
    metadata = session.get("metadata", {})
    tenant_id = metadata.get("tenant_id")
    plan_id = metadata.get("plan_id") or metadata.get("target_plan_id")
    
    if not tenant_id:
        logger.warning("Stripe checkout.session.completed missing tenant_id in metadata")
        return False, None, "Missing tenant_id in metadata"
    
    if not plan_id:
        logger.warning("Stripe checkout.session.completed missing plan_id in metadata")
        return False, None, "Missing plan_id in metadata"
    
    # Сохраняем customer_id если есть (через entitlement_service если доступен)
    customer_id = session.get("customer")
    if customer_id and hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.upsert_payment_profile(
                tenant_id=tenant_id,
                stripe_customer_id=customer_id
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
    
    # Для checkout.session.completed используем subscription если есть
    subscription_id = session.get("subscription")
    
    if subscription_id:
        # Если есть subscription, ждем invoice.paid или subscription.updated
        logger.info(f"Checkout completed для tenant {tenant_id}, subscription={subscription_id}, ждем invoice.paid")
        return True, tenant_id, None
    
    # Если нет subscription (one-time payment), применяем план сразу
    now = datetime.now()
    period_start = now.isoformat()
    period_end = (now + timedelta(days=30)).isoformat()
    
    try:
        subscription_repo.apply_plan(
            tenant_id=tenant_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            provider="stripe",
            provider_customer_id=customer_id,
            provider_subscription_id=None,
            status="active"
        )
        return True, tenant_id, None
    except Exception as e:
        logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
        return False, tenant_id, str(e)


def handle_stripe_invoice_paid(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Stripe invoice.paid событие.
    
    Args:
        event: Stripe event object (полный event, из которого извлекается data.object)
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    """
    # Извлекаем invoice из event.data.object
    invoice = event.get("data", {}).get("object", {})
    
    # Извлекаем metadata из invoice или subscription
    metadata = invoice.get("metadata", {})
    tenant_id = metadata.get("tenant_id")
    plan_id = metadata.get("plan_id") or metadata.get("target_plan_id")
    
    if not tenant_id:
        logger.warning("Stripe invoice.paid missing tenant_id in metadata")
        return False, None, "Missing tenant_id in metadata"
    
    if not plan_id:
        logger.warning("Stripe invoice.paid missing plan_id in metadata")
        return False, None, "Missing plan_id in metadata"
    
    subscription_id = invoice.get("subscription")
    customer_id = invoice.get("customer")
    period_start = invoice.get("period_start")
    period_end = invoice.get("period_end")
    
    # Сохраняем customer_id если есть (через entitlement_service если доступен)
    if customer_id and hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.upsert_payment_profile(
                tenant_id=tenant_id,
                stripe_customer_id=customer_id
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
    
    # Конвертируем Unix timestamp в ISO string
    if period_start:
        period_start = datetime.fromtimestamp(period_start).isoformat()
    else:
        period_start = datetime.now().isoformat()
    
    if period_end:
        period_end = datetime.fromtimestamp(period_end).isoformat()
    else:
        period_end = (datetime.now() + timedelta(days=30)).isoformat()
    
    try:
        subscription_repo.apply_plan(
            tenant_id=tenant_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            provider="stripe",
            provider_customer_id=customer_id,
            provider_subscription_id=subscription_id,
            status="active"
        )
        return True, tenant_id, None
    except Exception as e:
        logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
        return False, tenant_id, str(e)


def handle_stripe_subscription_updated(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Обработать Stripe customer.subscription.updated или deleted событие.
    
    Args:
        event: Stripe event object (with data.object = subscription)
        subscription_repo: Repository для работы с подписками
        
    Returns:
        (success, tenant_id, error_message)
    """
    subscription = event.get("data", {}).get("object", {})
    status = subscription.get("status")
    subscription_id = subscription.get("id")
    customer_id = subscription.get("customer")
    
    # Извлекаем metadata из subscription
    metadata = subscription.get("metadata", {})
    tenant_id = metadata.get("tenant_id")
    plan_id = metadata.get("plan_id") or metadata.get("target_plan_id")
    
    if not tenant_id:
        logger.warning("Stripe subscription.updated missing tenant_id in metadata")
        return False, None, "Missing tenant_id in metadata"
    
    if not plan_id:
        logger.warning("Stripe subscription.updated missing plan_id in metadata")
        return False, None, "Missing plan_id in metadata"
    
    # Сохраняем customer_id если есть (через entitlement_service если доступен)
    if customer_id and hasattr(subscription_repo, 'entitlement_service'):
        try:
            subscription_repo.entitlement_service.upsert_payment_profile(
                tenant_id=tenant_id,
                stripe_customer_id=customer_id
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении payment profile: {e}", exc_info=True)
    
    current_period_start = subscription.get("current_period_start")
    current_period_end = subscription.get("current_period_end")
    
    # Конвертируем Unix timestamp в ISO string
    if current_period_start:
        period_start = datetime.fromtimestamp(current_period_start).isoformat()
    else:
        period_start = datetime.now().isoformat()
    
    if current_period_end:
        period_end = datetime.fromtimestamp(current_period_end).isoformat()
    else:
        period_end = (datetime.now() + timedelta(days=30)).isoformat()
    
    # Если subscription canceled или past_due - откатываем на free plan
    if status in ["canceled", "past_due", "unpaid"]:
        plan_id = "plan_free"
        logger.info(f"Subscription {subscription_id} имеет статус {status}, откатываем на free plan")
    
    try:
        subscription_repo.apply_plan(
            tenant_id=tenant_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            provider="stripe",
            provider_customer_id=customer_id,
            provider_subscription_id=subscription_id,
            status=status
        )
        return True, tenant_id, None
    except Exception as e:
        logger.error(f"Ошибка при применении плана: {e}", exc_info=True)
        return False, tenant_id, str(e)


def create_stripe_event_handlers() -> Dict[str, callable]:
    """
    Создать словарь обработчиков Stripe событий.
    
    Returns:
        Dict mapping event_type -> handler function
    """
    return {
        "checkout.session.completed": handle_stripe_checkout_completed,
        "invoice.paid": handle_stripe_invoice_paid,
        "customer.subscription.updated": handle_stripe_subscription_updated,
        "customer.subscription.deleted": handle_stripe_subscription_updated,
        # Usage invoice billing jobs reconciliation (payment_intent.*)
        "payment_intent.succeeded": handle_stripe_payment_intent_succeeded,
        "payment_intent.payment_failed": handle_stripe_payment_intent_failed,
    }


def _reconcile_usage_invoice_by_provider_ref(
    provider_ref: str,
    *,
    payment_status: str,
) -> Optional[str]:
    """
    Find billing job by provider_ref and update usage_invoices.payment_status.
    Returns tenant_id if invoice was found, else None.
    """
    if not provider_ref:
        return None
    engine = get_engine()
    with Session(engine) as session:
        job = session.execute(
            select(BillingJob).where(BillingJob.provider == "stripe", BillingJob.provider_ref == provider_ref)
        ).scalar_one_or_none()
        if not job:
            return None
        inv = session.execute(select(UsageInvoice).where(UsageInvoice.id == job.invoice_id)).scalar_one_or_none()
        if not inv:
            return None
        # Idempotent update: set only if different
        if getattr(inv, "payment_status", None) != payment_status:
            session.execute(
                update(UsageInvoice)
                .where(UsageInvoice.id == inv.id)
                .where(UsageInvoice.tenant_id == inv.tenant_id)
                .values(payment_status=payment_status)
            )
            session.commit()
        return str(inv.tenant_id)


def handle_stripe_payment_intent_succeeded(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository,
) -> Tuple[bool, Optional[str], Optional[str]]:
    obj = event.get("data", {}).get("object", {}) or {}
    intent_id = obj.get("id")
    tenant_id = _reconcile_usage_invoice_by_provider_ref(str(intent_id or ""), payment_status="paid")
    # Always return success to ensure webhook idempotency ledger is marked processed.
    return True, tenant_id, None


def handle_stripe_payment_intent_failed(
    event: Dict[str, Any],
    subscription_repo: SubscriptionRepository,
) -> Tuple[bool, Optional[str], Optional[str]]:
    obj = event.get("data", {}).get("object", {}) or {}
    intent_id = obj.get("id")
    tenant_id = _reconcile_usage_invoice_by_provider_ref(str(intent_id or ""), payment_status="failed")
    return True, tenant_id, None
