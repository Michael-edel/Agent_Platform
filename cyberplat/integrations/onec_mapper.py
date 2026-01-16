"""Mapping layer: Artifact -> 1C payload."""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MappingValidationError(Exception):
    """Ошибка валидации данных для маппинга."""
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(message)


def map_counterparty(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Преобразовать артефакт в payload для создания контрагента в 1С.
    
    Args:
        artifact: Артефакт с данными контрагента
        
    Returns:
        Payload для API 1С
        
    Raises:
        MappingValidationError: Если обязательные поля отсутствуют
    """
    data = artifact.get("data", {})
    
    # Обязательные поля
    if "name" not in data and "название" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'name' или 'название'"
        )
    
    name = data.get("name") or data.get("название")
    
    # Формируем payload
    payload = {
        "name": name
    }
    
    # Опциональные поля
    if "inn" in data or "ИНН" in data:
        payload["inn"] = data.get("inn") or data.get("ИНН")
    
    if "kpp" in data or "КПП" in data:
        payload["kpp"] = data.get("kpp") or data.get("КПП")
    
    if "address" in data or "адрес" in data:
        payload["address"] = data.get("address") or data.get("адрес")
    
    if "phone" in data or "телефон" in data:
        payload["phone"] = data.get("phone") or data.get("телефон")
    
    if "email" in data or "email" in data:
        payload["email"] = data.get("email")
    
    return payload


def map_contract(artifact: Dict[str, Any], counterparty_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Преобразовать артефакт в payload для создания договора в 1С.
    
    Args:
        artifact: Артефакт с данными договора
        counterparty_id: ID контрагента в 1С (если уже создан)
        
    Returns:
        Payload для API 1С
        
    Raises:
        MappingValidationError: Если обязательные поля отсутствуют
    """
    data = artifact.get("data", {})
    
    # Обязательные поля
    if not counterparty_id and "counterparty_id" not in data and "контрагент_id" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'counterparty_id' или 'контрагент_id'"
        )
    
    counterparty_ref = counterparty_id or data.get("counterparty_id") or data.get("контрагент_id")
    
    if "number" not in data and "номер" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'number' или 'номер'"
        )
    
    number = data.get("number") or data.get("номер")
    
    # Формируем payload
    payload = {
        "counterparty_id": counterparty_ref,
        "number": number
    }
    
    # Опциональные поля
    if "date" in data or "дата" in data:
        payload["date"] = data.get("date") or data.get("дата")
    
    if "amount" in data or "сумма" in data:
        payload["amount"] = data.get("amount") or data.get("сумма")
    
    if "currency" in data or "валюта" in data:
        payload["currency"] = data.get("currency") or data.get("валюта")
    
    return payload


def map_invoice(artifact: Dict[str, Any], counterparty_id: Optional[str] = None, contract_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Преобразовать артефакт в payload для создания счёта в 1С.
    
    Args:
        artifact: Артефакт с данными счёта
        counterparty_id: ID контрагента в 1С (если уже создан)
        contract_id: ID договора в 1С (если уже создан)
        
    Returns:
        Payload для API 1С
        
    Raises:
        MappingValidationError: Если обязательные поля отсутствуют
    """
    data = artifact.get("data", {})
    
    # Обязательные поля
    if not counterparty_id and "counterparty_id" not in data and "контрагент_id" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'counterparty_id' или 'контрагент_id'"
        )
    
    counterparty_ref = counterparty_id or data.get("counterparty_id") or data.get("контрагент_id")
    
    if "number" not in data and "номер" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'number' или 'номер'"
        )
    
    number = data.get("number") or data.get("номер")
    
    if "amount" not in data and "сумма" not in data:
        raise MappingValidationError(
            error_code="missing_required_field",
            message="Отсутствует обязательное поле 'amount' или 'сумма'"
        )
    
    amount = data.get("amount") or data.get("сумма")
    
    # Формируем payload
    payload = {
        "counterparty_id": counterparty_ref,
        "number": number,
        "amount": amount
    }
    
    # Опциональные поля
    if contract_id or "contract_id" in data or "договор_id" in data:
        payload["contract_id"] = contract_id or data.get("contract_id") or data.get("договор_id")
    
    if "date" in data or "дата" in data:
        payload["date"] = data.get("date") or data.get("дата")
    
    if "currency" in data or "валюта" in data:
        payload["currency"] = data.get("currency") or data.get("валюта")
    
    if "items" in data or "позиции" in data:
        payload["items"] = data.get("items") or data.get("позиции")
    
    return payload
