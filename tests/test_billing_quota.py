"""Тесты для quota preview и admin reset usage."""

import pytest
import tempfile
import os
from datetime import datetime

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
def billing_service(temp_db):
    """Создать BillingService для тестов."""
    # BillingService теперь self-healing (автоматически создает schema в __init__)
    service = BillingService(db_path=temp_db)
    
    yield service
    service.close()


def test_quota_preview_no_quota_returns_null_remaining(billing_service):
    """Тест: quota preview для метрики без квоты возвращает null remaining."""
    tenant_id = "tenant-quota-1"
    period = datetime.now().strftime("%Y-%m")
    metric = "invoice_extracted"
    
    # Создаем usage без квоты (default rate не имеет monthly_quota)
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric=metric,
        units=5.0,
        unit_price_minor=25,
        currency="USD",
        period=period
    )
    
    # Получаем quota status
    quota_status = billing_service.get_quota_status(tenant_id=tenant_id, period=period)
    
    assert quota_status["tenant_id"] == tenant_id
    assert quota_status["period"] == period
    assert metric in quota_status["metrics"]
    
    metric_status = quota_status["metrics"][metric]
    assert metric_status["used_units"] == 5.0
    assert metric_status["monthly_quota"] is None
    assert metric_status["remaining_units"] is None
    assert metric_status["is_exceeded"] is False
    assert metric_status["unit_price_minor"] == 25
    assert metric_status["amount_minor"] == 125  # 5.0 * 25


def test_quota_preview_quota_exceeded(billing_service):
    """Тест: quota preview показывает exceeded когда used > quota."""
    tenant_id = "tenant-quota-2"
    period = datetime.now().strftime("%Y-%m")
    metric = "invoice_extracted"
    
    # Создаем tenant-specific rate с квотой
    billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric=metric,
        unit_price_minor=25,
        currency="USD",
        active=True,
        monthly_quota=10  # Квота 10
    )
    
    # Создаем usage больше квоты
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric=metric,
        units=15.0,  # Превышает квоту
        unit_price_minor=25,
        currency="USD",
        period=period
    )
    
    # Получаем quota status
    quota_status = billing_service.get_quota_status(tenant_id=tenant_id, period=period)
    
    metric_status = quota_status["metrics"][metric]
    assert metric_status["used_units"] == 15.0
    assert metric_status["monthly_quota"] == 10
    assert metric_status["remaining_units"] == 0  # Превышена квота
    assert metric_status["is_exceeded"] is True
    assert metric_status["unit_price_minor"] == 25
    assert metric_status["amount_minor"] == 375  # 15.0 * 25


def test_quota_preview_with_quota_not_exceeded(billing_service):
    """Тест: quota preview показывает remaining когда used < quota."""
    tenant_id = "tenant-quota-3"
    period = datetime.now().strftime("%Y-%m")
    metric = "invoice_extracted"
    
    # Создаем tenant-specific rate с квотой
    billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric=metric,
        unit_price_minor=25,
        currency="USD",
        active=True,
        monthly_quota=100  # Квота 100
    )
    
    # Создаем usage меньше квоты
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric=metric,
        units=30.0,  # Меньше квоты
        unit_price_minor=25,
        currency="USD",
        period=period
    )
    
    # Получаем quota status
    quota_status = billing_service.get_quota_status(tenant_id=tenant_id, period=period)
    
    metric_status = quota_status["metrics"][metric]
    assert metric_status["used_units"] == 30.0
    assert metric_status["monthly_quota"] == 100
    assert metric_status["remaining_units"] == 70  # 100 - 30
    assert metric_status["is_exceeded"] is False
    assert metric_status["unit_price_minor"] == 25
    assert metric_status["amount_minor"] == 750  # 30.0 * 25


def test_admin_reset_usage_deletes_rows(billing_service):
    """Тест: admin reset usage удаляет строки из billing_usage."""
    tenant_id = "tenant-reset-1"
    period = datetime.now().strftime("%Y-%m")
    
    # Создаем несколько usage записей
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric="invoice_extracted",
        units=5.0,
        unit_price_minor=25,
        currency="USD",
        period=period
    )
    
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-2",
        artifact_id="artifact-2",
        event_type="document.extracted",
        metric="page_processed",
        units=10.0,
        unit_price_minor=2,
        currency="USD",
        period=period
    )
    
    # Проверяем, что usage существует
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    assert len(usage["lines"]) == 2
    
    # Выполняем reset
    deleted_rows = billing_service.reset_usage(tenant_id=tenant_id, period=period)
    
    assert deleted_rows == 2
    
    # Проверяем, что usage удален
    usage_after = billing_service.get_usage(tenant_id=tenant_id, period=period)
    assert len(usage_after["lines"]) == 0
    assert usage_after["total_amount_minor"] == 0


def test_admin_reset_usage_only_deletes_specified_period(billing_service):
    """Тест: reset usage удаляет только указанный период."""
    tenant_id = "tenant-reset-2"
    period1 = "2026-01"
    period2 = "2026-02"
    
    # Создаем usage для двух периодов
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric="invoice_extracted",
        units=5.0,
        unit_price_minor=25,
        currency="USD",
        period=period1
    )
    
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-2",
        artifact_id="artifact-2",
        event_type="document.extracted",
        metric="invoice_extracted",
        units=3.0,
        unit_price_minor=25,
        currency="USD",
        period=period2
    )
    
    # Сбрасываем только period1
    deleted_rows = billing_service.reset_usage(tenant_id=tenant_id, period=period1)
    assert deleted_rows == 1
    
    # Проверяем, что period1 удален, а period2 остался
    usage1 = billing_service.get_usage(tenant_id=tenant_id, period=period1)
    assert len(usage1["lines"]) == 0
    
    usage2 = billing_service.get_usage(tenant_id=tenant_id, period=period2)
    assert len(usage2["lines"]) == 1


def test_quota_preview_multiple_metrics(billing_service):
    """Тест: quota preview показывает все метрики."""
    tenant_id = "tenant-quota-4"
    period = datetime.now().strftime("%Y-%m")
    
    # Создаем usage для разных метрик
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-1",
        artifact_id="artifact-1",
        event_type="document.extracted",
        metric="invoice_extracted",
        units=5.0,
        unit_price_minor=25,
        currency="USD",
        period=period
    )
    
    billing_service.record_event_charge(
        tenant_id=tenant_id,
        event_id="event-2",
        artifact_id="artifact-2",
        event_type="payment.prepared",
        metric="payment_prepared",
        units=2.0,
        unit_price_minor=5,
        currency="USD",
        period=period
    )
    
    # Получаем quota status
    quota_status = billing_service.get_quota_status(tenant_id=tenant_id, period=period)
    
    assert "invoice_extracted" in quota_status["metrics"]
    assert "payment_prepared" in quota_status["metrics"]
    
    assert quota_status["metrics"]["invoice_extracted"]["used_units"] == 5.0
    assert quota_status["metrics"]["payment_prepared"]["used_units"] == 2.0
