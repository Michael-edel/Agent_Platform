"""Тесты для определения country_hint в doc_agent."""

import pytest
from cyberplat.agents.doc_agent import DocAgent
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService
from cyberplat.storage_service import StorageService


@pytest.fixture
def doc_agent():
    """Создать DocAgent для тестов."""
    artifact_service = ArtifactService()
    event_service = EventService()
    storage_service = StorageService()
    return DocAgent(
        artifact_service=artifact_service,
        event_service=event_service,
        storage_service=storage_service
    )


def test_detect_country_hint_by_iban(doc_agent):
    """Тест: определение KZ по IBAN."""
    invoice_data = {
        "payment": {
            "beneficiary_account_iban": "KZ67914002203KZ0066V",
            "beneficiary_bank_bik": "SABRKZKA"
        }
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "KZ"


def test_detect_country_hint_by_swift(doc_agent):
    """Тест: определение KZ по SWIFT/BIC с буквами."""
    invoice_data = {
        "payment": {
            "beneficiary_account_iban": "12345678901234567890",
            "beneficiary_bank_bik": "SABRKZKA"
        }
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "KZ"


def test_detect_country_hint_by_bik_ru(doc_agent):
    """Тест: определение RU по БИК 9 цифр."""
    invoice_data = {
        "payment": {
            "beneficiary_account_iban": "40702810707000078053",
            "beneficiary_bank_bik": "045004799"
        }
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "RU"


def test_detect_country_hint_by_address_kz(doc_agent):
    """Тест: определение KZ по упоминанию Казахстана в адресе."""
    invoice_data = {
        "payment": {
            "beneficiary_account_iban": "12345678901234567890",
            "beneficiary_bank_bik": "12345"
        },
        "supplier": {
            "name": "ТОО Поставщик",
            "address": "Республика Казахстан, г. Алматы"
        }
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "KZ"


def test_detect_country_hint_default_kz(doc_agent):
    """Тест: default KZ при отсутствии признаков."""
    invoice_data = {
        "payment": {
            "beneficiary_account_iban": "12345678901234567890",
            "beneficiary_bank_bik": "12345"
        },
        "supplier": {
            "name": "ООО Поставщик",
            "address": "Москва, ул. Ленина, 1"
        }
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "KZ"


def test_detect_country_hint_with_pages(doc_agent):
    """Тест: определение country_hint для многостраничного документа."""
    invoice_data = {
        "pages": [
            {
                "payment": {
                    "beneficiary_account_iban": "KZ67914002203KZ0066V",
                    "beneficiary_bank_bik": "SABRKZKA"
                }
            }
        ],
        "total_pages": 1
    }
    country_hint = doc_agent._detect_country_hint(invoice_data)
    assert country_hint == "KZ"
