"""Тесты для интеграции платежей с кейсами."""

import pytest
import tempfile
import os

from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(db_path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                else:
                    import logging
                    logging.warning(f"Не удалось удалить временный файл {db_path} после {max_retries} попыток")


@pytest.fixture
def services(temp_db):
    """Создать сервисы для тестов."""
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return payment_service, case_service


def test_submit_creates_case_task(services):
    """Тест: submit_for_approval создаёт задачу в кейсе."""
    payment_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём политику с шагами
    payment_service.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["director"]}]
    )
    
    # Создаём платёжное поручение с case_id
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant",
        case_id=case_id
    )
    
    # Отправляем на согласование
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Проверяем, что создана задача в кейсе
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_tasks WHERE case_id = ?", (case_id,))
    task = cur.fetchone()
    conn.close()
    
    assert task is not None
    assert "согласовать" in task["title"].lower() or "approve" in task["title"].lower()
    assert task["assignee_role"] == "director"
    
    # Проверяем audit event
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "payment_submitted"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None


def test_approve_creates_export_task(services):
    """Тест: approve создаёт задачу на экспорт."""
    payment_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём политику
    payment_service.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant",
        case_id=case_id
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    payment_service.approve(order_id, "tenant-123", "accountant")
    
    # Проверяем, что создана задача на экспорт
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_tasks WHERE case_id = ? ORDER BY created_at DESC", (case_id,))
    tasks = cur.fetchall()
    conn.close()
    
    # Должно быть 2 задачи: на согласование и на экспорт
    assert len(tasks) >= 1
    export_task = None
    for task in tasks:
        if "выгрузить" in task["title"].lower() or "export" in task["title"].lower():
            export_task = task
            break
    
    assert export_task is not None
    assert export_task["assignee_role"] == "accountant"
    
    # Проверяем audit event
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "payment_approved"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None


def test_reject_logs_case_event(services):
    """Тест: reject логирует событие в кейсе."""
    payment_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant",
        case_id=case_id
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    payment_service.reject(order_id, "tenant-123", "accountant", comment="Отклонено")
    
    # Проверяем audit event
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "payment_rejected"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None
