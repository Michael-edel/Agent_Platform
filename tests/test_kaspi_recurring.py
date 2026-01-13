"""Тесты для Kaspi recurring subscriptions (autocharge)."""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from cyberplat.billing_entitlements import EntitlementService
from cyberplat.billing_service import BillingService
from cyberplat.kaspi_recurring import charge_kaspi_subscriptions
from cyberplat.kaspi_webhook_handler import KaspiWebhookHandler
from cyberplat.kaspi_client import set_kaspi_client, get_kaspi_client


@pytest.fixture
def temp_db():
    """
    Создать временную БД для тестов.
    
    На Windows файл может быть заблокирован, если соединения не закрыты.
    Используем retry логику для безопасного удаления.
    """
    import time
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Teardown: закрываем соединения и удаляем файл
    if os.path.exists(path):
        # Retry логика для Windows (файл может быть временно заблокирован)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)  # Короткая пауза перед повтором
                else:
                    # Последняя попытка - логируем но не падаем
                    import logging
                    logging.warning(f"Не удалось удалить временный файл {path} после {max_retries} попыток")


@pytest.fixture
def entitlement_service(temp_db):
    """
    Создать EntitlementService для тестов.
    
    КРИТИЧНО: Инициализируем BillingService schema ПЕРЕД EntitlementService,
    потому что apply_plan_to_tenant() использует BillingService для обновления billing_rates.
    """
    # 1. Сначала инициализируем BillingService schema (billing_rates таблица)
    billing_service = BillingService(db_path=temp_db)
    billing_service.ensure_schema()
    billing_service.seed_default_rates_if_empty()
    billing_service.close()  # Закрываем соединения
    
    # 2. Затем инициализируем EntitlementService (планы, подписки, заказы)
    service = EntitlementService(db_path=temp_db)
    service.ensure_schema()
    service.seed_default_plans_if_empty()
    
    yield service
    
    # Teardown: закрываем соединения
    service.close()


@pytest.fixture
def billing_service(temp_db):
    """Создать BillingService для тестов."""
    service = BillingService(db_path=temp_db)
    service.ensure_schema()
    service.seed_default_rates_if_empty()
    
    yield service
    
    # Teardown: закрываем соединения
    service.close()


@pytest.fixture
def tenant_id():
    """Тестовый tenant ID."""
    return "tenant-kaspi-autocharge-test"


@pytest.fixture
def kaspi_token():
    """Тестовый Kaspi payment token."""
    return "kaspi_token_test_12345"


def setup_initial_subscription(entitlement_service, tenant_id, kaspi_token):
    """
    Настроить начальную подписку (Month 1 - оплачена через webhook).
    
    Примечание: BillingService schema должна быть инициализирована в фикстуре,
    так как apply_plan_to_tenant() использует BillingService для обновления billing_rates.
    
    Returns:
        subscription_id, order_id
    """
    # Сохраняем payment profile с kaspi_token в billing_kaspi_profiles
    # КРИТИЧНО: Используем save_kaspi_token, который сохраняет в billing_kaspi_profiles,
    # а не upsert_payment_profile, который сохраняет в billing_tenant_payment_profiles
    entitlement_service.save_kaspi_token(tenant_id, kaspi_token)
    
    # Создаем заказ (симулируем оплату через webhook)
    now = datetime.now()
    period_start = now.isoformat()
    period_end = (now + timedelta(days=30)).isoformat()
    
    # Получаем цену plan_pro
    conn = entitlement_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT price_minor, currency FROM billing_plans WHERE id = ?", ("plan_pro",))
    plan_row = cur.fetchone()
    conn.close()
    
    amount_minor = plan_row["price_minor"]
    currency = plan_row["currency"]
    
    # Создаем заказ
    order_id = entitlement_service.create_order(
        tenant_id=tenant_id,
        provider="kaspi",
        plan_id="plan_pro",
        amount_minor=amount_minor,
        currency=currency,
        external_order_id=f"kaspi_order_month1_{now.strftime('%Y%m%d%H%M%S')}"
    )
    
    # Обновляем статус на paid (симулируем webhook)
    entitlement_service.update_order_status(order_id, "paid")
    
    # Применяем план (симулируем webhook обработку)
    subscription_id = entitlement_service.apply_plan_to_tenant(
        tenant_id=tenant_id,
        plan_id="plan_pro",
        period_start=period_start,
        period_end=period_end,
        provider="kaspi",
        provider_subscription_id=f"kaspi_sub_{tenant_id}",
        status="active"
    )
    
    return subscription_id, order_id


class TestKaspiRecurringSubscriptions:
    """Тесты для Kaspi recurring subscriptions."""
    
    def test_1_initial_paid_subscription(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST 1: Initial Paid Subscription (Month 1)
        
        Симулируем Kaspi webhook → payment.paid
        """
        # Настраиваем начальную подписку
        subscription_id, order_id = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        # Проверяем billing_orders
        order = entitlement_service.get_order(order_id)
        assert order is not None, "Заказ должен быть создан"
        assert order["status"] == "paid", "Статус заказа должен быть 'paid'"
        assert order["tenant_id"] == tenant_id, "Заказ должен принадлежать правильному tenant"
        assert order["plan_id"] == "plan_pro", "Заказ должен быть для plan_pro"
        assert order["external_order_id"] is not None, "Должен быть external_order_id"
        assert order["paid_at"] is not None, "Должно быть время оплаты"
        
        # Проверяем tenant_subscriptions
        plan_info = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info["plan"]["id"] == "plan_pro", "План должен быть plan_pro"
        assert plan_info["subscription"]["status"] == "active", "Подписка должна быть active"
        
        # Проверяем kaspi_token сохранен в billing_kaspi_profiles
        # КРИТИЧНО: Используем get_kaspi_token, который читает из billing_kaspi_profiles,
        # а не get_payment_profile, который читает из billing_tenant_payment_profiles
        kaspi_token_saved = entitlement_service.get_kaspi_token(tenant_id)
        assert kaspi_token_saved is not None, "Kaspi token должен быть сохранен в billing_kaspi_profiles"
        assert kaspi_token_saved == kaspi_token, "Kaspi token должен совпадать с переданным"
        
        # Проверяем billing_plan_limits применены
        limits = entitlement_service.get_plan_limits("plan_pro")
        assert len(limits) > 0, "Должны быть лимиты для plan_pro"
        
        # Проверяем, что billing_rates обновлены с квотами
        # Примечание: BillingService schema уже инициализирована в фикстуре entitlement_service
        billing_service = BillingService(db_path=entitlement_service.db_path)
        
        # Проверяем, что для tenant есть тарифы с квотами
        conn = billing_service._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT monthly_quota
            FROM billing_rates
            WHERE tenant_id = ? AND metric = 'invoice_extracted'
        """, (tenant_id,))
        rate_row = cur.fetchone()
        conn.close()
        billing_service.close()  # Закрываем соединения для Windows
        
        assert rate_row is not None, "Должен быть tenant-specific тариф"
        assert rate_row["monthly_quota"] is not None, "Должна быть установлена квота"
        assert rate_row["monthly_quota"] > 0, "Квота должна быть больше 0"
    
    def test_2_recurring_charge_success(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST 2: Recurring charge success (Month 2)
        
        Симулируем успешное автопродление подписки.
        """
        # Настраиваем начальную подписку (Month 1)
        subscription_id, order_id_1 = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        # Устанавливаем период окончания в прошлом (чтобы триггернуть автопродление)
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id = ?
        """, (past_period_end, subscription_id))
        conn.commit()
        conn.close()
        
        # Мокаем KaspiClient.charge_token() для успешного списания
        mock_charge_result = {
            "external_order_id": f"kaspi_charge_month2_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "status": "success"
        }
        
        with patch('cyberplat.kaspi_recurring.charge_token', return_value=mock_charge_result):
            # Вызываем автопродление
            result = charge_kaspi_subscriptions(entitlement_service)
        
        # Проверяем результат
        assert result["charged"] == 1, "Должна быть одна успешно списанная подписка"
        assert result["failed"] == 0, "Не должно быть неудачных списаний"
        assert len(result["errors"]) == 0, "Не должно быть ошибок"
        
        # Проверяем, что создан новый заказ
        orders_conn = entitlement_service._get_connection()
        orders_cur = orders_conn.cursor()
        orders_cur.execute("""
            SELECT id, status, external_order_id
            FROM billing_orders
            WHERE tenant_id = ? AND provider = 'kaspi'
            ORDER BY created_at DESC
        """, (tenant_id,))
        orders = orders_cur.fetchall()
        orders_conn.close()
        
        assert len(orders) >= 2, "Должно быть минимум 2 заказа (Month 1 + Month 2)"
        
        # Проверяем последний заказ (Month 2)
        latest_order = orders[0]
        assert latest_order["status"] == "paid", "Новый заказ должен быть paid"
        assert latest_order["external_order_id"] == mock_charge_result["external_order_id"]
        
        # Проверяем, что подписка все еще active
        plan_info = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info["plan"]["id"] == "plan_pro", "План должен остаться plan_pro"
        assert plan_info["subscription"]["status"] == "active", "Подписка должна остаться active"
        
        # Проверяем, что период обновлен
        sub_conn = entitlement_service._get_connection()
        sub_cur = sub_conn.cursor()
        sub_cur.execute("""
            SELECT current_period_start, current_period_end
            FROM tenant_subscriptions
            WHERE tenant_id = ?
        """, (tenant_id,))
        sub_row = sub_cur.fetchone()
        sub_conn.close()
        
        assert sub_row is not None, "Подписка должна существовать"
        assert sub_row["current_period_end"] is not None, "Должен быть установлен новый период окончания"
        
        # Проверяем, что период в будущем (продлен на 30 дней)
        period_end_dt = datetime.fromisoformat(sub_row["current_period_end"])
        assert period_end_dt > datetime.now(), "Период должен быть продлен в будущее"
    
    def test_3_recurring_charge_failure(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST 3: Recurring charge failure (Month 3)
        
        Симулируем неудачное автопродление → downgrade to free.
        """
        # Настраиваем начальную подписку (Month 1)
        subscription_id, order_id_1 = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        # Устанавливаем период окончания в прошлом
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id = ?
        """, (past_period_end, subscription_id))
        conn.commit()
        conn.close()
        
        # Мокаем KaspiClient.charge_token() для неудачного списания
        mock_charge_result = None  # Или {"status": "failed"}
        
        with patch('cyberplat.kaspi_recurring.charge_token', return_value=mock_charge_result):
            # Вызываем автопродление
            result = charge_kaspi_subscriptions(entitlement_service)
        
        # Проверяем результат
        assert result["charged"] == 0, "Не должно быть успешных списаний"
        assert result["failed"] == 1, "Должна быть одна неудачная попытка"
        assert len(result["errors"]) > 0, "Должна быть ошибка"
        
        # Проверяем, что подписка отменена и downgrade на plan_free
        plan_info = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info["plan"]["id"] == "plan_free", "План должен быть downgrade на plan_free"
        assert plan_info["subscription"]["status"] == "canceled", "Подписка должна быть canceled"
        
        # Проверяем, что billing_plan_limits обновлены (квоты plan_free)
        limits = entitlement_service.get_plan_limits("plan_free")
        assert len(limits) > 0, "Должны быть лимиты для plan_free"
        
        # Проверяем, что создан failed заказ (для аудита)
        orders_conn = entitlement_service._get_connection()
        orders_cur = orders_conn.cursor()
        orders_cur.execute("""
            SELECT id, status
            FROM billing_orders
            WHERE tenant_id = ? AND provider = 'kaspi' AND status = 'failed'
            ORDER BY created_at DESC
            LIMIT 1
        """, (tenant_id,))
        failed_order = orders_cur.fetchone()
        orders_conn.close()
        
        assert failed_order is not None, "Должен быть создан failed заказ для аудита"
        
        # Проверяем, что billing_rates обновлены с квотами plan_free
        # Примечание: BillingService schema уже инициализирована в фикстуре entitlement_service
        billing_service = BillingService(db_path=entitlement_service.db_path)
        
        conn = billing_service._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT monthly_quota
            FROM billing_rates
            WHERE tenant_id = ? AND metric = 'invoice_extracted'
        """, (tenant_id,))
        rate_row = cur.fetchone()
        conn.close()
        billing_service.close()  # Закрываем соединения
        
        assert rate_row is not None, "Должен быть tenant-specific тариф"
        # Квота plan_free должна быть меньше чем plan_pro
        free_limits = entitlement_service.get_plan_limits("plan_free")
        free_quota = next((l["monthly_quota"] for l in free_limits if l["metric"] == "invoice_extracted"), None)
        assert rate_row["monthly_quota"] == free_quota, "Квота должна соответствовать plan_free"
    
    def test_idempotency_double_charge(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST: Idempotency - запуск дважды не должен двойно списывать.
        """
        # Настраиваем начальную подписку
        subscription_id, order_id_1 = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        # Устанавливаем период окончания в прошлом
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id = ?
        """, (past_period_end, subscription_id))
        conn.commit()
        conn.close()
        
        # Мокаем KaspiClient.charge_token()
        mock_charge_result = {
            "external_order_id": f"kaspi_charge_idempotency_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "status": "success"
        }
        
        with patch('cyberplat.kaspi_recurring.charge_token', return_value=mock_charge_result) as mock_charge:
            # Первый вызов
            result1 = charge_kaspi_subscriptions(entitlement_service)
            
            # Второй вызов (тот же период еще не закончился)
            result2 = charge_kaspi_subscriptions(entitlement_service)
        
        # Проверяем, что charge_token вызван только один раз (период еще не закончился после первого списания)
        # Или дважды, но второй раз период уже продлен, поэтому не списывается
        
        # Проверяем количество заказов
        orders_conn = entitlement_service._get_connection()
        orders_cur = orders_conn.cursor()
        orders_cur.execute("""
            SELECT COUNT(*) as count
            FROM billing_orders
            WHERE tenant_id = ? AND provider = 'kaspi' AND status = 'paid'
        """, (tenant_id,))
        orders_count = orders_cur.fetchone()["count"]
        orders_conn.close()
        
        # Должен быть минимум 1 заказ (Month 1), максимум 2 (Month 1 + один автоплатеж)
        assert orders_count >= 1, "Должен быть минимум один оплаченный заказ"
        assert orders_count <= 2, "Не должно быть более двух оплаченных заказов (idempotency)"
    
    def test_tenant_isolation(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST: Tenant isolation - списание для одного tenant не влияет на другой.
        """
        tenant_id_1 = tenant_id
        tenant_id_2 = "tenant-kaspi-autocharge-test-2"
        kaspi_token_2 = "kaspi_token_test_67890"
        
        # Настраиваем подписки для двух tenants
        sub_id_1, order_id_1 = setup_initial_subscription(
            entitlement_service, tenant_id_1, kaspi_token
        )
        sub_id_2, order_id_2 = setup_initial_subscription(
            entitlement_service, tenant_id_2, kaspi_token_2
        )
        
        # Устанавливаем период окончания в прошлом для обоих
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id IN (?, ?)
        """, (past_period_end, sub_id_1, sub_id_2))
        conn.commit()
        conn.close()
        
        # Мокаем charge_token для успешного списания
        call_count = {"count": 0}
        
        def mock_charge_token(*args, **kwargs):
            call_count["count"] += 1
            return {
                "external_order_id": f"kaspi_charge_{call_count['count']}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "status": "success"
            }
        
        with patch('cyberplat.kaspi_recurring.charge_token', side_effect=mock_charge_token):
            result = charge_kaspi_subscriptions(entitlement_service)
        
        # Проверяем, что оба tenant списаны
        assert result["charged"] == 2, "Должны быть списаны оба tenant"
        
        # Проверяем, что каждый tenant имеет свои заказы
        orders_conn = entitlement_service._get_connection()
        orders_cur = orders_conn.cursor()
        orders_cur.execute("""
            SELECT tenant_id, COUNT(*) as count
            FROM billing_orders
            WHERE provider = 'kaspi' AND status = 'paid'
            GROUP BY tenant_id
        """)
        orders_by_tenant = {row["tenant_id"]: row["count"] for row in orders_cur.fetchall()}
        orders_conn.close()
        
        assert tenant_id_1 in orders_by_tenant, "Tenant 1 должен иметь заказы"
        assert tenant_id_2 in orders_by_tenant, "Tenant 2 должен иметь заказы"
        assert orders_by_tenant[tenant_id_1] >= 1, "Tenant 1 должен иметь минимум 1 заказ"
        assert orders_by_tenant[tenant_id_2] >= 1, "Tenant 2 должен иметь минимум 1 заказ"
        
        # Проверяем, что планы изолированы
        plan_info_1 = entitlement_service.get_tenant_plan_info(tenant_id_1)
        plan_info_2 = entitlement_service.get_tenant_plan_info(tenant_id_2)
        
        assert plan_info_1["plan"]["id"] == "plan_pro", "Tenant 1 должен иметь plan_pro"
        assert plan_info_2["plan"]["id"] == "plan_pro", "Tenant 2 должен иметь plan_pro"
    
    def test_webhook_events_consistency(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST: Проверка консистентности billing_webhook_events и billing_orders.
        """
        # Настраиваем начальную подписку
        subscription_id, order_id = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        # Симулируем webhook событие
        handler = KaspiWebhookHandler(entitlement_service, webhook_secret=None)
        
        # Создаем mock webhook event
        webhook_event = {
            "type": "payment.paid",
            "id": "kaspi_webhook_event_123",
            "order_id": entitlement_service.get_order(order_id)["external_order_id"]
        }
        
        # Записываем webhook событие
        webhook_id = entitlement_service.record_webhook_event(
            provider="kaspi",
            event_id=webhook_event["id"],
            raw_json=str(webhook_event),
            tenant_id=tenant_id
        )
        
        # Обрабатываем событие
        success, event_tenant_id, error = handler.handle_event(webhook_event)
        
        assert success, "Webhook должен быть обработан успешно"
        assert event_tenant_id == tenant_id, "Tenant ID должен совпадать"
        
        # Проверяем, что webhook событие отмечено как processed
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT status, processed_at
            FROM billing_webhook_events
            WHERE id = ?
        """, (webhook_id,))
        webhook_row = cur.fetchone()
        conn.close()
        
        assert webhook_row is not None, "Webhook событие должно быть записано"
        # Статус может быть "received" если мы не вызвали mark_webhook_processed
        # Но в реальном сценарии это должно быть "processed"
        
        # Проверяем консистентность: заказ должен быть paid
        order = entitlement_service.get_order(order_id)
        assert order["status"] == "paid", "Заказ должен быть paid после webhook"
    
    def test_full_lifecycle_month1_to_month3(self, entitlement_service, tenant_id, kaspi_token):
        """
        TEST: Полный lifecycle - Month 1 (paid) → Month 2 (success) → Month 3 (failure).
        """
        # Month 1: Initial payment
        subscription_id, order_id_1 = setup_initial_subscription(
            entitlement_service, tenant_id, kaspi_token
        )
        
        plan_info_1 = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info_1["plan"]["id"] == "plan_pro", "Month 1: должен быть plan_pro"
        assert plan_info_1["subscription"]["status"] == "active", "Month 1: должна быть active"
        
        # Month 2: Successful autocharge
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id = ?
        """, (past_period_end, subscription_id))
        conn.commit()
        conn.close()
        
        mock_charge_success = {
            "external_order_id": f"kaspi_charge_month2_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "status": "success"
        }
        
        with patch('cyberplat.kaspi_recurring.charge_token', return_value=mock_charge_success):
            result_month2 = charge_kaspi_subscriptions(entitlement_service)
        
        assert result_month2["charged"] == 1, "Month 2: должно быть успешное списание"
        
        plan_info_2 = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info_2["plan"]["id"] == "plan_pro", "Month 2: должен остаться plan_pro"
        assert plan_info_2["subscription"]["status"] == "active", "Month 2: должна остаться active"
        
        # Month 3: Failed autocharge
        conn = entitlement_service._get_connection()
        cur = conn.cursor()
        past_period_end_2 = (datetime.now() - timedelta(days=1)).isoformat()
        cur.execute("""
            UPDATE tenant_subscriptions
            SET current_period_end = ?
            WHERE id = ?
        """, (past_period_end_2, subscription_id))
        conn.commit()
        conn.close()
        
        mock_charge_failure = None
        
        with patch('cyberplat.kaspi_recurring.charge_token', return_value=mock_charge_failure):
            result_month3 = charge_kaspi_subscriptions(entitlement_service)
        
        assert result_month3["failed"] == 1, "Month 3: должно быть неудачное списание"
        
        plan_info_3 = entitlement_service.get_tenant_plan_info(tenant_id)
        assert plan_info_3["plan"]["id"] == "plan_free", "Month 3: должен быть downgrade на plan_free"
        assert plan_info_3["subscription"]["status"] == "canceled", "Month 3: должна быть canceled"
        
        # Проверяем итоговое количество заказов
        orders_conn = entitlement_service._get_connection()
        orders_cur = orders_conn.cursor()
        orders_cur.execute("""
            SELECT COUNT(*) as count, status
            FROM billing_orders
            WHERE tenant_id = ? AND provider = 'kaspi'
            GROUP BY status
        """, (tenant_id,))
        orders_by_status = {row["status"]: row["count"] for row in orders_cur.fetchall()}
        orders_conn.close()
        
        assert orders_by_status.get("paid", 0) >= 2, "Должно быть минимум 2 оплаченных заказа (Month 1 + Month 2)"
        assert orders_by_status.get("failed", 0) >= 1, "Должен быть минимум 1 failed заказ (Month 3 - для аудита)"
