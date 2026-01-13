"""Принудительная математическая коррекция сумм."""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

from models.document import DocumentData, DocumentItem
from utils.validators import NumberCleaner

logger = logging.getLogger(__name__)

class MathCorrector:
    def __init__(self):
        self.cleaner = NumberCleaner()

    def correct_document(self, document_data: DocumentData) -> DocumentData:
        """Корректирует totals по строкам, но НЕ портит итоги счета.

        - Всегда корректируем total по каждой строке = quantity * price (если возможно).
        - Для счета на оплату НЕ переписываем document_data.total/subtotal/vat, если они заполнены в документе.
        - Если total в документе пустой или 0 — тогда подставляем сумму строк (fallback).
        """
        try:
            if not document_data.items:
                logger.warning("Документ не содержит товаров для коррекции")
                return document_data

            corrected_items = []
            recalculated_total = Decimal("0")

            for item in document_data.items:
                corrected_item = self._correct_item(item)
                corrected_items.append(corrected_item)
                recalculated_total += self.cleaner.clean_to_decimal(corrected_item.total)

            document_data.items = corrected_items

            # сравнение — только логируем
            if document_data.total:
                try:
                    original_total = self.cleaner.clean_to_decimal(document_data.total)
                    difference = abs(original_total - recalculated_total)
                    if difference > Decimal("5.0"):
                        logger.warning(
                            "Несовпадение итога и суммы строк: итого=%s суммаСтрок=%s Δ=%s (итог НЕ меняем).",
                            original_total, recalculated_total, difference
                        )
                except Exception:
                    pass

            doc_type = (document_data.document_type or "").lower()
            is_invoice = ("счет" in doc_type) or ("счёт" in doc_type) or ("invoice" in doc_type)

            # Для счета: подставляем total только если он пустой/нулевой
            if is_invoice:
                if (not document_data.total) or (self.cleaner.clean_to_decimal(document_data.total) == Decimal("0")):
                    fixed_total = recalculated_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    document_data.total = str(fixed_total)
                    logger.info("СЧЁТ: total был пустой, подставили сумму строк=%s", document_data.total)

            return document_data
        except Exception as e:
            logger.error("Ошибка при математической коррекции: %s", e, exc_info=True)
            return document_data

    def _correct_item(self, item: DocumentItem) -> DocumentItem:
        try:
            q = self.cleaner.clean_to_decimal(item.quantity)
            p = self.cleaner.clean_to_decimal(item.price)
            correct_total = (q * p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            original_total = self.cleaner.clean_to_decimal(item.total)
            if original_total != correct_total:
                logger.debug("Коррекция '%s': %s -> %s", item.name, original_total, correct_total)

            return DocumentItem(
                name=item.name,
                sku=item.sku,
                quantity=item.quantity,
                unit=item.unit,
                price=item.price,
                total=str(correct_total),
            )
        except Exception as e:
            logger.warning("Ошибка коррекции товара '%s': %s", item.name, e)
            return item

    def correct_json(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            doc = DocumentData.from_dict(json_data)
            return self.correct_document(doc).to_dict()
        except Exception as e:
            logger.error("Ошибка при коррекции JSON: %s", e)
            return json_data
