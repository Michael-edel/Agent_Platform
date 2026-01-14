"""Общие pytest фикстуры для тестового контура.

Цели:
- Все тесты работают с изолированной БД (без platform.db в корне репозитория).
- SQLAlchemy product-layer таблицы создаются один раз на сессию (Base.metadata.create_all),
  чтобы тесты не падали с "no such table: tenant_plans / plans / ...".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _test_db_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    """
    Настроить изолированную SQLite БД для всего тест-рана.

    В production используется PostgreSQL + Alembic,
    но для unit-тестов нам достаточно SQLite и create_all().
    """
    db_dir = tmp_path_factory.mktemp("agent_platform_test_db")
    db_path = db_dir / "test.db"

    os.environ["PLATFORM_DB_PATH"] = str(db_path)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    # Сбросить глобальный engine/sessionmaker product слоя и создать schema
    from cyberplat.product.infrastructure import database as product_db

    product_db._engine = None
    product_db._SessionLocal = None

    engine = product_db.get_engine()

    from cyberplat.product.infrastructure.models import Base, Plan

    Base.metadata.create_all(bind=engine)

    # Seed базовых планов (trial/pro/enterprise) для тестов, которые полагаются на plans.
    SessionLocal = product_db.get_sessionmaker()
    session = SessionLocal()
    try:
        import json
        from datetime import datetime

        now = datetime.now().isoformat()
        defaults = [
            ("trial", "Trial", None, None, {"document_upload": 20, "invoice_extracted": 10, "page_processed": 50}),
            ("pro", "Pro", 2900, "USD", {"document_upload": 100, "invoice_extracted": 50, "page_processed": 500}),
            ("enterprise", "Enterprise", 9900, "USD", {"document_upload": None, "invoice_extracted": None, "page_processed": None}),
        ]
        for pid, name, price, cur, quotas in defaults:
            if not session.query(Plan).filter(Plan.id == pid).first():
                session.add(
                    Plan(
                        id=pid,
                        name=name,
                        description=f"{name} plan",
                        quotas=json.dumps(quotas, ensure_ascii=False),
                        price_minor=price,
                        currency=cur,
                        active=True,
                        created_at=now,
                    )
                )
        session.commit()
    finally:
        session.close()

