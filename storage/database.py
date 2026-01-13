"""SQLite база: инвентарь и сопоставления."""

import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime

from models.document import InventoryItem, ProductMapping

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "inventory.db"):
        self.db_path = db_path
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_1c TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                sku TEXT,
                unit TEXT,
                category TEXT,
                is_active INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_active ON inventory(is_active)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_item_name TEXT NOT NULL,
                supplier_sku TEXT,
                my_item_id_1c TEXT NOT NULL,
                my_item_name TEXT NOT NULL,
                confidence INTEGER DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usage_count INTEGER DEFAULT 1,
                UNIQUE(supplier_item_name, supplier_sku, my_item_id_1c)
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_mapping_supplier_name ON product_mappings(supplier_item_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mapping_supplier_sku ON product_mappings(supplier_sku)")

        # Таблица для статистики обработки документов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                client_id TEXT,
                filename TEXT NOT NULL,
                hash TEXT NOT NULL,
                cached INTEGER DEFAULT 0,
                openai_requests INTEGER DEFAULT 0,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                elapsed_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_client_id ON runs(client_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_hash ON runs(hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at)")

        conn.commit()
        conn.close()
        logger.info("База инициализирована: %s", self.db_path)

    def sync_inventory_from_1c(self, inventory_data: List[Dict]) -> Dict[str, int]:
        conn = self._get_connection()
        cur = conn.cursor()

        added = updated = errors = 0

        for item in inventory_data:
            try:
                id_1c = (item.get("id_1c") or "").strip()
                if not id_1c:
                    errors += 1
                    continue

                cur.execute("SELECT id FROM inventory WHERE id_1c = ?", (id_1c,))
                exists = cur.fetchone() is not None

                cur.execute("""
                    INSERT OR REPLACE INTO inventory (id_1c, name, sku, unit, category, is_active, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    id_1c,
                    (item.get("name") or "").strip(),
                    (item.get("sku") or "").strip() if item.get("sku") else None,
                    (item.get("unit") or "шт").strip(),
                    (item.get("category") or "").strip() if item.get("category") else None,
                    1 if item.get("is_active", True) else 0,
                    datetime.now().isoformat(),
                ))

                if exists:
                    updated += 1
                else:
                    added += 1
            except Exception as e:
                errors += 1
                logger.error("Ошибка синхронизации %s: %s", item.get("id_1c"), e)

        conn.commit()
        conn.close()
        return {"added": added, "updated": updated, "errors": errors, "total_processed": len(inventory_data)}

    def get_all_inventory(self, active_only: bool = True) -> List[InventoryItem]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory" + (" WHERE is_active = 1" if active_only else ""))
        rows = cur.fetchall()
        conn.close()

        out: List[InventoryItem] = []
        for r in rows:
            out.append(InventoryItem(
                id=r["id"], id_1c=r["id_1c"], name=r["name"], sku=r["sku"],
                unit=r["unit"], category=r["category"], is_active=bool(r["is_active"]), updated_at=r["updated_at"]
            ))
        return out

    def find_product_by_sku(self, sku: str) -> Optional[InventoryItem]:
        if not sku:
            return None
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory WHERE sku = ? AND is_active = 1", (sku.strip(),))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return InventoryItem(
            id=r["id"], id_1c=r["id_1c"], name=r["name"], sku=r["sku"],
            unit=r["unit"], category=r["category"], is_active=bool(r["is_active"]), updated_at=r["updated_at"]
        )

    def find_product_by_id_1c(self, id_1c: str) -> Optional[InventoryItem]:
        if not id_1c:
            return None
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory WHERE id_1c = ?", (id_1c.strip(),))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return InventoryItem(
            id=r["id"], id_1c=r["id_1c"], name=r["name"], sku=r["sku"],
            unit=r["unit"], category=r["category"], is_active=bool(r["is_active"]), updated_at=r["updated_at"]
        )

    # ---- mappings ----
    def get_mapping(self, supplier_name: str, supplier_sku: Optional[str]) -> Optional[ProductMapping]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM product_mappings
            WHERE supplier_item_name = ? AND (supplier_sku IS ? OR supplier_sku = ?)
            ORDER BY confidence DESC, usage_count DESC
            LIMIT 1
        """, (supplier_name, supplier_sku, supplier_sku))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return ProductMapping(
            supplier_item_name=r["supplier_item_name"],
            supplier_sku=r["supplier_sku"],
            my_item_id_1c=r["my_item_id_1c"],
            my_item_name=r["my_item_name"],
            confidence=int(r["confidence"]),
            usage_count=int(r["usage_count"]),
        )

    def save_mapping(self, mapping: ProductMapping) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO product_mappings
            (supplier_item_name, supplier_sku, my_item_id_1c, my_item_name, confidence, last_used_at, usage_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            mapping.supplier_item_name,
            mapping.supplier_sku,
            mapping.my_item_id_1c,
            mapping.my_item_name,
            int(mapping.confidence),
            datetime.now().isoformat(),
            int(mapping.usage_count),
        ))
        conn.commit()
        conn.close()

    def touch_mapping_usage(self, supplier_name: str, supplier_sku: Optional[str], my_item_id_1c: str) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE product_mappings
            SET usage_count = usage_count + 1, last_used_at = ?
            WHERE supplier_item_name = ? AND (supplier_sku IS ? OR supplier_sku = ?) AND my_item_id_1c = ?
        """, (datetime.now().isoformat(), supplier_name, supplier_sku, supplier_sku, my_item_id_1c))
        conn.commit()
        conn.close()

    # ---- runs (статистика обработки) ----
    def save_run(self, job_id: str, client_id: Optional[str], filename: str, file_hash: str,
                 cached: bool, openai_requests: int, tokens_in: int, tokens_out: int,
                 cost_usd: float, elapsed_ms: int) -> None:
        """Сохранить статистику обработки документа."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO runs (job_id, client_id, filename, hash, cached, openai_requests,
                           tokens_in, tokens_out, cost_usd, elapsed_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            client_id,
            filename,
            file_hash,
            1 if cached else 0,
            openai_requests,
            tokens_in,
            tokens_out,
            cost_usd,
            elapsed_ms,
            datetime.now().isoformat(),
        ))
        conn.commit()
        conn.close()

    def get_runs_stats(self, client_id: Optional[str] = None) -> Dict:
        """Получить агрегированную статистику по обработке."""
        conn = self._get_connection()
        cur = conn.cursor()
        
        if client_id:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cached_runs,
                    SUM(openai_requests) as total_openai_requests,
                    SUM(tokens_in) as total_tokens_in,
                    SUM(tokens_out) as total_tokens_out,
                    SUM(cost_usd) as total_cost_usd,
                    SUM(elapsed_ms) as total_elapsed_ms
                FROM runs
                WHERE client_id = ?
            """, (client_id,))
        else:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cached_runs,
                    SUM(openai_requests) as total_openai_requests,
                    SUM(tokens_in) as total_tokens_in,
                    SUM(tokens_out) as total_tokens_out,
                    SUM(cost_usd) as total_cost_usd,
                    SUM(elapsed_ms) as total_elapsed_ms
                FROM runs
            """)
        
        row = cur.fetchone()
        conn.close()
        
        if not row or row["total_runs"] is None:
            return {
                "total_runs": 0,
                "cached_runs": 0,
                "total_openai_requests": 0,
                "total_tokens_in": 0,
                "total_tokens_out": 0,
                "total_cost_usd": 0.0,
                "total_elapsed_ms": 0,
                "avg_elapsed_ms": 0,
            }
        
        total_runs = row["total_runs"] or 0
        return {
            "total_runs": total_runs,
            "cached_runs": row["cached_runs"] or 0,
            "total_openai_requests": row["total_openai_requests"] or 0,
            "total_tokens_in": row["total_tokens_in"] or 0,
            "total_tokens_out": row["total_tokens_out"] or 0,
            "total_cost_usd": float(row["total_cost_usd"] or 0.0),
            "total_elapsed_ms": row["total_elapsed_ms"] or 0,
            "avg_elapsed_ms": (row["total_elapsed_ms"] or 0) // total_runs if total_runs > 0 else 0,
        }
