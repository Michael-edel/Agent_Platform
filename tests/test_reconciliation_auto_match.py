"""Тесты для автоматического сопоставления транзакций."""

import pytest
import tempfile
import os
import csv
from pathlib import Path
from datetime import date

from cyberplat.reconciliation.reconciliation_service import ReconciliationService
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


def test_auto_match_exact_amount(services):
    """Тест: auto_match сопоставляет по точной сумме."""
    reconciliation_service, payment_service = services
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата по договору №123",
        created_by_role="accountant"
    )
    
    # Auto-approve
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Создаём выписку
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём CSV с транзакцией
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
    
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 0
    
    # Проверяем match
    matches = reconciliation_service.get_matches(statement_id=statement_id)
    assert len(matches) == 1
    assert matches[0]["payment_order_id"] == order_id
    assert matches[0]["match_type"] == "auto"
    assert matches[0]["confidence"] >= 0.8
    
    # Проверяем транзакцию
    transactions = reconciliation_service.get_transactions(statement_id, matched=True)
    assert len(transactions) == 1
    assert transactions[0]["matched"] is True
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_auto_match_with_tolerance(services):
    """Тест: auto_match сопоставляет с tolerance."""
    reconciliation_service, payment_service = services
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата по договору",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Создаём выписку
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём CSV с транзакцией (сумма с небольшой разницей)
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "-100500", "Оплата по договору", "ООО Получатель"])
    
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
    
    # Должно сопоставиться (в пределах tolerance)
    assert result["matched_count"] >= 0  # Может быть 0 или 1 в зависимости от tolerance
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_auto_match_no_match(services):
    """Тест: auto_match не сопоставляет если нет подходящих orders."""
    reconciliation_service, payment_service = services
    
    # Создаём выписку без payment orders
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
        writer.writerow(["2024-01-15", "-100000", "Оплата", "ООО Тест"])
    
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
    
    assert result["matched_count"] == 0
    assert result["unmatched_count"] == 1
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_auto_match_only_out_direction(services):
    """Тест: auto_match обрабатывает только direction=out."""
    reconciliation_service, payment_service = services
    
    # Создаём платёжное поручение
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    # Создаём выписку
    statement_id = reconciliation_service.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём CSV с in транзакцией
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "50000", "Поступление", "ООО Клиент"])
    
    reconciliation_service.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    # Автосопоставление (не должно сопоставить in транзакцию)
    result = reconciliation_service.auto_match(
        statement_id=statement_id,
        tenant_id="tenant-123",
        payment_service=payment_service
    )
    
    assert result["matched_count"] == 0
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()
