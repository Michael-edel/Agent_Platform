"""Тесты для stuck detectors Money Ops."""

import pytest
import tempfile
import os
import time
from datetime import datetime, timedelta

from cyberplat.money_ops.stuck_detectors import detect_stuck_approvals, detect_stuck_reconciliation
from cyberplat.payments.payment_service import PaymentService
from cyberplat.reconciliation.reconciliation_service import ReconciliationService
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
    reconciliation_service = ReconciliationService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return payment_service, reconciliation_service, case_service


def test_detect_stuck_approvals(services):
    """Тест: detect_stuck_approvals находит застрявшие approvals."""
    payment_service, reconciliation_service, case_service = services
    
    # Создаём политику
    payment_service.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["director"]}]
    )
    
    # Создаём payment order
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    # Отправляем на согласование
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Обновляем updated_at на старую дату (симулируем stuck)
    conn = payment_service._get_connection()
    cur = conn.cursor()
    old_time = (datetime.now() - timedelta(hours=9)).isoformat()
    cur.execute(
        "UPDATE payment_orders SET updated_at = ? WHERE id = ?",
        (old_time, order_id)
    )
    conn.commit()
    conn.close()
    
    # Обнаруживаем stuck
    stuck = detect_stuck_approvals(
        payment_service=payment_service,
        case_service=case_service,
        threshold_hours=8
    )
    
    assert len(stuck) == 1
    assert stuck[0]["order_id"] == order_id
    assert stuck[0]["stuck_hours"] >= 8


def test_detect_stuck_approvals_creates_case_task(services):
    """Тест: detect_stuck_approvals создаёт задачу в кейсе."""
    payment_service, reconciliation_service, case_service = services
    
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
        thresholds=[{"max": 1000000, "roles": ["director"]}]
    )
    
    # Создаём payment order с case_id
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
    
    # Обновляем updated_at на старую дату
    conn = payment_service._get_connection()
    cur = conn.cursor()
    old_time = (datetime.now() - timedelta(hours=9)).isoformat()
    cur.execute(
        "UPDATE payment_orders SET updated_at = ? WHERE id = ?",
        (old_time, order_id)
    )
    conn.commit()
    conn.close()
    
    # Обнаруживаем stuck
    detect_stuck_approvals(
        payment_service=payment_service,
        case_service=case_service,
        threshold_hours=8
    )
    
    # Проверяем, что создана задача
    conn = case_service._get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM case_tasks WHERE case_id = ? AND title LIKE '%согласован%'",
        (case_id,)
    )
    task = cur.fetchone()
    conn.close()
    
    assert task is not None
    assert "согласован" in task["title"].lower() or "approval" in task["title"].lower()


def test_detect_stuck_reconciliation(services):
    """Тест: detect_stuck_reconciliation находит застрявшие выписки."""
    payment_service, reconciliation_service, case_service = services
    
    # Создаём выписку
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Обновляем created_at на старую дату
    conn = reconciliation_service._get_connection()
    cur = conn.cursor()
    old_time = (datetime.now() - timedelta(hours=25)).isoformat()
    cur.execute(
        "UPDATE bank_statements SET created_at = ?, status = 'parsed' WHERE id = ?",
        (old_time, statement_id)
    )
    
    # Создаём unmatched транзакцию
    txn_id = "test-txn-id"
    cur.execute(
        """
        INSERT INTO bank_transactions
        (id, statement_id, tenant_id, txn_date, amount, currency, direction, matched, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (txn_id, statement_id, "tenant-123", datetime.now().date().isoformat(), 100000.0, "KZT", "out", datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    # Обнаруживаем stuck
    stuck = detect_stuck_reconciliation(
        reconciliation_service=reconciliation_service,
        case_service=case_service,
        threshold_hours=24
    )
    
    assert len(stuck) == 1
    assert stuck[0]["statement_id"] == statement_id
    assert stuck[0]["unmatched_count"] >= 1


def test_detect_stuck_approvals_no_stuck(services):
    """Тест: detect_stuck_approvals не находит недавние approvals."""
    payment_service, reconciliation_service, case_service = services
    
    # Создаём недавний payment order
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Обнаруживаем stuck (не должно найти)
    stuck = detect_stuck_approvals(
        payment_service=payment_service,
        case_service=case_service,
        threshold_hours=8
    )
    
    assert len(stuck) == 0
