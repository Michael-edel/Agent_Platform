"""Линт-тест для Alembic миграций: запрет 0/1 для boolean в SQL INSERT.

Цель: предотвратить регресс, когда миграция работает в SQLite,
но падает в PostgreSQL (psycopg v3) из-за вставки 0/1 в boolean колонку.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_alembic_migrations_do_not_insert_boolean_as_int() -> None:
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    assert versions_dir.exists(), "Каталог alembic/versions не найден"

    # Ищем типичный анти-паттерн:
    # INSERT INTO ... active ... VALUES ( ... \n 1, ... )
    # Важно: ловим только случаи, когда отдельной строкой вставляется 0/1
    # (чтобы минимизировать ложные срабатывания на других числах).
    bad_insert_re = re.compile(
        r"INSERT\s+INTO[\s\S]{0,800}\bactive\b[\s\S]{0,800}VALUES\s*\([\s\S]{0,1200}?"
        r"(?:\r?\n)\s*[01]\s*(?:,|\))",
        re.IGNORECASE,
    )

    offenders: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        m = bad_insert_re.search(text)
        if m:
            snippet = m.group(0)
            snippet = snippet[:500].replace("\r\n", "\n")
            offenders.append(f"- {path.name}\n  Фрагмент:\n{snippet}\n")

    if offenders:
        raise AssertionError(
            "Найдены Alembic-миграции с SQL INSERT, где boolean колонка 'active' "
            "вставляется как 0/1. Для PostgreSQL это ошибка.\n"
            "Исправление: используйте TRUE/FALSE в SQL или op.bulk_insert() с True/False.\n\n"
            "Проблемные файлы:\n" + "\n".join(offenders)
        )

