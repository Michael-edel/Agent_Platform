"""
Модуль валидации данных документов
Версия 2.0 (рефакторинг)
"""

import re
import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

IIN_BIN_LENGTH = 12
WEIGHTS_1 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
WEIGHTS_2 = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
MAX_TOLERANCE = Decimal('0.01')  # 1 копейка
VAT_TOLERANCE = Decimal('1.00')  # 1 тенге для НДС

@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    def to_dict(self) -> Dict[str, Any]:
        return {"is_valid": self.is_valid, "errors": self.errors, "warnings": self.warnings}

class NumberCleaner:
    @staticmethod
    def clean_to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
        if value is None:
            return default

        if isinstance(value, Decimal):
            return value

        if isinstance(value, (int, float)):
            try:
                return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            except (ValueError, InvalidOperation):
                return default

        try:
            cleaned = str(value).strip()
            cleaned = re.sub(r'[\s\xa0\u2009]', '', cleaned)

            if not cleaned or cleaned == '-':
                return default

            dot_count = cleaned.count('.')
            comma_count = cleaned.count(',')

            if dot_count > 0 and comma_count > 0:
                dot_pos = cleaned.find('.')
                comma_pos = cleaned.find(',')
                if dot_pos < comma_pos:
                    cleaned = cleaned.replace('.', '')
                    cleaned = cleaned.replace(',', '.')
                else:
                    cleaned = cleaned.replace(',', '')
            elif comma_count > 0:
                cleaned = cleaned.replace(',', '.')

            parts = cleaned.split('.')
            if len(parts) > 2:
                cleaned = parts[0] + '.' + ''.join(parts[1:])

            cleaned = re.sub(r'[^\d.\-]', '', cleaned)

            if not cleaned or cleaned in {'-', '.', '-.'}:
                return default

            result = Decimal(cleaned)
            return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        except (ValueError, InvalidOperation, TypeError) as e:
            logger.warning(f"Ошибка преобразования числа '{value}': {e}")
            return default

    @staticmethod
    def format_decimal(value: Decimal) -> str:
        try:
            return f"{value:.2f}".replace('.', ',')
        except (ValueError, InvalidOperation):
            return "0,00"

class IINBINValidator:
    @staticmethod
    def validate(identifier: str) -> Tuple[bool, Optional[str]]:
        if identifier is None:
            return False, "ИИН/БИН не указан"

        clean_id = re.sub(r'\D', '', str(identifier))

        if len(clean_id) != IIN_BIN_LENGTH:
            return False, f"ИИН/БИН должен содержать {IIN_BIN_LENGTH} цифр, получено {len(clean_id)}"

        if not clean_id.isdigit():
            return False, "ИИН/БИН должен содержать только цифры"

        try:
            digits = [int(d) for d in clean_id]
            checksum = sum(digits[i] * WEIGHTS_1[i] for i in range(11)) % 11
            if checksum == 10:
                checksum = sum(digits[i] * WEIGHTS_2[i] for i in range(11)) % 11
                if checksum == 10:
                    checksum = 0

            expected_checksum = checksum
            actual_checksum = digits[11]

            if expected_checksum != actual_checksum:
                return False, (
                    f"Неверная контрольная сумма ИИН/БИН (ожидалось {expected_checksum}, получено {actual_checksum})"
                )

            return True, None

        except Exception as e:
            logger.error(f"Ошибка при проверке контрольной суммы: {e}")
            return False, f"Ошибка при проверке ИИН/БИН: {str(e)}"

class DocumentValidator:
    def __init__(self):
        self.cleaner = NumberCleaner()
        self.iin_validator = IINBINValidator()

    def validate_arithmetic(self, items: List[Dict]) -> ValidationResult:
        result = ValidationResult(is_valid=True, errors=[], warnings=[])
        if not items:
            result.add_warning("Документ не содержит товаров")
            return result

        for i, item in enumerate(items, 1):
            try:
                name = str(item.get("name", "Без названия")).strip()
                quantity_raw = item.get("quantity")
                price_raw = item.get("price")
                total_raw = item.get("total")

                if None in (quantity_raw, price_raw, total_raw):
                    result.add_error(
                        f"Товар {i} ('{name}'): отсутствуют обязательные поля (количество: {quantity_raw}, цена: {price_raw}, сумма: {total_raw})"
                    )
                    continue

                quantity = self.cleaner.clean_to_decimal(quantity_raw)
                price = self.cleaner.clean_to_decimal(price_raw)
                total = self.cleaner.clean_to_decimal(total_raw)

                if quantity == Decimal('0'):
                    result.add_warning(f"Товар {i} ('{name}'): количество равно 0")
                if price == Decimal('0'):
                    result.add_warning(f"Товар {i} ('{name}'): цена равна 0")

                expected_total = (quantity * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                difference = abs(total - expected_total)

                if difference > MAX_TOLERANCE:
                    result.add_error(
                        f"Товар {i} ('{name}'): неверная сумма ({quantity} × {price} = {expected_total}, указано {total}, разница: {difference:.2f})"
                    )
                elif difference > Decimal('0'):
                    result.add_warning(f"Товар {i} ('{name}'): небольшая разница в сумме ({difference:.2f} тенге)")

            except Exception as e:
                logger.error(f"Ошибка при валидации товара {i}: {e}")
                result.add_error(f"Товар {i}: ошибка при проверке числовых значений")

        return result

    def validate_document_totals(self, items: List[Dict], subtotal: Any, vat: Optional[Any], total: Any) -> ValidationResult:
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        try:
            if not isinstance(items, list):
                result.add_error("Поле 'items' должно быть списком")
                return result

            calculated_subtotal = Decimal('0')
            for item in items:
                calculated_subtotal += self.cleaner.clean_to_decimal(item.get("total"))
            calculated_subtotal = calculated_subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            declared_subtotal = self.cleaner.clean_to_decimal(subtotal)
            declared_total = self.cleaner.clean_to_decimal(total)
            declared_vat = self.cleaner.clean_to_decimal(vat) if vat is not None else Decimal('0')

            subtotal_diff = abs(calculated_subtotal - declared_subtotal)
            if subtotal_diff > MAX_TOLERANCE:
                result.add_error(
                    f"Неверная сумма товаров: рассчитано {calculated_subtotal}, указано {declared_subtotal} (разница: {subtotal_diff:.2f})"
                )

            if declared_vat > 0:
                total_with_vat_extra = declared_subtotal + declared_vat
                diff_extra = abs(total_with_vat_extra - declared_total)
                diff_included = abs(declared_subtotal - declared_total)

                if diff_included <= diff_extra and diff_included <= VAT_TOLERANCE:
                    result.add_warning("НДС, вероятно, уже включен в сумму товаров")
                elif diff_extra <= VAT_TOLERANCE:
                    pass
                else:
                    if diff_included < diff_extra:
                        result.add_error(
                            f"Неверная итоговая сумма: сумма товаров ({declared_subtotal}) не совпадает с итогом ({declared_total}), разница: {diff_included:.2f}"
                        )
                    else:
                        result.add_error(
                            f"Неверная итоговая сумма: {declared_subtotal} + {declared_vat} = {total_with_vat_extra}, указано {declared_total}, разница: {diff_extra:.2f}"
                        )
            else:
                total_diff = abs(declared_subtotal - declared_total)
                if total_diff > MAX_TOLERANCE:
                    result.add_error(
                        f"Неверная итоговая сумма: должна быть {declared_subtotal}, указано {declared_total} (разница: {total_diff:.2f})"
                    )

        except Exception as e:
            logger.error(f"Ошибка при валидации итогов: {e}")
            result.add_error(f"Ошибка при проверке итоговых сумм: {str(e)}")

        return result

    def validate_document_quality(self, extracted_data: Dict) -> ValidationResult:
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if not extracted_data or not isinstance(extracted_data, dict):
            result.add_error("Нет данных для проверки")
            return result

        doc_type = str(extracted_data.get("document_type", "")).lower()
        required_fields = self._get_required_fields(doc_type)

        for field_key, field_name in required_fields.items():
            value = extracted_data.get(field_key)
            if value is None or (isinstance(value, str) and value.strip() == ""):
                result.add_error(f"Отсутствует обязательное поле: {field_name}")

        error_field = extracted_data.get("error")
        if error_field:
            result.add_warning(f"Распознавание с ошибками: {error_field}")

        iin_bin = extracted_data.get("iin_bin") or extracted_data.get("iin") or extracted_data.get("bin")
        if iin_bin:
            is_valid, error_msg = self.iin_validator.validate(iin_bin)
            if not is_valid:
                result.add_error(f"ИИН/БИН: {error_msg}")

        return result

    def _get_required_fields(self, doc_type: str) -> Dict[str, str]:
        doc_type = doc_type.lower()

        if "накладная" in doc_type or "invoice" in doc_type:
            return {"iin_bin": "ИИН/БИН", "document_number": "Номер документа", "document_date": "Дата документа", "items": "Список товаров"}
        if "счет" in doc_type:
            return {"iin_bin": "ИИН/БИН", "document_number": "Номер документа", "total": "Итоговая сумма"}
        if "паспорт" in doc_type or "удостоверение" in doc_type:
            return {"iin": "ИИН", "full_name": "ФИО"}
        return {"iin_bin": "ИИН/БИН", "document_number": "Номер документа"}

def clean_float(value) -> float:
    try:
        return float(NumberCleaner.clean_to_decimal(value))
    except (ValueError, TypeError):
        return 0.0

def validate_iin_bin(identifier: str):
    return IINBINValidator.validate(identifier)

def validate_arithmetic(items):
    v = DocumentValidator()
    r = v.validate_arithmetic(items)
    return r.is_valid, r.errors

def validate_document_totals(items, subtotal, vat, total):
    v = DocumentValidator()
    r = v.validate_document_totals(items, subtotal, vat, total)
    return r.is_valid, r.errors

def check_document_quality(extracted_data):
    v = DocumentValidator()
    r = v.validate_document_quality(extracted_data)
    return len(r.errors) == 0, r.errors
