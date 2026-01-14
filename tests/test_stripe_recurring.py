"""Тесты для Stripe recurring billing (RenewSubscriptionsUseCase)."""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from cyberplat.billing_entitlements import EntitlementService
from cyberplat.billing_service import BillingService
from cyberplat.billing.infrastructure.repositories import EntitlementSubscriptionRepository
from cyberplat.billing.infrastructure.stripe_provider import StripePaymentProvider
from cyberplat.billing.application.renew_subscriptions_use_case import RenewSubscriptionsUseCase
# Импорты для мокирования


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
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
    billing_service = BillingService(db_path=temp_db)
    billing_service.ensure_schema()
    billing_service.seed_default_rates_if_empty()
    billing_service.close()
    
    service = EntitlementService(db_path=temp_db)
    service.ensure_schema()
    service.seed_default_plans_if_empty()
    
    yield service
    service.close()


@pytest.fixture
def billing_service(temp_db):
    """Создать BillingService для тестов."""
    service = BillingService(db_path=temp_db)
    service.ensure_schema()
    service.seed_default_rates_if_empty()
    
    yield service
    service.close()


@pytest.fixture
def subscription_repo(entitlement_service, billing_service):
    """Создать SubscriptionRepository для тестов."""
    from cyberplat.billing.infrastructure.repositories import EntitlementSubscriptionRepository
    return EntitlementSubscriptionRepository(entitlement_service, billing_service=billing_service)


def setup_stripe_subscription(
    entitlement_service,
    tenant_id: str,
    plan_id: str,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    period_end: str  # ISO format, может быть в прошлом для тестирования
):
    """
    Создать Stripe подписку в БД для тестов.
    
    Returns:
        subscription_id (внутренний ID)
    """
    now = datetime.now()
    period_start = (datetime.fromisoformat(period_end) - timedelta(days=30)).isoformat()
    
    subscription_id = entitlement_service.apply_plan_to_tenant(
        tenant_id=tenant_id,
        plan_id=plan_id,
        period_start=period_start,
        period_end=period_end,
        provider="stripe",
        provider_customer_id=stripe_customer_id,
        provider_subscription_id=stripe_subscription_id,
        status="active"
    )
    
    return subscription_id


def test_stripe_recurring_happy_path_sync_period(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Happy path - Stripe subscription active, период изменился.
    
    Ожидаем:
    - RecurringResult.charged == 1
    - Подписка обновлена (period_end синхронизирован)
    - errors пустой
    """
    tenant_id = "tenant-stripe-sync"
    plan_id = "plan_pro"
    stripe_subscription_id = "sub_test_sync"
    stripe_customer_id = "cus_test_sync"
    
    # Создаем подписку с периодом, который уже закончился (требует синхронизации)
    now = datetime.now()
    old_period_end = (now - timedelta(days=1)).isoformat()  # Период закончился вчера
    
    subscription_id = setup_stripe_subscription(
        entitlement_service,
        tenant_id=tenant_id,
        plan_id=plan_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        period_end=old_period_end
    )
    
    # Мокируем Stripe API - возвращаем активную подписку с новым периодом
    new_period_start = int(now.timestamp())
    new_period_end = int((now + timedelta(days=30)).timestamp())
    
    mock_stripe_subscription = MagicMock()
    mock_stripe_subscription.status = "active"
    mock_stripe_subscription.current_period_start = new_period_start
    mock_stripe_subscription.current_period_end = new_period_end
    mock_stripe_subscription.customer = stripe_customer_id
    
    mock_stripe = MagicMock()
    mock_stripe.Subscription.retrieve = MagicMock(return_value=mock_stripe_subscription)
    
    # Создаем use case
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    # Мокируем get_stripe_client внутри use case
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=mock_stripe):
        result = use_case.execute()
    
    # Проверяем результат
    assert result.charged == 1
    assert result.failed == 0
    assert result.skipped == 0
    assert len(result.errors) == 0
    
    # Проверяем, что период обновлен в БД
    conn = entitlement_service._get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT current_period_end
        FROM tenant_subscriptions
        WHERE id = ?
    """, (subscription_id,))
    row = cur.fetchone()
    conn.close()
    
    assert row is not None
    updated_period_end = datetime.fromisoformat(row["current_period_end"])
    expected_period_end = datetime.fromtimestamp(new_period_end)
    # Проверяем, что период обновлен (разница менее 1 секунды)
    assert abs((updated_period_end - expected_period_end).total_seconds()) < 1


def test_stripe_recurring_skip_period_not_expired(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Skip path - Stripe subscription active, период уже синхронизирован.
    
    Ожидаем:
    - RecurringResult.skipped == 1
    - charged == 0, failed == 0
    
    Подписка попадает в выборку (period_end в прошлом), но период в Stripe
    практически равен БД (разница < 1 day), поэтому синхронизация не требуется.
    """
    tenant_id = "tenant-stripe-skip"
    plan_id = "plan_pro"
    stripe_subscription_id = "sub_test_skip"
    stripe_customer_id = "cus_test_skip"
    
    # Создаем подписку с периодом, который уже истёк (чтобы попасть в выборку)
    # get_active_subscriptions_for_renewal() выбирает подписки с current_period_end < now
    now = datetime.now()
    past_period_end = (now - timedelta(days=1)).isoformat()  # Период истёк вчера
    
    subscription_id = setup_stripe_subscription(
        entitlement_service,
        tenant_id=tenant_id,
        plan_id=plan_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        period_end=past_period_end
    )
    
    # Мокируем Stripe API - возвращаем период, который практически равен БД
    # (разница < 1 day = 86400 секунд), чтобы use case засчитал skipped
    # Используем тот же период, что в БД (past_period_end), но в Unix timestamp
    past_period_end_dt = datetime.fromisoformat(past_period_end)
    stripe_period_end_ts = int(past_period_end_dt.timestamp())
    # Можно добавить небольшую разницу (< 12 часов), чтобы было явно < 1 day
    stripe_period_end_ts = int((past_period_end_dt + timedelta(hours=6)).timestamp())
    
    mock_stripe_subscription = MagicMock()
    mock_stripe_subscription.status = "active"
    mock_stripe_subscription.current_period_start = int((past_period_end_dt - timedelta(days=30)).timestamp())
    mock_stripe_subscription.current_period_end = stripe_period_end_ts
    mock_stripe_subscription.customer = stripe_customer_id
    
    mock_stripe = MagicMock()
    mock_stripe.Subscription.retrieve = MagicMock(return_value=mock_stripe_subscription)
    
    # Создаем use case
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    # Мокируем get_stripe_client внутри use case
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=mock_stripe):
        result = use_case.execute()
    
    # Проверяем результат
    assert result.charged == 0
    assert result.failed == 0
    assert result.skipped == 1
    assert len(result.errors) == 0


def test_stripe_recurring_cancel_downgrade_past_due(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Cancel/downgrade path - Stripe subscription status="past_due".
    
    Ожидаем:
    - RecurringResult.failed == 1
    - Подписка становится canceled
    - План применяется plan_free
    """
    tenant_id = "tenant-stripe-cancel"
    plan_id = "plan_pro"
    stripe_subscription_id = "sub_test_cancel"
    stripe_customer_id = "cus_test_cancel"
    
    # Создаем подписку с периодом, который уже закончился
    now = datetime.now()
    old_period_end = (now - timedelta(days=1)).isoformat()
    
    subscription_id = setup_stripe_subscription(
        entitlement_service,
        tenant_id=tenant_id,
        plan_id=plan_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        period_end=old_period_end
    )
    
    # Проверяем, что изначально план pro
    conn = entitlement_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT plan_id FROM tenant_subscriptions WHERE id = ?", (subscription_id,))
    row = cur.fetchone()
    assert row["plan_id"] == "plan_pro"
    conn.close()
    
    # Мокируем Stripe API - возвращаем подписку со статусом past_due
    mock_stripe_subscription = MagicMock()
    mock_stripe_subscription.status = "past_due"
    mock_stripe_subscription.current_period_start = int((now - timedelta(days=30)).timestamp())
    mock_stripe_subscription.current_period_end = int(now.timestamp())
    mock_stripe_subscription.customer = stripe_customer_id
    
    mock_stripe = MagicMock()
    mock_stripe.Subscription.retrieve = MagicMock(return_value=mock_stripe_subscription)
    
    # Создаем use case
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    # Мокируем get_stripe_client внутри use case
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=mock_stripe):
        result = use_case.execute()
    
    # Проверяем результат
    assert result.charged == 0
    assert result.failed == 1
    assert result.skipped == 0
    assert len(result.errors) == 0
    
    # Проверяем, что подписка отменена и downgrade на plan_free
    conn = entitlement_service._get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT plan_id, status
        FROM tenant_subscriptions
        WHERE id = ?
    """, (subscription_id,))
    row = cur.fetchone()
    conn.close()
    
    assert row is not None
    assert row["plan_id"] == "plan_free"
    assert row["status"] == "canceled"


def test_stripe_recurring_error_retrieve_fails(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Error path - stripe.Subscription.retrieve бросает Exception.
    
    Ожидаем:
    - RecurringResult.failed == 1
    - errors содержит описание ошибки
    - Исключение не пробрасывается наружу
    """
    tenant_id = "tenant-stripe-error"
    plan_id = "plan_pro"
    stripe_subscription_id = "sub_test_error"
    stripe_customer_id = "cus_test_error"
    
    # Создаем подписку
    now = datetime.now()
    old_period_end = (now - timedelta(days=1)).isoformat()
    
    subscription_id = setup_stripe_subscription(
        entitlement_service,
        tenant_id=tenant_id,
        plan_id=plan_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        period_end=old_period_end
    )
    
    # Мокируем Stripe API - бросаем исключение
    mock_stripe = MagicMock()
    mock_stripe.Subscription.retrieve = MagicMock(side_effect=Exception("Stripe API error: subscription not found"))
    
    # Создаем use case
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    # Мокируем get_stripe_client внутри use case
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=mock_stripe):
        result = use_case.execute()
    
    # Проверяем результат
    assert result.charged == 0
    assert result.failed == 1
    assert result.skipped == 0
    assert len(result.errors) == 1
    assert "Failed to retrieve Stripe subscription" in result.errors[0]
    assert stripe_subscription_id in result.errors[0]


def test_stripe_recurring_unpaid_status(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Cancel/downgrade path - Stripe subscription status="unpaid".
    
    Аналогично past_due, но проверяем статус unpaid.
    """
    tenant_id = "tenant-stripe-unpaid"
    plan_id = "plan_enterprise"
    stripe_subscription_id = "sub_test_unpaid"
    stripe_customer_id = "cus_test_unpaid"
    
    now = datetime.now()
    old_period_end = (now - timedelta(days=1)).isoformat()
    
    subscription_id = setup_stripe_subscription(
        entitlement_service,
        tenant_id=tenant_id,
        plan_id=plan_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        period_end=old_period_end
    )
    
    # Мокируем Stripe API - возвращаем подписку со статусом unpaid
    mock_stripe_subscription = MagicMock()
    mock_stripe_subscription.status = "unpaid"
    mock_stripe_subscription.current_period_start = int((now - timedelta(days=30)).timestamp())
    mock_stripe_subscription.current_period_end = int(now.timestamp())
    mock_stripe_subscription.customer = stripe_customer_id
    
    mock_stripe = MagicMock()
    mock_stripe.Subscription.retrieve = MagicMock(return_value=mock_stripe_subscription)
    
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=mock_stripe):
        result = use_case.execute()
    
    assert result.failed == 1
    assert result.charged == 0
    
    # Проверяем downgrade на plan_free
    conn = entitlement_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT plan_id, status FROM tenant_subscriptions WHERE id = ?", (subscription_id,))
    row = cur.fetchone()
    conn.close()
    
    assert row["plan_id"] == "plan_free"
    assert row["status"] == "canceled"


def test_stripe_recurring_no_client_available(
    entitlement_service,
    billing_service,
    subscription_repo
):
    """
    Тест: Stripe client не доступен.
    
    Ожидаем:
    - RecurringResult с errors=["Stripe client not available"]
    - charged/failed/skipped == 0
    """
    use_case = RenewSubscriptionsUseCase(
        provider_name="stripe",
        subscription_repo=subscription_repo,
        stripe_provider=StripePaymentProvider()
    )
    
    # Мокируем get_stripe_client - возвращаем None
    with patch('cyberplat.stripe_client.get_stripe_client', return_value=None):
        result = use_case.execute()
    
    assert result.charged == 0
    assert result.failed == 0
    assert result.skipped == 0
    assert len(result.errors) == 1
    assert "Stripe client not available" in result.errors[0]
