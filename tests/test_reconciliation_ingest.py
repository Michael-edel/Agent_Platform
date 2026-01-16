"""Тесты для ingestion банковских выписок."""

import pytest
import tempfile
import os
import csv
from pathlib import Path

from cyberplat.reconciliation.reconciliation_service import ReconciliationService, ReconciliationError


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
def reconciliation_service_memory():
    """Создать ReconciliationService с in-memory БД для тестов."""
    service = ReconciliationService(db_path=":memory:")
    yield service
    service.close()


def test_ingest_statement(reconciliation_service_memory):
    """Тест: ingest_statement создаёт выписку."""
    statement_id = reconciliation_service_memory.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    assert statement_id is not None
    
    statement = reconciliation_service_memory.get_statement(statement_id)
    assert statement is not None
    assert statement["tenant_id"] == "tenant-123"
    assert statement["source"] == "upload"
    assert statement["status"] == "uploaded"


def test_parse_transactions_csv(reconciliation_service_memory):
    """Тест: parse_transactions_csv парсит CSV файл."""
    # Создаём выписку
    statement_id = reconciliation_service_memory.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём тестовый CSV файл
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test_statement.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "-100000", "Оплата по договору №123", "ООО Поставщик"])
        writer.writerow(["2024-01-16", "50000", "Поступление", "ООО Клиент"])
    
    # Парсим
    count = reconciliation_service_memory.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    assert count == 2
    
    # Проверяем статус
    statement = reconciliation_service_memory.get_statement(statement_id)
    assert statement["status"] == "parsed"
    
    # Проверяем транзакции
    transactions = reconciliation_service_memory.get_transactions(statement_id)
    assert len(transactions) == 2
    
    # Проверяем первую транзакцию (out)
    txn1 = transactions[0]  # Последняя по дате
    assert txn1["amount"] == 50000.0
    assert txn1["direction"] == "in"
    
    # Проверяем вторую транзакцию (out)
    txn2 = transactions[1]
    assert txn2["amount"] == 100000.0
    assert txn2["direction"] == "out"
    assert "Оплата" in txn2["description"]
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_parse_transactions_csv_invalid_format(reconciliation_service_memory):
    """Тест: parse_transactions_csv обрабатывает некорректный формат."""
    statement_id = reconciliation_service_memory.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём некорректный CSV
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "invalid.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        f.write("invalid,format\n")
        f.write("not,valid,data\n")
    
    # Парсим (должно пропустить некорректные строки)
    count = reconciliation_service_memory.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    # Должно быть 0 транзакций (некорректный формат)
    assert count == 0
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()


def test_get_transactions_filtered(reconciliation_service_memory):
    """Тест: get_transactions с фильтрацией по matched."""
    statement_id = reconciliation_service_memory.ingest_statement(
        tenant_id="tenant-123",
        source="upload"
    )
    
    # Создаём CSV с транзакциями
    temp_dir = Path("out/test_uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = temp_dir / "test.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description", "counterparty"])
        writer.writerow(["2024-01-15", "-100000", "Оплата", "ООО Тест"])
    
    reconciliation_service_memory.parse_transactions_csv(
        statement_id=statement_id,
        tenant_id="tenant-123",
        file_path=str(csv_path)
    )
    
    # Все unmatched
    unmatched = reconciliation_service_memory.get_transactions(
        statement_id=statement_id,
        matched=False
    )
    assert len(unmatched) == 1
    
    # Matched (пусто)
    matched = reconciliation_service_memory.get_transactions(
        statement_id=statement_id,
        matched=True
    )
    assert len(matched) == 0
    
    # Очистка
    if csv_path.exists():
        csv_path.unlink()
