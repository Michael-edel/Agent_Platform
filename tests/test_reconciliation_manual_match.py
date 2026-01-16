"""Тесты для ручного сопоставления транзакций."""

import pytest
import tempfile
import os
import csv
from pathlib import Path

from cyberplat.reconciliation.reconciliation_service import ReconciliationService, ReconciliationError
from cyberplat.payments.payment_service import PaymentService


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
    return reconciliation_service, payment_service


def test_manual_match(services):
    """Тест: manual_match создаёт сопоставление."""
    reconciliation_service, payment_service = services
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата",
        created_by_role="accountant"
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
    
    # Ручное сопоставление
    match_id = reconciliation_service.manual_match(
        tenant_id="tenant-123",
        transaction_id=transaction_id,
        payment_order_id=order_id
    )
    
    assert match_id is not None
    
    # Проверяем match
    matches = reconciliation_service.get_matches(statement_id=statement_id)
    assert len(matches) == 1
    assert matches[0]["match_type"] == "manual"
    assert matches[0]["confidence"] == 1.0
    assert matches[0]["payment_order_id"] == order_id
    
    # Проверяем транзакцию
    transactions = reconciliation_service.get_transactions(statement_id, matched=True)
    assert len(transactions) == 1
    assert transactions[0]["matched"] is True
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_manual_match_duplicate_fails(services):
    """Тест: повторное manual_match выбрасывает ошибку."""
    reconciliation_service, payment_service = services
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата",
        created_by_role="accountant"
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
    
    # Первое сопоставление
    reconciliation_service.manual_match(
        tenant_id="tenant-123",
        transaction_id=transaction_id,
        payment_order_id=order_id
    )
    
    # Повторное сопоставление (должно выбросить ошибку)
    with pytest.raises(ReconciliationError) as exc_info:
        reconciliation_service.manual_match(
            tenant_id="tenant-123",
            transaction_id=transaction_id,
            payment_order_id=order_id
        )
    
    assert "уже сопоставлена" in str(exc_info.value).lower() or "already" in str(exc_info.value).lower()
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_manual_match_invalid_transaction(services):
    """Тест: manual_match с несуществующей транзакцией выбрасывает ошибку."""
    reconciliation_service, payment_service = services
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата",
        created_by_role="accountant"
    )
    
    with pytest.raises(ReconciliationError) as exc_info:
        reconciliation_service.manual_match(
            tenant_id="tenant-123",
            transaction_id="non-existent",
            payment_order_id=order_id
        )
    
    assert "не найдена" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


def test_finalize_statement(services):
    """Тест: finalize_statement помечает выписку как reconciled."""
    reconciliation_service, payment_service = services
    
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    reconciliation_service.finalize_statement(statement_id, "tenant-123")
    
    statement = reconciliation_service.get_statement(statement_id)
    assert statement["status"] == "reconciled"
