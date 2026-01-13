"""Тесты для payment_agent с поддержкой RU и KZ."""

import pytest
import tempfile
import os
from pathlib import Path

from cyberplat.agents.payment_agent import PaymentAgent
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService
from cyberplat.base_agent import AgentContext


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        # Retry логика для Windows (файл может быть временно заблокирован)
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
    event_service = EventService(db_path=temp_db)
    artifact_service = ArtifactService(db_path=temp_db, event_service=event_service)
    event_service.artifact_service = artifact_service
    return artifact_service, event_service


@pytest.fixture
def payment_agent(services):
    """Создать PaymentAgent для тестов."""
    artifact_service, event_service = services
    return PaymentAgent(artifact_service=artifact_service, event_service=event_service)


def test_detect_country_kz_by_iban(payment_agent):
    """Тест: определение KZ по IBAN (правило 1)."""
    country = payment_agent._detect_country(
        account_iban="KZ67914002203KZ0066V",
        bik=None
    )
    assert country == "KZ"


def test_detect_country_kz_by_swift(payment_agent):
    """Тест: определение KZ по SWIFT/BIC с буквами (правило 2)."""
    country = payment_agent._detect_country(
        account_iban=None,
        bik="SABRKZKA"
    )
    assert country == "KZ"


def test_detect_country_ru_by_bik(payment_agent):
    """Тест: определение RU по БИК 9 цифр (правило 3)."""
    country = payment_agent._detect_country(
        account_iban="40702810707000078053",
        bik="045004799"
    )
    assert country == "RU"


def test_detect_country_kz_default(payment_agent):
    """Тест: default KZ при неопределенности (правило 4)."""
    country = payment_agent._detect_country(
        account_iban=None,
        bik=None
    )
    assert country == "KZ"
    
    # Также если BIK не подходит ни под одно правило
    country2 = payment_agent._detect_country(
        account_iban="12345",
        bik="12345"
    )
    assert country2 == "KZ"


def test_validate_iban_kz_valid(payment_agent):
    """Тест: валидация валидного IBAN KZ."""
    valid, error, cleaned = payment_agent._validate_iban_kz("KZ67914002203KZ0066V")
    assert valid is True
    assert error is None
    assert cleaned == "KZ67914002203KZ0066V"


def test_validate_iban_kz_invalid(payment_agent):
    """Тест: валидация невалидного IBAN KZ."""
    valid, error, cleaned = payment_agent._validate_iban_kz("KZ123")
    assert valid is False
    assert error is not None
    assert "20 символов" in error


def test_validate_bic_swift_valid(payment_agent):
    """Тест: валидация валидного BIC/SWIFT."""
    valid, error, cleaned = payment_agent._validate_bic_swift("SABRKZKA")
    assert valid is True
    assert error is None
    assert cleaned == "SABRKZKA"


def test_validate_bic_swift_invalid(payment_agent):
    """Тест: валидация невалидного BIC/SWIFT."""
    valid, error, cleaned = payment_agent._validate_bic_swift("SABR")
    assert valid is False
    assert error is not None
    assert "8 или 11" in error


def test_validate_bik_ru_valid(payment_agent):
    """Тест: валидация валидного БИК РФ."""
    valid, error, cleaned = payment_agent._validate_bik_ru("045004799")
    assert valid is True
    assert error is None
    assert cleaned == "045004799"


def test_validate_bik_ru_invalid(payment_agent):
    """Тест: валидация невалидного БИК РФ."""
    valid, error, cleaned = payment_agent._validate_bik_ru("12345")
    assert valid is False
    assert error is not None
    assert "9 цифр" in error


def test_validate_account_ru_valid(payment_agent):
    """Тест: валидация валидного счета РФ."""
    valid, error, cleaned = payment_agent._validate_account_ru("40702810707000078053")
    assert valid is True
    assert error is None
    assert cleaned == "40702810707000078053"


def test_validate_account_ru_invalid(payment_agent):
    """Тест: валидация невалидного счета РФ."""
    valid, error, cleaned = payment_agent._validate_account_ru("12345")
    assert valid is False
    assert error is not None
    assert "20 цифр" in error


@pytest.mark.asyncio
async def test_payment_agent_kz_ready(payment_agent, services):
    """Тест: payment_agent создает payment.ready для валидного KZ счета."""
    artifact_service, event_service = services
    
    # Создаем invoice артефакт с KZ реквизитами
    invoice_data = {
        "total": "750,00",
        "payment": {
            "beneficiary_bank_name": "Народный Банк Казахстана",
            "beneficiary_bank_bik": "SABRKZKA",
            "beneficiary_account_iban": "KZ67914002203KZ0066V",
            "payment_purpose": "Оплата счета"
        },
        "supplier": {"name": "ТОО Тест", "bin_iin": "123456789012"},
        "buyer": {"name": "ООО Покупатель", "bin_iin": "987654321098"}
    }
    
    invoice_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Запускаем payment_agent
    context = AgentContext(
        artifact_id=invoice_id,
        file_id=None,
        tenant_id="tenant-123"
    )
    
    result = await payment_agent.run(context)
    
    assert result["success"] is True
    assert result["artifact_id"] is not None
    assert result["validation"]["is_ready"] is True
    assert result["validation"]["country"] == "KZ"
    
    # Проверяем payment артефакт
    payment_artifact = artifact_service.get_artifact(result["artifact_id"])
    assert payment_artifact is not None
    assert payment_artifact["kind"] == "payment"
    
    payment_data = payment_artifact["data"]
    assert payment_data["currency"] == "KZT"
    assert payment_data["bank"]["country"] == "KZ"
    assert payment_data["bank"]["iban"] == "KZ67914002203KZ0066V"
    assert payment_data["bank"]["bic_swift"] == "SABRKZKA"


@pytest.mark.asyncio
async def test_payment_agent_kz_without_bic_ready(payment_agent, services):
    """Тест: payment_agent создает payment.ready для KZ без BIC (BIC не обязателен)."""
    artifact_service, event_service = services
    
    # Создаем invoice артефакт с KZ реквизитами без BIC
    invoice_data = {
        "total": "750,00",
        "payment": {
            "beneficiary_bank_name": "Народный Банк Казахстана",
            "beneficiary_bank_bik": None,  # BIC отсутствует
            "beneficiary_account_iban": "KZ67914002203KZ0066V",
            "payment_purpose": "Оплата счета"
        },
        "supplier": {"name": "ТОО Тест", "bin_iin": "123456789012"},
        "buyer": {"name": "ООО Покупатель", "bin_iin": "987654321098"}
    }
    
    invoice_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Запускаем payment_agent
    context = AgentContext(
        artifact_id=invoice_id,
        file_id=None,
        tenant_id="tenant-123"
    )
    
    result = await payment_agent.run(context)
    
    # Должно быть ready, так как IBAN валиден, а BIC не обязателен
    assert result["success"] is True
    assert result["validation"]["is_ready"] is True
    assert result["validation"]["country"] == "KZ"
    # Должно быть warning о BIC, но не error
    assert any("BIC/SWIFT отсутствует" in w for w in result["validation"]["warnings"])
    assert len(result["validation"]["errors"]) == 0
    
    # Проверяем payment артефакт
    payment_artifact = artifact_service.get_artifact(result["artifact_id"])
    payment_data = payment_artifact["data"]
    assert payment_data["currency"] == "KZT"
    assert payment_data["bank"]["country"] == "KZ"
    assert payment_data["bank"]["iban"] == "KZ67914002203KZ0066V"
    assert "bic_swift" not in payment_data["bank"] or payment_data["bank"].get("bic_swift") is None


@pytest.mark.asyncio
async def test_payment_agent_ru_ready(payment_agent, services):
    """Тест: payment_agent создает payment.ready для валидного RU счета."""
    artifact_service, event_service = services
    
    # Создаем invoice артефакт с RU реквизитами
    invoice_data = {
        "total": "750,00",
        "payment": {
            "beneficiary_bank_name": "РАЙФФАЙЗЕНБАНК",
            "beneficiary_bank_bik": "045004799",
            "beneficiary_account_iban": "40702810707000078053",
            "payment_purpose": "Оплата счета"
        },
        "supplier": {"name": "ООО Поставщик", "bin_iin": "7017248475"},
        "buyer": {"name": "ООО Покупатель", "bin_iin": "123456789012"}
    }
    
    invoice_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Запускаем payment_agent
    context = AgentContext(
        artifact_id=invoice_id,
        file_id=None,
        tenant_id="tenant-123"
    )
    
    result = await payment_agent.run(context)
    
    assert result["success"] is True
    assert result["artifact_id"] is not None
    assert result["validation"]["is_ready"] is True
    assert result["validation"]["country"] == "RU"
    
    # Проверяем payment артефакт
    payment_artifact = artifact_service.get_artifact(result["artifact_id"])
    assert payment_artifact is not None
    assert payment_artifact["kind"] == "payment"
    
    payment_data = payment_artifact["data"]
    assert payment_data["currency"] == "RUB"
    assert payment_data["bank"]["country"] == "RU"
    assert payment_data["bank"]["bik_ru"] == "045004799"
    assert payment_data["bank"]["account_ru"] == "40702810707000078053"


@pytest.mark.asyncio
async def test_payment_agent_country_hint_priority(payment_agent, services):
    """Тест: country_hint имеет приоритет над detection по реквизитам."""
    artifact_service, event_service = services
    
    # Создаем invoice с country_hint="RU", но реквизиты похожи на KZ
    invoice_data = {
        "country_hint": "RU",  # Явно указан RU
        "total": "750,00",
        "payment": {
            "beneficiary_bank_name": "Банк",
            "beneficiary_bank_bik": "SABRKZKA",  # SWIFT (обычно KZ)
            "beneficiary_account_iban": "KZ67914002203KZ0066V",  # IBAN KZ
            "payment_purpose": "Оплата счета"
        },
        "supplier": {"name": "ООО Поставщик", "bin_iin": "7017248475"},
        "buyer": {"name": "ООО Покупатель", "bin_iin": "123456789012"}
    }
    
    invoice_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Запускаем payment_agent
    context = AgentContext(
        artifact_id=invoice_id,
        file_id=None,
        tenant_id="tenant-123"
    )
    
    result = await payment_agent.run(context)
    
    # Должен использовать country_hint="RU", несмотря на KZ-подобные реквизиты
    # Но валидация RU не пройдет (нет 9-значного БИК и 20-значного счета)
    # Поэтому будет invalid, но country должен быть RU
    assert result["success"] is True
    assert result["validation"]["country"] == "RU"
    
    # Проверяем payment артефакт
    payment_artifact = artifact_service.get_artifact(result["artifact_id"])
    payment_data = payment_artifact["data"]
    assert payment_data["bank"]["country"] == "RU"
    assert payment_data["currency"] == "RUB"


@pytest.mark.asyncio
async def test_payment_agent_country_hint_kz(payment_agent, services):
    """Тест: country_hint="KZ" используется корректно."""
    artifact_service, event_service = services
    
    # Создаем invoice с country_hint="KZ"
    invoice_data = {
        "country_hint": "KZ",
        "total": "750,00",
        "payment": {
            "beneficiary_bank_name": "Народный Банк Казахстана",
            "beneficiary_bank_bik": "SABRKZKA",
            "beneficiary_account_iban": "KZ67914002203KZ0066V",
            "payment_purpose": "Оплата счета"
        },
        "supplier": {"name": "ТОО Поставщик", "bin_iin": "123456789012"},
        "buyer": {"name": "ТОО Покупатель", "bin_iin": "987654321098"}
    }
    
    invoice_id = artifact_service.create_artifact(
        kind="invoice",
        source="test",
        data=invoice_data,
        tenant_id="tenant-123"
    )
    
    # Запускаем payment_agent
    context = AgentContext(
        artifact_id=invoice_id,
        file_id=None,
        tenant_id="tenant-123"
    )
    
    result = await payment_agent.run(context)
    
    assert result["success"] is True
    assert result["validation"]["country"] == "KZ"
    assert result["validation"]["is_ready"] is True
    
    # Проверяем payment артефакт
    payment_artifact = artifact_service.get_artifact(result["artifact_id"])
    payment_data = payment_artifact["data"]
    assert payment_data["bank"]["country"] == "KZ"
    assert payment_data["currency"] == "KZT"

