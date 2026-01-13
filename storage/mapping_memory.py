"""Упрощённый слой над Database для сопоставлений."""

from typing import Optional
from models.document import ProductMapping
from storage.database import Database

class MappingMemory:
    def __init__(self, db: Database):
        self.db = db

    def get_mapping(self, supplier_item_name: str, supplier_sku: Optional[str]) -> Optional[ProductMapping]:
        return self.db.get_mapping(supplier_item_name, supplier_sku)

    def save_mapping(self, mapping: ProductMapping) -> None:
        self.db.save_mapping(mapping)

    def touch_usage(self, supplier_item_name: str, supplier_sku: Optional[str], my_item_id_1c: str) -> None:
        self.db.touch_mapping_usage(supplier_item_name, supplier_sku, my_item_id_1c)
