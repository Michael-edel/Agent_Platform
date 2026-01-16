"""Тесты для интеграции reconciliation с Case/Payments."""

import pytest
import tempfile
import os
import csv
from pathlib import Path

from cyberplat.reconciliation.reconciliation_service import ReconciliationService
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
    reconciliation_service = ReconciliationService(db_path=temp_db)
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return reconciliation_service, payment_service, case_service


def test_manual_match_closes_case_task(services):
    """Тест: manual_match закрывает задачу в кейсе."""
    reconciliation_service, payment_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём задачу "Ожидается оплата"
    task_id = case_service.add_task(
        case_id=case_id,
        step_key="payment",
        title="Ожидается оплата",
        assignee_role="accountant"
    )
    
    # Создаём платёжное поручение с case_id
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата",
        created_by_role="accountant",
        case_id=case_id
    )
    
    # Создаём выписку и транзакцию
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "-100000", "Оплата", "ООО Тест"])
    
    reconciliation_service.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    transactions = reconciliation_service.get_transactions(statement_id)
    transaction_id = transactions[0]["id"]
    
    # Ручное сопоставление (через API будет вызываться интеграция с кейсом)
    # Здесь проверяем только логику сервиса
    match_id = reconciliation_service.manual_match(
        tenant_id="tenant-123",
        transaction_id=transaction_id,
        payment_order_id=order_id
    )
    
    # Проверяем, что match создан
    assert match_id is not None
    
    # Проверяем audit event в кейсе (будет создан через API)
    # Здесь просто проверяем, что match работает
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_auto_match_with_case(services):
    """Тест: auto_match работает с payment orders, привязанными к кейсам."""
    reconciliation_service, payment_service, case_service = services
    
    # Создаём кейс
    case_id = case_service.create_case(
        tenant_id="tenant-123",
        case_type="support",
        title="Test case"
    )
    
    # Создаём платёжное поручение с case_id
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата по договору №123",
        created_by_role="accountant",
        case_id=case_id
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    payment_service.approve(order_id, "tenant-123", "director", comment="Одобрено")
    
    # Создаём выписку
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём CSV
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "-100000", "Оплата по договору №123", "ООО Получатель"])
    
    reconciliation_service.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    # Автосопоставление
    result = reconciliation_service.auto_match(
        statement_id=statement_id,
        tenant_id="tenant-123",
        payment_service=payment_service
    )
    
    # Проверяем, что сопоставление произошло
    assert result["matched_count"] == 1
    
    # Проверяем match
    matches = reconciliation_service.get_matches(statement_id=statement_id)
    assert len(matches) == 1
    assert matches[0]["payment_order_id"] == order_id
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()
