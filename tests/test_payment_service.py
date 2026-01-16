"""Тесты для PaymentService."""

import pytest
import tempfile
import os
import time

from cyberplat.payments.payment_service import PaymentService, PaymentNotFoundError, InvalidApprovalError


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
def payment_service_memory():
    """Создать PaymentService с in-memory БД для тестов."""
    service = PaymentService(db_path=":memory:")
    yield service
    service.close()


def test_create_payment_order(payment_service_memory):
    """Тест: create_payment_order создаёт поручение."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Получатель",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Оплата по договору",
        created_by_role="accountant"
    )
    
    assert order_id is not None
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order is not None
    assert order["tenant_id"] == "tenant-123"
    assert order["amount"] == 100000.0
    assert order["status"] == "draft"
    assert order["currency"] == "KZT"


def test_submit_for_approval_auto_approve(payment_service_memory):
    """Тест: submit_for_approval без политики -> auto-approve."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "approved"
    assert order["approved_at"] is not None


def test_submit_for_approval_with_policy(payment_service_memory):
    """Тест: submit_for_approval с политикой создаёт шаги."""
    # Создаём политику
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[
            {"max": 100000, "roles": ["accountant"]},
            {"max": 1000000, "roles": ["accountant", "director"]}
        ]
    )
    
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "pending_approval"
    
    # Проверяем шаги согласования
    approvals = payment_service_memory.get_approvals(order_id)
    assert len(approvals) == 1
    assert approvals[0]["required_role"] == "accountant"
    assert approvals[0]["status"] == "pending"


def test_approve_step(payment_service_memory):
    """Тест: approve одобряет шаг."""
    # Создаём политику
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    # Одобряем
    payment_service_memory.approve(order_id, "tenant-123", "accountant", comment="Одобрено")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "approved"
    
    approvals = payment_service_memory.get_approvals(order_id)
    assert approvals[0]["status"] == "approved"


def test_approve_wrong_role(payment_service_memory):
    """Тест: approve с неправильной ролью выбрасывает ошибку."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["director"]}]
    )
    
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    # Пытаемся одобрить с неправильной ролью
    with pytest.raises(InvalidApprovalError) as exc_info:
        payment_service_memory.approve(order_id, "tenant-123", "accountant")
    
    assert "роль" in str(exc_info.value).lower() or "role" in str(exc_info.value).lower()


def test_approve_idempotent(payment_service_memory):
    """Тест: повторное approve того же шага не падает."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    # Первое одобрение
    payment_service_memory.approve(order_id, "tenant-123", "accountant")
    
    # Второе одобрение (идемпотентно)
    payment_service_memory.approve(order_id, "tenant-123", "accountant")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "approved"


def test_reject(payment_service_memory):
    """Тест: reject отклоняет поручение."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    # Отклоняем
    payment_service_memory.reject(order_id, "tenant-123", "accountant", comment="Отклонено")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "rejected"
    assert order["rejected_at"] is not None


def test_reject_idempotent(payment_service_memory):
    """Тест: повторное reject не падает."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    
    # Первое отклонение
    payment_service_memory.reject(order_id, "tenant-123", "accountant")
    
    # Второе отклонение (идемпотентно)
    payment_service_memory.reject(order_id, "tenant-123", "accountant")
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "rejected"


def test_reject_after_approved_fails(payment_service_memory):
    """Тест: reject после approved выбрасывает ошибку."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    # Auto-approve (нет политики)
    
    # Пытаемся отклонить после одобрения
    with pytest.raises(InvalidApprovalError) as exc_info:
        payment_service_memory.reject(order_id, "tenant-123", "accountant")
    
    assert "экспортировано" in str(exc_info.value).lower() or "exported" in str(exc_info.value).lower()


def test_export_csv(payment_service_memory):
    """Тест: export в CSV создаёт файл."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id, "tenant-123")
    # Auto-approve
    
    result = payment_service_memory.export(order_id, "tenant-123", format="csv")
    
    assert result["format"] == "csv"
    assert result["file_path"] is not None
    assert os.path.exists(result["file_path"])
    
    order = payment_service_memory.get_payment_order(order_id)
    assert order["status"] == "exported"
    assert order["exported_at"] is not None


def test_export_not_approved_fails(payment_service_memory):
    """Тест: export неодобренного поручения выбрасывает ошибку."""
    order_id = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    # Пытаемся экспортировать draft
    with pytest.raises(InvalidApprovalError) as exc_info:
        payment_service_memory.export(order_id, "tenant-123", format="csv")
    
    assert "одобрено" in str(exc_info.value).lower() or "approved" in str(exc_info.value).lower()


def test_payment_policy_threshold_selection(payment_service_memory):
    """Тест: выбор threshold по сумме."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[
            {"max": 100000, "roles": ["accountant"]},
            {"max": 1000000, "roles": ["accountant", "director"]}
        ]
    )
    
    # Малая сумма -> первый threshold
    order_id1 = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=50000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id1, "tenant-123")
    approvals1 = payment_service_memory.get_approvals(order_id1)
    assert len(approvals1) == 1
    assert approvals1[0]["required_role"] == "accountant"
    
    # Большая сумма -> второй threshold
    order_id2 = payment_service_memory.create_payment_order(
        tenant_id="tenant-123",
        amount=500000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service_memory.submit_for_approval(order_id2, "tenant-123")
    approvals2 = payment_service_memory.get_approvals(order_id2)
    assert len(approvals2) == 2
    assert approvals2[0]["required_role"] == "accountant"
    assert approvals2[1]["required_role"] == "director"
