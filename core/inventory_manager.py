"""Поиск и сопоставление товаров поставщика с вашей номенклатурой."""

import logging
from typing import Optional, List, Tuple

from models.document import InventoryItem, ProductMapping
from storage.database import Database

logger = logging.getLogger(__name__)

def _simple_similarity(a: str, b: str) -> int:
    """Очень простой скоринг похожести (без внешних библиотек)."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0
    if a == b:
        return 100
    # пересечение слов
    wa, wb = set(a.split()), set(b.split())
    inter = len(wa & wb)
    union = max(1, len(wa | wb))
    return int(100 * inter / union)

class InventoryManager:
    def __init__(self, db: Database):
        self.db = db

    def find_best_match(self, supplier_name: str, supplier_sku: Optional[str] = None) -> Tuple[Optional[InventoryItem], int]:
        # 1) точное по SKU
        if supplier_sku:
            item = self.db.find_product_by_sku(supplier_sku)
            if item:
                return item, 100

        # 2) простая похожесть по имени
        inventory = self.db.get_all_inventory(active_only=True)
        best: Optional[InventoryItem] = None
        best_score = 0
        for inv in inventory:
            score = _simple_similarity(supplier_name, inv.name)
            if inv.sku and supplier_sku:
                # небольшой бонус если SKU частично совпал
                if supplier_sku.strip().lower() in inv.sku.lower():
                    score = min(100, score + 10)
            if score > best_score:
                best_score = score
                best = inv

        if best_score < 40:
            return None, best_score
        return best, best_score

    def get_or_suggest_mapping(self, supplier_name: str, supplier_sku: Optional[str] = None) -> Optional[ProductMapping]:
        # пытаемся найти сохранённое сопоставление
        mapping = self.db.get_mapping(supplier_name, supplier_sku)
        if mapping:
            self.db.touch_mapping_usage(supplier_name, supplier_sku, mapping.my_item_id_1c)
            return mapping

        item, score = self.find_best_match(supplier_name, supplier_sku)
        if not item:
            return None

        return ProductMapping(
            supplier_item_name=supplier_name,
            supplier_sku=supplier_sku,
            my_item_id_1c=item.id_1c,
            my_item_name=item.name,
            confidence=score,
            usage_count=1,
        )
