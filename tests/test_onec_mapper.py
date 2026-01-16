"""Тесты для OneC mapper."""

import pytest

from cyberplat.integrations.onec_mapper import (
    map_counterparty,
    map_contract,
    map_invoice,
    MappingValidationError
)


def test_map_counterparty_success():
    """Тест: map_counterparty успешно преобразует артефакт."""
    artifact = {
        "data": {
            "name": "ООО Тест",
            "inn": "1234567890",
            "address": "Москва, ул. Тестовая, 1"
        }
    }
    
    payload = map_counterparty(artifact)
    
    assert payload["name"] == "ООО Тест"
    assert payload["inn"] == "1234567890"
    assert payload["address"] == "Москва, ул. Тестовая, 1"


def test_map_counterparty_missing_name():
    """Тест: map_counterparty выбрасывает ошибку при отсутствии name."""
    artifact = {
        "data": {
            "inn": "1234567890"
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_counterparty(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"
    assert "name" in str(exc_info.value.message).lower() or "название" in str(exc_info.value.message).lower()


def test_map_counterparty_russian_fields():
    """Тест: map_counterparty поддерживает русские названия полей."""
    artifact = {
        "data": {
            "название": "ООО Тест",
            "ИНН": "1234567890"
        }
    }
    
    payload = map_counterparty(artifact)
    
    assert payload["name"] == "ООО Тест"
    assert payload["inn"] == "1234567890"


def test_map_contract_success():
    """Тест: map_contract успешно преобразует артефакт."""
    artifact = {
        "data": {
            "counterparty_id": "cp-123",
            "number": "Д-001",
            "date": "2025-01-15",
            "amount": 100000
        }
    }
    
    payload = map_contract(artifact)
    
    assert payload["counterparty_id"] == "cp-123"
    assert payload["number"] == "Д-001"
    assert payload["date"] == "2025-01-15"
    assert payload["amount"] == 100000


def test_map_contract_with_counterparty_id_param():
    """Тест: map_contract использует counterparty_id из параметра."""
    artifact = {
        "data": {
            "number": "Д-001"
        }
    }
    
    payload = map_contract(artifact, counterparty_id="cp-456")
    
    assert payload["counterparty_id"] == "cp-456"
    assert payload["number"] == "Д-001"


def test_map_contract_missing_counterparty_id():
    """Тест: map_contract выбрасывает ошибку при отсутствии counterparty_id."""
    artifact = {
        "data": {
            "number": "Д-001"
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_contract(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"
    assert "counterparty_id" in str(exc_info.value.message).lower() or "контрагент_id" in str(exc_info.value.message).lower()


def test_map_contract_missing_number():
    """Тест: map_contract выбрасывает ошибку при отсутствии number."""
    artifact = {
        "data": {
            "counterparty_id": "cp-123"
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_contract(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"
    assert "number" in str(exc_info.value.message).lower() or "номер" in str(exc_info.value.message).lower()


def test_map_invoice_success():
    """Тест: map_invoice успешно преобразует артефакт."""
    artifact = {
        "data": {
            "counterparty_id": "cp-123",
            "number": "СЧ-001",
            "amount": 50000,
            "date": "2025-01-15"
        }
    }
    
    payload = map_invoice(artifact)
    
    assert payload["counterparty_id"] == "cp-123"
    assert payload["number"] == "СЧ-001"
    assert payload["amount"] == 50000
    assert payload["date"] == "2025-01-15"


def test_map_invoice_with_params():
    """Тест: map_invoice использует counterparty_id и contract_id из параметров."""
    artifact = {
        "data": {
            "number": "СЧ-001",
            "amount": 50000
        }
    }
    
    payload = map_invoice(artifact, counterparty_id="cp-456", contract_id="contract-789")
    
    assert payload["counterparty_id"] == "cp-456"
    assert payload["contract_id"] == "contract-789"
    assert payload["number"] == "СЧ-001"
    assert payload["amount"] == 50000


def test_map_invoice_missing_counterparty_id():
    """Тест: map_invoice выбрасывает ошибку при отсутствии counterparty_id."""
    artifact = {
        "data": {
            "number": "СЧ-001",
            "amount": 50000
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_invoice(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"


def test_map_invoice_missing_number():
    """Тест: map_invoice выбрасывает ошибку при отсутствии number."""
    artifact = {
        "data": {
            "counterparty_id": "cp-123",
            "amount": 50000
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_invoice(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"


def test_map_invoice_missing_amount():
    """Тест: map_invoice выбрасывает ошибку при отсутствии amount."""
    artifact = {
        "data": {
            "counterparty_id": "cp-123",
            "number": "СЧ-001"
        }
    }
    
    with pytest.raises(MappingValidationError) as exc_info:
        map_invoice(artifact)
    
    assert exc_info.value.error_code == "missing_required_field"
