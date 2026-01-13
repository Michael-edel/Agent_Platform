"""Агент для создания payment артефактов из invoice артефактов."""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple

from cyberplat.base_agent import BaseAgent, AgentContext
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService, TenantValidationError

logger = logging.getLogger(__name__)


class PaymentAgent(BaseAgent):
    """Агент для создания payment артефактов из invoice артефактов."""
    
    def __init__(
        self,
        artifact_service: ArtifactService,
        event_service: EventService
    ):
        super().__init__(
            name="payment_agent",
            description="Агент для создания payment артефактов из invoice артефактов"
        )
        self.artifact_service = artifact_service
        self.event_service = event_service
    
    def _parse_amount_to_minor(self, amount_str: Optional[str]) -> Optional[int]:
        """
        Парсить сумму из строки и привести к minor units (копейки).
        
        Примеры:
        - "750,00" → 75000
        - "750.00" → 75000
        - "750" → 75000
        - "1 234,50" → 123450
        """
        if not amount_str:
            return None
        
        # Удаляем пробелы
        amount_str = amount_str.strip().replace(" ", "")
        
        # Заменяем запятую на точку для унификации
        amount_str = amount_str.replace(",", ".")
        
        try:
            # Парсим как float
            amount_float = float(amount_str)
            # Конвертируем в minor units (умножаем на 100)
            return int(amount_float * 100)
        except (ValueError, TypeError):
            logger.warning(f"Не удалось распарсить сумму: {amount_str}")
            return None
    
    def _detect_country(
        self,
        account_iban: Optional[str],
        bik: Optional[str]
    ) -> str:
        """
        Определить страну по реквизитам (KZ-first стратегия).
        
        Правила:
        1) Если IBAN начинается с "KZ" → KZ
        2) Если BIK содержит буквы (A-Z) → KZ (SWIFT)
        3) Если BIK = 9 цифр → RU
        4) Иначе → KZ (default, KZ-first)
        
        Returns:
            "KZ" или "RU"
        """
        # 1) Проверяем IBAN на KZ
        if account_iban:
            account_clean = account_iban.strip().upper()
            if account_clean.startswith("KZ"):
                return "KZ"
        
        # 2) Проверяем BIK на наличие букв (SWIFT/BIC для KZ)
        if bik:
            bik_clean = bik.strip().upper()
            # Если содержит буквы - это SWIFT/BIC (KZ)
            if re.search(r'[A-Z]', bik_clean):
                return "KZ"
            
            # 3) Проверяем на чисто цифровой БИК РФ (9 цифр)
            bik_digits = re.sub(r'\D', '', bik_clean)
            if len(bik_digits) == 9 and bik_digits.isdigit():
                return "RU"
        
        # 4) Default: KZ (KZ-first стратегия)
        return "KZ"
    
    def _validate_bik_ru(self, bik: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Валидация БИК РФ: должен быть 9 цифр.
        
        Returns:
            (is_valid, error_message, cleaned_bik)
        """
        if not bik:
            return False, "БИК отсутствует", None
        
        bik_clean = re.sub(r'\D', '', bik)
        if len(bik_clean) != 9:
            return False, f"БИК должен содержать 9 цифр, получено: {len(bik_clean)}", None
        
        if not bik_clean.isdigit():
            return False, "БИК должен содержать только цифры", None
        
        return True, None, bik_clean
    
    def _validate_account_ru(self, account: Optional[str], field_name: str = "счет") -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Валидация счета РФ: должен быть 20 цифр.
        
        Returns:
            (is_valid, error_or_warning, cleaned_account)
        """
        if not account:
            return True, None, None  # Счет может быть опциональным
        
        account_clean = re.sub(r'\D', '', account)
        if len(account_clean) == 20:
            if not account_clean.isdigit():
                return False, f"{field_name} должен содержать только цифры", None
            return True, None, account_clean
        
        # Если не 20 цифр - это ошибка для RU
        if len(account_clean) > 0:
            return False, f"{field_name} должен содержать 20 цифр для РФ, получено: {len(account_clean)}", None
        
        return True, None, None
    
    def _validate_iban_kz(self, iban: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Валидация IBAN KZ: должен начинаться с KZ и иметь длину 20 символов.
        
        Returns:
            (is_valid, error_message, cleaned_iban)
        """
        if not iban:
            return False, "IBAN отсутствует", None
        
        iban_clean = iban.strip().upper()
        
        if not iban_clean.startswith("KZ"):
            return False, f"IBAN должен начинаться с KZ, получено: {iban_clean[:5]}", None
        
        if len(iban_clean) != 20:
            return False, f"IBAN KZ должен иметь длину 20 символов, получено: {len(iban_clean)}", None
        
        return True, None, iban_clean
    
    def _validate_bic_swift(self, bic: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Валидация BIC/SWIFT: должен быть 8 или 11 символов, латиница+цифры.
        
        Returns:
            (is_valid, error_or_warning, cleaned_bic)
        """
        if not bic:
            return True, None, None  # BIC/SWIFT может быть опциональным (warning)
        
        bic_clean = bic.strip().upper()
        
        # Проверяем формат: только латиница и цифры
        if not re.match(r'^[A-Z0-9]+$', bic_clean):
            return False, f"BIC/SWIFT должен содержать только латинские буквы и цифры, получено: {bic_clean}", None
        
        # Проверяем длину: 8 или 11 символов
        if len(bic_clean) not in [8, 11]:
            return False, f"BIC/SWIFT должен иметь длину 8 или 11 символов, получено: {len(bic_clean)}", None
        
        return True, None, bic_clean
    
    def _extract_barcode(self, barcodes: Optional[List[str]]) -> Optional[str]:
        """Извлечь штрихкод (ST00012 или первый доступный)."""
        if not barcodes:
            return None
        
        # Ищем ST00012
        for barcode in barcodes:
            if barcode and barcode.startswith("ST00012"):
                return barcode
        
        # Возвращаем первый непустой
        for barcode in barcodes:
            if barcode and barcode.strip():
                return barcode.strip()
        
        return None
    
    def _validate_payment_data(
        self,
        country: str,
        amount_minor: Optional[int],
        bank_name: Optional[str],
        bik: Optional[str],
        account_iban: Optional[str],
        corr_account: Optional[str],
        purpose: Optional[str]
    ) -> Dict[str, Any]:
        """
        Валидировать данные платежа в зависимости от страны.
        
        Returns:
            {
                "is_ready": bool,
                "errors": List[str],
                "warnings": List[str],
                "country": str
            }
        """
        errors = []
        warnings = []
        
        # Валидация суммы
        if amount_minor is None or amount_minor <= 0:
            errors.append("Сумма платежа отсутствует или некорректна")
        
        # Валидация банка
        if not bank_name:
            warnings.append("Название банка отсутствует")
        
        # Валидация в зависимости от страны
        if country == "RU":
            # Валидация БИК РФ
            bik_valid, bik_error, bik_clean = self._validate_bik_ru(bik)
            if not bik_valid:
                errors.append(f"БИК РФ: {bik_error}")
            elif not bik:
                warnings.append("БИК отсутствует")
            
            # Валидация счета РФ
            account_valid, account_error, account_clean = self._validate_account_ru(account_iban, "Счет получателя")
            if not account_valid:
                errors.append(f"Счет РФ: {account_error}")
            elif not account_iban:
                warnings.append("Счет получателя отсутствует")
            
            # Валидация корреспондентского счета (опционально)
            if corr_account:
                corr_valid, corr_error, corr_clean = self._validate_account_ru(corr_account, "Корреспондентский счет")
                if not corr_valid:
                    warnings.append(f"Корреспондентский счет: {corr_error}")
        
        elif country == "KZ":
            # Валидация IBAN KZ (обязательно)
            iban_valid, iban_error, iban_clean = self._validate_iban_kz(account_iban)
            if not iban_valid:
                errors.append(f"IBAN KZ: {iban_error}")
            
            # Валидация BIC/SWIFT (желательно, но не обязательно)
            bic_valid, bic_error, bic_clean = self._validate_bic_swift(bik)
            if not bic_valid and bic_error:
                # Если BIC невалидный - это ошибка
                errors.append(f"BIC/SWIFT: {bic_error}")
            elif not bik:
                # Если BIC отсутствует - это только warning, не invalid
                warnings.append("BIC/SWIFT отсутствует (рекомендуется для KZ)")
        
        # Назначение платежа
        if not purpose:
            warnings.append("Назначение платежа отсутствует")
        
        is_ready = len(errors) == 0
        
        return {
            "is_ready": is_ready,
            "errors": errors,
            "warnings": warnings,
            "country": country
        }
    
    async def run(self, context: AgentContext) -> Dict[str, Any]:
        """
        Запустить обработку invoice артефакта и создать payment артефакт.
        
        Args:
            context: Контекст выполнения агента (должен содержать artifact_id invoice)
            
        Returns:
            Результат выполнения
        """
        logger.info(f"Запуск payment_agent для artifact_id={context.artifact_id}")
        
        # Получаем invoice артефакт
        invoice_artifact = self.artifact_service.get_artifact(context.artifact_id)
        if not invoice_artifact:
            raise ValueError(f"Артефакт {context.artifact_id} не найден")
        
        if invoice_artifact.get("kind") != "invoice":
            raise ValueError(f"Артефакт {context.artifact_id} не является invoice (kind={invoice_artifact.get('kind')})")
        
        # Получаем tenant_id из invoice артефакта
        tenant_id = invoice_artifact.get("tenant_id")
        if not tenant_id:
            # Пытаемся получить из контекста
            tenant_id = context.tenant_id
            if not tenant_id:
                raise TenantValidationError(
                    f"tenant_id не найден в артефакте {context.artifact_id} и не передан в контексте. "
                    f"Передайте X-Tenant-ID заголовок."
                )
        
        # КРИТИЧНО: нормализуем и валидируем tenant_id перед созданием payment артефакта
        tenant_id = tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise TenantValidationError(
                f"Некорректный tenant_id: '{tenant_id}'. tenant_id не может быть пустым или 'string'"
            )
        
        invoice_data = invoice_artifact.get("data", {})
        
        # Получаем country_hint из invoice (приоритетный источник)
        country_hint = invoice_data.get("country_hint")
        
        # Обработка многостраничных документов (если invoice содержит pages)
        page_data = invoice_data
        if "pages" in invoice_data and isinstance(invoice_data["pages"], list) and len(invoice_data["pages"]) > 0:
            # Берем первую страницу для извлечения данных
            page_data = invoice_data["pages"][0]
            # Если country_hint не найден на верхнем уровне, ищем в первой странице
            if not country_hint:
                country_hint = page_data.get("country_hint")
        
        # Извлекаем данные из invoice
        # Сумма
        total_str = page_data.get("total") or page_data.get("subtotal")
        amount_minor = self._parse_amount_to_minor(total_str)
        
        # Реквизиты платежа
        payment_data = page_data.get("payment", {})
        bank_name = payment_data.get("beneficiary_bank_name")
        bik = payment_data.get("beneficiary_bank_bik")
        account_iban = payment_data.get("beneficiary_account_iban")
        corr_account = payment_data.get("beneficiary_account_iban")  # Может быть отдельное поле для корр. счета
        purpose = payment_data.get("payment_purpose")
        
        # Определяем страну (приоритет: country_hint > detection > default KZ)
        if country_hint and country_hint in ["KZ", "RU"]:
            country = country_hint
            logger.info(f"Использован country_hint из invoice: {country}")
        else:
            # Fallback на detection по реквизитам
            country = self._detect_country(account_iban, bik)
            logger.info(f"Определена страна по реквизитам: {country} (country_hint не найден или невалиден)")
        
        # Если всё ещё неизвестно → KZ (дефолт)
        if country not in ["KZ", "RU"]:
            country = "KZ"
            logger.info(f"Страна установлена как KZ (default)")
        
        # Supplier/Buyer
        supplier = invoice_data.get("supplier", {})
        buyer = invoice_data.get("buyer", {})
        
        # Barcode
        barcodes = invoice_data.get("barcodes", [])
        if isinstance(barcodes, str):
            barcodes = [barcodes]
        barcode = self._extract_barcode(barcodes)
        
        # Валидация
        validation = self._validate_payment_data(
            country=country,
            amount_minor=amount_minor,
            bank_name=bank_name,
            bik=bik,
            account_iban=account_iban,
            corr_account=corr_account,
            purpose=purpose
        )
        
        # Определяем валюту
        if country == "RU":
            currency = "RUB"
        elif country == "KZ":
            currency = "KZT"
        else:
            # Fallback (не должно происходить, так как default = KZ)
            currency = "KZT"
            validation["warnings"].append(f"Неожиданная страна {country}, валюта установлена как KZT")
        
        # Формируем данные payment артефакта в зависимости от страны
        bank_data = {
            "name": bank_name,
            "country": country
        }
        
        # Добавляем реквизиты в зависимости от страны
        if country == "RU":
            bik_valid, _, bik_clean = self._validate_bik_ru(bik)
            account_valid, _, account_clean = self._validate_account_ru(account_iban)
            corr_valid, _, corr_clean = self._validate_account_ru(corr_account) if corr_account else (True, None, None)
            
            if bik_clean:
                bank_data["bik_ru"] = bik_clean
            if account_clean:
                bank_data["account_ru"] = account_clean
            if corr_clean:
                bank_data["corr_account_ru"] = corr_clean
        
        elif country == "KZ":
            iban_valid, _, iban_clean = self._validate_iban_kz(account_iban)
            bic_valid, _, bic_clean = self._validate_bic_swift(bik)
            
            if iban_clean:
                bank_data["iban"] = iban_clean
            if bic_clean:
                bank_data["bic_swift"] = bic_clean
        
        # Сохраняем raw данные из invoice.payment
        bank_raw = payment_data.copy()
        
        # Формируем данные payment артефакта
        payment_artifact_data = {
            "amount_minor": amount_minor,
            "currency": currency,
            "bank": bank_data,
            "bank_raw": bank_raw,  # Сохраняем сырые данные
            "purpose": purpose,
            "payee": {
                "name": supplier.get("name"),
                "bin_iin": supplier.get("bin_iin")
            },
            "payer": {
                "name": buyer.get("name"),
                "bin_iin": buyer.get("bin_iin")
            },
            "barcode": barcode,
            "validation": validation
        }
        
        # КРИТИЧНО: Создаем payment артефакт ПЕРЕД эмиссией событий
        # Это гарантирует, что артефакт существует, когда события ссылаются на него
        try:
            payment_artifact_id = self.artifact_service.create_artifact(
                kind="payment",
                source="payment_agent",
                data=payment_artifact_data,
                tenant_id=tenant_id
            )
            logger.info(f"Payment артефакт создан: {payment_artifact_id} (tenant_id={tenant_id})")
        except Exception as e:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: не удалось создать payment артефакт: {e}", exc_info=True)
            raise RuntimeError(f"Не удалось создать payment артефакт: {e}") from e
        
        # Проверяем, что артефакт действительно создан
        created_artifact = self.artifact_service.get_artifact(payment_artifact_id)
        if not created_artifact:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: payment артефакт {payment_artifact_id} не найден после создания")
            raise RuntimeError(f"Payment артефакт {payment_artifact_id} не найден после создания")
        
        if created_artifact.get("kind") != "payment":
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: созданный артефакт имеет kind={created_artifact.get('kind')}, ожидается 'payment'")
            raise RuntimeError(f"Созданный артефакт имеет неправильный kind: {created_artifact.get('kind')}")
        
        # Эмитим события
        # Всегда эмитим payment.prepared
        self.event_service.emit(
            event_type="payment.prepared",
            tenant_id=tenant_id,
            artifact_id=payment_artifact_id,
            payload={"invoice_artifact_id": context.artifact_id}
        )
        
        # Эмитим payment.ready или payment.invalid в зависимости от валидации
        if validation["is_ready"]:
            self.event_service.emit(
                event_type="payment.ready",
                tenant_id=tenant_id,
                artifact_id=payment_artifact_id,
                payload={"invoice_artifact_id": context.artifact_id}
            )
        else:
            self.event_service.emit(
                event_type="payment.invalid",
                tenant_id=tenant_id,
                artifact_id=payment_artifact_id,
                payload={
                    "invoice_artifact_id": context.artifact_id,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"]
                }
            )
        
        logger.info(f"Payment артефакт создан и события эмитированы: {payment_artifact_id} (is_ready={validation['is_ready']})")
        
        return {
            "success": True,
            "artifact_id": payment_artifact_id,
            "tenant_id": tenant_id,
            "validation": validation
        }
