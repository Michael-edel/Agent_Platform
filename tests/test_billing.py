"""Тесты для billing системы."""

import pytest
import tempfile
import os
from datetime import datetime

from cyberplat.billing_service import BillingService
from cyberplat.billing_subscriber import BillingSubscriber
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService


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


@pytest.fixture
def artifact_service():
    """Создать ArtifactService для тестов."""
    event_service = EventService()
    service = ArtifactService(event_service=event_service)
    event_service.artifact_service = service
    return service


@pytest.fixture
def billing_subscriber(billing_service, artifact_service):
    """Создать BillingSubscriber для тестов."""
    return BillingSubscriber(
        billing_service=billing_service,
        artifact_service=artifact_service
    )


def test_idempotency_same_event_twice(billing_service, billing_subscriber):
    """Тест: идемпотентность - одно событие не должно начисляться дважды."""
    event_id = "event-123"
    tenant_id = "tenant-123"
    event_type = "document.extracted"
    artifact_id = "artifact-123"
    created_at = datetime.now().isoformat()
    
    # Первый вызов
    billing_subscriber(event_id, event_type, tenant_id, artifact_id, {}, created_at)
    
    # Второй вызов (то же событие)
    billing_subscriber(event_id, event_type, tenant_id, artifact_id, {}, created_at)
    
    # Проверяем, что записана только одна запись для каждой метрики
    period = datetime.now().strftime("%Y-%m")
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    
    # Должна быть только одна запись для invoice_extracted
    invoice_lines = [line for line in usage["lines"] if line["metric"] == "invoice_extracted"]
    assert len(invoice_lines) == 1, "Одно событие должно начисляться только один раз для каждой метрики"
    
    # Проверяем, что event_id уникален (составной: event_id:metric)
    assert invoice_lines[0]["event_id"] == f"{event_id}:invoice_extracted"


def test_default_rate_applied(billing_service, billing_subscriber):
    """Тест: применяется default тариф, если нет tenant-specific."""
    event_id = "event-456"
    tenant_id = "tenant-456"
    event_type = "invoice_extracted"  # Прямой вызов для теста
    
    # Проверяем, что есть default тариф
    rate = billing_service.resolve_rate(tenant_id, "invoice_extracted")
    assert rate is not None, "Должен быть default тариф"
    assert rate["tenant_id"] is None, "Должен быть default тариф (tenant_id IS NULL)"
    assert rate["unit_price_minor"] == 25, "Default тариф для invoice_extracted = 25 cents"


def test_tenant_override_rate(billing_service, billing_subscriber):
    """Тест: tenant-specific тариф имеет приоритет над default."""
    tenant_id = "tenant-789"
    metric = "invoice_extracted"
    
        # Создаем tenant-specific тариф
    billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric=metric,
        unit_price_minor=50,  # Выше чем default (25)
        currency="USD"
    )
    
    # Проверяем, что применяется tenant-specific тариф
    rate = billing_service.resolve_rate(tenant_id, metric)
    assert rate is not None
    assert rate["tenant_id"] == tenant_id, "Должен быть tenant-specific тариф"
    assert rate["unit_price_minor"] == 50, "Должен применяться tenant-specific тариф"


def test_usage_aggregation_period(billing_service, billing_subscriber, artifact_service):
    """Тест: агрегация usage по периодам."""
    tenant_id = "tenant-abc"
    
    # Создаем события в разных периодах
    period1 = "2026-01"
    period2 = "2026-02"
    
    # События для периода 1
    billing_subscriber(
        "event-1", "document.extracted", tenant_id, "artifact-1", {},
        f"{period1}-15T10:00:00"
    )
    
    # События для периода 2
    billing_subscriber(
        "event-2", "document.extracted", tenant_id, "artifact-2", {},
        f"{period2}-15T10:00:00"
    )
    
    # Проверяем агрегацию
    summary = billing_service.get_summary(
        tenant_id=tenant_id,
        from_period=period1,
        to_period=period2
    )
    
    assert len(summary["periods"]) == 2, "Должно быть 2 периода"
    assert summary["periods"][0]["period"] == period1
    assert summary["periods"][1]["period"] == period2


def test_page_processed_metric_from_invoice_total_pages(
    billing_service,
    billing_subscriber,
    artifact_service
):
    """Тест: метрика page_processed извлекается из invoice.total_pages."""
    tenant_id = "tenant-pages"
    
    # Создаем invoice artifact с total_pages
    invoice_data = {
        "total": "1000",
        "total_pages": 3,
        "supplier": {"name": "Test"}
    }
    
    artifact_id = artifact_service.create_artifact(
        kind="invoice",
        source="doc_agent",
        data=invoice_data,
        tenant_id=tenant_id
    )
    
    # Вызываем подписчика для document.extracted
    event_id = "event-pages"
    billing_subscriber(
        event_id,
        "document.extracted",
        tenant_id,
        artifact_id,
        {},
        datetime.now().isoformat()
    )
    
    # Проверяем, что записаны обе метрики
    period = datetime.now().strftime("%Y-%m")
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    
    invoice_lines = [line for line in usage["lines"] if line["metric"] == "invoice_extracted"]
    page_lines = [line for line in usage["lines"] if line["metric"] == "page_processed"]
    
    assert len(invoice_lines) == 1, "Должна быть запись для invoice_extracted"
    assert len(page_lines) == 1, "Должна быть запись для page_processed"
    assert page_lines[0]["units"] == 3.0, "Должно быть 3 страницы"


def test_billing_rates_get(billing_service):
    """Тест: получение тарифов (tenant + default)."""
    tenant_id = "tenant-rates"
    
    # Создаем tenant-specific тариф
    billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric="invoice_extracted",
        unit_price_minor=30,
        currency="USD"
    )
    
    # Получаем тарифы
    rates = billing_service.list_rates(tenant_id)
    
    assert "tenant" in rates
    assert "default" in rates
    assert len(rates["tenant"]) > 0, "Должны быть tenant-specific тарифы"
    assert len(rates["default"]) > 0, "Должны быть default тарифы"


def test_billing_usage_empty_period(billing_service):
    """Тест: получение usage для пустого периода."""
    tenant_id = "tenant-empty"
    period = "2026-01"
    
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    
    assert usage["tenant_id"] == tenant_id
    assert usage["period"] == period
    assert usage["total_amount_minor"] == 0
    assert len(usage["lines"]) == 0


def test_billing_subscriber_artifact_created_document(billing_service, billing_subscriber, artifact_service):
    """Тест: billing для artifact.created с kind=document."""
    tenant_id = "tenant-doc"
    
    # Создаем document artifact
    artifact_id = artifact_service.create_artifact(
        kind="document",
        source="upload",
        data={"filename": "test.pdf", "file_id": "file-123"},
        tenant_id=tenant_id
    )
    
    # Получаем event_id из последнего события (artifact.created)
    # Для теста просто вызываем подписчика напрямую
    event_id = "event-doc"
    billing_subscriber(
        event_id,
        "artifact.created",
        tenant_id,
        artifact_id,
        {"kind": "document", "source": "upload"},
        datetime.now().isoformat()
    )
    
    # Проверяем usage
    period = datetime.now().strftime("%Y-%m")
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    
    doc_lines = [line for line in usage["lines"] if line["metric"] == "document_upload"]
    assert len(doc_lines) == 1, "Должна быть запись для document_upload"


def test_billing_subscriber_payment_events(billing_service, billing_subscriber, artifact_service):
    """Тест: billing для payment.prepared и payment.ready."""
    tenant_id = "tenant-payment"
    
    # Создаем payment artifact
    payment_data = {"amount_minor": 100000, "currency": "RUB"}
    artifact_id = artifact_service.create_artifact(
        kind="payment",
        source="payment_agent",
        data=payment_data,
        tenant_id=tenant_id
    )
    
    # Вызываем подписчика для payment.prepared
    billing_subscriber(
        "event-prep",
        "payment.prepared",
        tenant_id,
        artifact_id,
        {},
        datetime.now().isoformat()
    )
    
    # Вызываем подписчика для payment.ready
    billing_subscriber(
        "event-ready",
        "payment.ready",
        tenant_id,
        artifact_id,
        {},
        datetime.now().isoformat()
    )
    
    # Проверяем usage
    period = datetime.now().strftime("%Y-%m")
    usage = billing_service.get_usage(tenant_id=tenant_id, period=period)
    
    prep_lines = [line for line in usage["lines"] if line["metric"] == "payment_prepared"]
    ready_lines = [line for line in usage["lines"] if line["metric"] == "payment_ready"]
    
    assert len(prep_lines) == 1, "Должна быть запись для payment_prepared"
    assert len(ready_lines) == 1, "Должна быть запись для payment_ready"


def test_invoice_aggregation_totals_by_metric(billing_service, billing_subscriber, artifact_service):
    """Тест: invoice агрегирует totals_by_metric."""
    tenant_id = "tenant-invoice"
    period = datetime.now().strftime("%Y-%m")
    
    # Создаем несколько событий для разных метрик
    billing_subscriber(
        "event-1", "document.extracted", tenant_id, "artifact-1", {},
        f"{period}-15T10:00:00"
    )
    billing_subscriber(
        "event-2", "document.extracted", tenant_id, "artifact-2", {},
        f"{period}-15T11:00:00"
    )
    billing_subscriber(
        "event-3", "payment.prepared", tenant_id, "artifact-3", {},
        f"{period}-15T12:00:00"
    )
    
    # Получаем invoice
    invoice = billing_service.get_invoice(tenant_id=tenant_id, period=period)
    
    assert invoice["tenant_id"] == tenant_id
    assert invoice["period"] == period
    assert "totals_by_metric" in invoice
    assert "invoice_extracted" in invoice["totals_by_metric"]
    assert "payment_prepared" in invoice["totals_by_metric"]
    assert invoice["totals_by_metric"]["invoice_extracted"]["units"] == 2.0
    assert invoice["totals_by_metric"]["payment_prepared"]["units"] == 1.0


def test_invoice_total_amount_matches_sum(billing_service, billing_subscriber, artifact_service):
    """Тест: invoice total_amount_minor совпадает с суммой totals_by_metric."""
    tenant_id = "tenant-invoice-sum"
    period = datetime.now().strftime("%Y-%m")
    
    # Создаем события
    billing_subscriber(
        "event-1", "document.extracted", tenant_id, "artifact-1", {},
        f"{period}-15T10:00:00"
    )
    billing_subscriber(
        "event-2", "payment.ready", tenant_id, "artifact-2", {},
        f"{period}-15T11:00:00"
    )
    
    # Получаем invoice
    invoice = billing_service.get_invoice(tenant_id=tenant_id, period=period)
    
    # Считаем сумму из totals_by_metric
    sum_by_metric = sum(
        metric_data["amount_minor"]
        for metric_data in invoice["totals_by_metric"].values()
    )
    
    assert invoice["total_amount_minor"] == sum_by_metric, \
        f"total_amount_minor ({invoice['total_amount_minor']}) должен совпадать с суммой totals_by_metric ({sum_by_metric})"


def test_upsert_tenant_rate_twice_updates_quota(billing_service):
    """Тест: upsert tenant-specific rate два раза подряд не падает и обновляет monthly_quota."""
    tenant_id = "tenant-upsert"
    metric = "invoice_extracted"
    
    # Первый upsert
    rate_id_1 = billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric=metric,
        unit_price_minor=50,
        currency="USD",
        active=True,
        monthly_quota=100
    )
    
    # Получаем тариф после первого upsert
    rate_1 = billing_service.resolve_rate(tenant_id, metric)
    assert rate_1 is not None
    assert rate_1["monthly_quota"] == 100
    assert rate_1["unit_price_minor"] == 50
    
    # Второй upsert (обновляем monthly_quota)
    rate_id_2 = billing_service.upsert_rate(
        tenant_id=tenant_id,
        metric=metric,
        unit_price_minor=50,
        currency="USD",
        active=True,
        monthly_quota=200  # Обновляем квоту
    )
    
    # Получаем тариф после второго upsert
    rate_2 = billing_service.resolve_rate(tenant_id, metric)
    assert rate_2 is not None
    assert rate_2["monthly_quota"] == 200, "monthly_quota должен быть обновлен"
    assert rate_2["unit_price_minor"] == 50
    # ID может быть разным (создается новый), но активный тариф должен быть обновлен


def test_upsert_default_rate_twice(billing_service):
    """Тест: upsert default rate (tenant_id=None) два раза подряд не падает."""
    metric = "test_metric"
    
    # Первый upsert (создаем default тариф)
    rate_id_1 = billing_service.upsert_rate(
        tenant_id=None,  # Default rate
        metric=metric,
        unit_price_minor=10,
        currency="USD",
        active=True,
        monthly_quota=50
    )
    
    # Проверяем, что тариф создан
    # Используем любой tenant_id для проверки default rate
    rate_1 = billing_service.resolve_rate("any-tenant", metric)
    assert rate_1 is not None
    assert rate_1["tenant_id"] is None, "Должен быть default тариф"
    assert rate_1["monthly_quota"] == 50
    assert rate_1["unit_price_minor"] == 10
    
    # Второй upsert (обновляем default тариф)
    rate_id_2 = billing_service.upsert_rate(
        tenant_id=None,  # Default rate
        metric=metric,
        unit_price_minor=15,  # Обновляем цену
        currency="USD",
        active=True,
        monthly_quota=100  # Обновляем квоту
    )
    
    # Проверяем, что тариф обновлен
    rate_2 = billing_service.resolve_rate("any-tenant", metric)
    assert rate_2 is not None
    assert rate_2["tenant_id"] is None, "Должен быть default тариф"
    assert rate_2["monthly_quota"] == 100, "monthly_quota должен быть обновлен"
    assert rate_2["unit_price_minor"] == 15, "unit_price_minor должен быть обновлен"
