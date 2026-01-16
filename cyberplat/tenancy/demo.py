"""Demo tenant bootstrap utilities (idempotent, opt-in)."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from cyberplat.product.infrastructure.database import get_engine

logger = logging.getLogger(__name__)

DEMO_TENANT_ID = "demo-tenant"
DEMO_TENANT_NAME = "Demo Tenant"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_db_path() -> str:
    # Keep consistent with other SQLite-backed services.
    return os.getenv("PLATFORM_DB_PATH", "platform.db")


def _ensure_bootstrap_event_once(tenant_id: str) -> bool:
    """
    Ensure one-time bootstrap marker.

    Returns True if marker was created, False if it already existed.
    """
    db_path = _platform_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_bootstrap_events (
                tenant_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("SELECT 1 FROM tenant_bootstrap_events WHERE tenant_id = ? LIMIT 1", (tenant_id,))
        exists = cur.fetchone() is not None
        if exists:
            return False
        cur.execute(
            "INSERT INTO tenant_bootstrap_events (tenant_id, created_at) VALUES (?, ?)",
            (tenant_id, _now_iso()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def ensure_demo_tenant(*, tenant_id: str = DEMO_TENANT_ID, name: str = DEMO_TENANT_NAME) -> dict:
    """
    Ensure demo tenant exists and is ready for pilot/demo flows.

    Safe and idempotent; intended to be called only when DEMO_BOOTSTRAP_ENABLED=true.
    """
    engine = get_engine()
    now = _now_iso()

    # Ensure minimal tenant tables exist for SQLite demo environments (no Alembic required).
    # In Postgres we expect migrations to manage schema.
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS tenants (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tenants_is_active ON tenants(is_active)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tenants_created_at ON tenants(created_at)"))

            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS tenant_billing_settings (
                        tenant_id TEXT PRIMARY KEY,
                        default_provider TEXT,
                        stripe_enabled BOOLEAN NOT NULL DEFAULT 0,
                        kaspi_enabled BOOLEAN NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tenant_billing_settings_tenant_id ON tenant_billing_settings(tenant_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tenant_billing_settings_default_provider ON tenant_billing_settings(default_provider)"))
            conn.commit()

    # Upsert tenant row (idempotent).
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (id, name, is_active, created_at, updated_at)
                VALUES (:id, :name, 1, :now, :now)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": tenant_id, "name": name, "now": now},
        )

        # Seed minimal billing settings (disabled but valid) – idempotent.
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO tenant_billing_settings (
                        tenant_id, default_provider, stripe_enabled, kaspi_enabled, created_at, updated_at
                    )
                    VALUES (:tenant_id, NULL, 0, 0, :now, :now)
                    ON CONFLICT (tenant_id) DO NOTHING
                    """
                ),
                {"tenant_id": tenant_id, "now": now},
            )
        except Exception:
            # Table may be absent in Postgres if migrations not applied; ignore for demo bootstrap.
            pass

        conn.commit()

    created_marker = _ensure_bootstrap_event_once(tenant_id)
    if created_marker:
        logger.info("Demo tenant bootstrapped: tenant_id=%s", tenant_id)
    else:
        logger.info("Demo tenant bootstrap skipped (already done): tenant_id=%s", tenant_id)

    return {"tenant_id": tenant_id, "created_marker": created_marker}

