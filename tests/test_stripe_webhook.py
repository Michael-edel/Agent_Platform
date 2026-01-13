"""Тесты для Stripe webhook обработки."""

import pytest
import json
import tempfile
import os
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from cyberplat.billing_entitlements import EntitlementService
from cyberplat.stripe_webhook_handler import StripeWebhookHandler
from cyberplat.billing_service import BillingService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        # Retry логика для Windows (файл может быть временно заблокирован)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                else:
                    import logging
                    logging.warning(f"Не удалось удалить временный файл {path} после {max_retries} попыток")


@pytest.fixture
def entitlement_service(temp_db):
    """Создать EntitlementService для тестов."""
    # КРИТИЧНО: Инициализируем BillingService schema ПЕРЕД EntitlementService
    # потому что apply_plan_to_tenant() использует BillingService для обновления billing_rates
    billing_service = BillingService(db_path=temp_db)
    # BillingService теперь self-healing (автоматически создает schema в __init__)
    billing_service.close()
    
    service = EntitlementService(db_path=temp_db)
    service.ensure_schema()
    service.seed_default_plans_if_empty()
    
    yield service
    service.close()


@pytest.fixture
def billing_service(temp_db):
    """Создать BillingService для тестов."""
    # BillingService теперь self-healing (автоматически создает schema в __init__)
    service = BillingService(db_path=temp_db)
    
    yield service
    service.close()


@pytest.fixture
def stripe_handler(entitlement_service):
    """Создать StripeWebhookHandler для тестов."""
    return StripeWebhookHandler(
        entitlement_service=entitlement_service,
        webhook_secret="test_secret"
    )


def test_idempotency_same_event_twice(entitlement_service, stripe_handler):
    """Тест: идемпотентность - одно событие дважды не обрабатывается."""
    tenant_id = "tenant-123"
    plan_id = "plan_pro"
    
    # Создаем Stripe event
    event = {
        "id": "evt_test123",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test",
                "customer": "cus_test",
                "subscription": "sub_test",
                "period_start": int(datetime.now().timestamp()),
                "period_end": int(datetime.now().timestamp()) + 2592000,  # +30 дней
                "metadata": {
                    "tenant_id": tenant_id,
                    "plan_id": plan_id
                }
            }
        }
    }
    
    # Первая обработка
    success1, tenant_id1, error1 = stripe_handler.handle_event(event)
    assert success1 is True
    assert tenant_id1 == tenant_id
    
    # Записываем событие
    webhook_id1 = entitlement_service.record_webhook_event(
        provider="stripe",
        event_id=event["id"],
        raw_json=json.dumps(event),
        tenant_id=tenant_id
    )
    entitlement_service.mark_webhook_processed(webhook_id1, tenant_id=tenant_id)
    
    # Вторая обработка (то же событие)
    webhook_id2 = entitlement_service.record_webhook_event(
        provider="stripe",
        event_id=event["id"],
        raw_json=json.dumps(event),
        tenant_id=tenant_id
    )
    
    # Должен вернуть тот же webhook_id
    assert webhook_id1 == webhook_id2


def test_missing_signature_returns_400():
    """Тест: отсутствие подписи возвращает ошибку."""
    handler = StripeWebhookHandler(
        entitlement_service=Mock(),
        webhook_secret="test_secret"
    )
    
    payload = b'{"test": "data"}'
    signature = None
    
    # Проверка подписи должна вернуть False
    result = handler.verify_signature(payload, signature)
    assert result is False


def test_unknown_tenant_ignored(entitlement_service, stripe_handler):
    """Тест: событие без tenant_id игнорируется."""
    event = {
        "id": "evt_test456",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test",
                "metadata": {}  # Нет tenant_id
            }
        }
    }
    
    success, tenant_id, error_msg = stripe_handler.handle_event(event)
    
    assert success is False
    assert tenant_id is None
    assert "Missing tenant_id" in error_msg


def test_invoice_paid_applies_plan_and_updates_rates_quota(
    entitlement_service,
    billing_service,
    stripe_handler
):
    """Тест: invoice.paid применяет план и обновляет квоты."""
    tenant_id = "tenant-pro"
    plan_id = "plan_pro"
    
    # Создаем Stripe event invoice.paid
    event = {
        "id": "evt_invoice_paid",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test",
                "customer": "cus_test",
                "subscription": "sub_test",
                "period_start": int(datetime.now().timestamp()),
                "period_end": int(datetime.now().timestamp()) + 2592000,
                "metadata": {
                    "tenant_id": tenant_id,
                    "plan_id": plan_id
                }
            }
        }
    }
    
    # Обрабатываем событие
    success, event_tenant_id, error_msg = stripe_handler.handle_event(event)
    
    assert success is True
    assert event_tenant_id == tenant_id
    
    # Проверяем, что квоты обновлены
    # Получаем тариф для invoice_extracted
    rate = billing_service.resolve_rate(tenant_id, "invoice_extracted")
    assert rate is not None
    # Pro plan должен иметь quota=500 для invoice_extracted
    assert rate["monthly_quota"] == 500


def test_subscription_canceled_rolls_back_to_free_plan(
    entitlement_service,
    billing_service,
    stripe_handler
):
    """Тест: subscription canceled откатывает на free plan."""
    tenant_id = "tenant-canceled"
    plan_id = "plan_pro"
    
    # Сначала применяем pro plan
    now = datetime.now()
    entitlement_service.apply_plan_to_tenant(
        tenant_id=tenant_id,
        plan_id=plan_id,
        period_start=now.isoformat(),
        period_end=(now.replace(day=1) if now.day > 1 else now).isoformat(),
        provider="stripe",
        status="active"
    )
    
    # Проверяем, что квота pro установлена
    rate_before = billing_service.resolve_rate(tenant_id, "invoice_extracted")
    assert rate_before["monthly_quota"] == 500  # Pro plan
    
    # Создаем событие subscription canceled
    event = {
        "id": "evt_sub_canceled",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test",
                "customer": "cus_test",
                "status": "canceled",
                "current_period_start": int(now.timestamp()),
                "current_period_end": int(now.timestamp()) + 2592000,
                "metadata": {
                    "tenant_id": tenant_id,
                    "plan_id": plan_id
                }
            }
        }
    }
    
    # Обрабатываем событие
    success, event_tenant_id, error_msg = stripe_handler.handle_event(event)
    
    assert success is True
    
    # Проверяем, что откатилось на free plan (quota=5)
    rate_after = billing_service.resolve_rate(tenant_id, "invoice_extracted")
    assert rate_after["monthly_quota"] == 5  # Free plan


def test_verify_signature_valid(stripe_handler):
    """Тест: проверка валидной подписи."""
    import hmac
    import hashlib
    
    payload = b'{"test": "data"}'
    timestamp = str(int(datetime.now().timestamp()))
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    
    # Создаем валидную подпись
    expected_sig = hmac.new(
        "test_secret".encode('utf-8'),
        signed_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    signature = f"t={timestamp},v1={expected_sig}"
    
    result = stripe_handler.verify_signature(payload, signature)
    assert result is True


def test_verify_signature_invalid(stripe_handler):
    """Тест: проверка невалидной подписи."""
    payload = b'{"test": "data"}'
    signature = "t=1234567890,v1=invalid_signature"
    
    result = stripe_handler.verify_signature(payload, signature)
    assert result is False
