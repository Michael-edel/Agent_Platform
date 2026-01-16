"""Тесты для политики согласования платежей."""

import pytest
import tempfile
import os

from cyberplat.payments.payment_service import PaymentService


@pytest.fixture
def payment_service_memory():
    """Создать PaymentService с in-memory БД для тестов."""
    service = PaymentService(db_path=":memory:")
    yield service
    service.close()


def test_policy_threshold_rules(payment_service_memory):
    """Тест: правила выбора threshold по сумме."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[
            {"max": 100000, "roles": ["accountant"]},
            {"max": 500000, "roles": ["accountant", "manager"]},
            {"max": 999999999, "roles": ["accountant", "manager", "director"]}
        ]
    )
    
    # Сумма <= 100000 -> первый threshold
    steps1 = payment_service_memory._build_approval_steps("tenant-123", 50000.0)
    assert len(steps1) == 1
    assert steps1[0]["required_role"] == "accountant"
    
    # Сумма <= 500000 -> второй threshold
    steps2 = payment_service_memory._build_approval_steps("tenant-123", 200000.0)
    assert len(steps2) == 2
    assert steps2[0]["required_role"] == "accountant"
    assert steps2[1]["required_role"] == "manager"
    
    # Сумма > 500000 -> третий threshold
    steps3 = payment_service_memory._build_approval_steps("tenant-123", 1000000.0)
    assert len(steps3) == 3
    assert steps3[0]["required_role"] == "accountant"
    assert steps3[1]["required_role"] == "manager"
    assert steps3[2]["required_role"] == "director"


def test_policy_disabled_auto_approve(payment_service_memory):
    """Тест: отключённая политика -> auto-approve."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=False,
        thresholds=[{"max": 1000000, "roles": ["director"]}]
    )
    
    steps = payment_service_memory._build_approval_steps("tenant-123", 50000.0)
    assert len(steps) == 0  # Auto-approve


def test_policy_not_found_auto_approve(payment_service_memory):
    """Тест: отсутствие политики -> auto-approve."""
    steps = payment_service_memory._build_approval_steps("tenant-123", 50000.0)
    assert len(steps) == 0  # Auto-approve


def test_policy_upsert_get(payment_service_memory):
    """Тест: upsert_payment_policy -> get_payment_policy."""
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 100000, "roles": ["accountant"]}]
    )
    
    policy = payment_service_memory.get_payment_policy("tenant-123")
    assert policy is not None
    assert policy["enabled"] is True
    assert len(policy["thresholds"]) == 1
    assert policy["thresholds"][0]["max"] == 100000


def test_policy_update(payment_service_memory):
    """Тест: обновление политики."""
    # Создаём
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 100000, "roles": ["accountant"]}]
    )
    
    # Обновляем
    payment_service_memory.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=False,
        thresholds=[{"max": 500000, "roles": ["director"]}]
    )
    
    policy = payment_service_memory.get_payment_policy("tenant-123")
    assert policy["enabled"] is False
    assert len(policy["thresholds"]) == 1
    assert policy["thresholds"][0]["max"] == 500000
