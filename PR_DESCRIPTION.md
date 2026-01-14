# Fix: psycopg v3 for PostgreSQL readiness & Alembic

## Summary

- ✅ **DRY**: Вынесена нормализация `DATABASE_URL` в единый хелпер `utils/db_url.py`
- ✅ **Тесты**: Добавлено 12 регрессионных тестов для нормализации URL
- ✅ **Документация**: Обновлён README.md с разделом "PostgreSQL + psycopg v3"
- ✅ **Рефакторинг**: Удалена копипаста нормализации из `app/main.py` и `alembic/env.py`

## Root Cause

SQLAlchemy по умолчанию использует драйвер `psycopg2` при `DATABASE_URL` вида `postgresql://...`, но в проекте установлен `psycopg v3` (`psycopg[binary]`). Это приводило к ошибке `No module named 'psycopg2'` в `/ready` endpoint и при выполнении Alembic миграций.

**Решение**: автоматическая нормализация `DATABASE_URL` для SQLAlchemy/Alembic:
- `postgresql://...` → `postgresql+psycopg://...`
- `postgres://...` → `postgresql+psycopg://...`
- Замена выполняется один раз (production-safe)
- SQLite и другие схемы не изменяются

## What Changed

### Новые файлы:
- **`utils/db_url.py`** (58 строк)
  - Функция `normalize_database_url(url: Optional[str]) -> Optional[str]`
  - Единый источник истины для нормализации URL
  - Полная документация с примерами

- **`tests/test_database_url_normalization.py`** (94 строки)
  - 12 регрессионных тестов
  - Покрытие всех сценариев: None, пустая строка, SQLite, PostgreSQL, замена один раз, сложные URL
  - Тесты не требуют реального PostgreSQL

### Изменённые файлы:
- **`app/main.py`**
  - Импорт `normalize_database_url` в начале файла (строка 24)
  - Удалена локальная копипаста нормализации
  - `/ready` endpoint использует `normalize_database_url()`
  - Для `create_engine()` используется нормализованный URL
  - Для `psycopg.connect()` используется оригинальный URL (без `+psycopg`)
  - Улучшена проверка PostgreSQL через `is_postgres` с `startswith()`

- **`alembic/env.py`**
  - Импорт `normalize_database_url` из `utils.db_url`
  - Удалена локальная копипаста нормализации
  - `config.set_main_option("sqlalchemy.url", ...)` получает нормализованный URL
  - Fallback из `POSTGRES_*` формирует URL как `postgresql+psycopg://...`

- **`README.md`**
  - Добавлен раздел "PostgreSQL + psycopg v3" в секцию "Database Migrations"
  - Объяснение автоматической нормализации
  - Примеры настройки с `docker-compose` и `curl /ready`
  - Указание, что `psycopg2` не используется и не нужен

### Без изменений:
- **`Makefile`**: команда `db-check` оставлена как есть (inline Python скрипт работает корректно)

## How to Test

### Локально (без реального PostgreSQL):

```bash
# 1. Тесты нормализации URL
python -m pytest tests/test_database_url_normalization.py -v
# Ожидается: 12 passed

# 2. Все существующие тесты (SQLite)
python -m pytest -q
# Ожидается: все тесты зелёные (SQLite workflow не изменён)
```

### С PostgreSQL (docker-compose):

```bash
# 1. Установите DATABASE_URL (можно postgresql:// или postgres://)
export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform

# 2. Запустите docker-compose
docker-compose up -d --build

# 3. Проверьте готовность
curl http://127.0.0.1:8000/ready
```

**Ожидаемый ответ:**
```json
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql)",
    "database_migration": "ok (revision: d01c4c0f81ed)",
    "billing_service": "ok",
    "entitlement_service": "ok"
  }
}
```

**Проверка драйвера:**
```bash
docker-compose exec app python -c "from sqlalchemy import create_engine; from utils.db_url import normalize_database_url; import os; db_url = normalize_database_url(os.getenv('DATABASE_URL', '')); engine = create_engine(db_url); print(f'Driver: {engine.driver}')"
# Должно показать: Driver: psycopg
```

**Проверка миграций:**
```bash
# Внутри контейнера или локально с DATABASE_URL
make db-current
# Должно показать: d01c4c0f81ed (initial_schema)

make db-check
# Должно показать: ✓ Schema is up-to-date (revision: d01c4c0f81ed)
```

## Risk / Impact

### ✅ Низкий риск:
- **Обратная совместимость**: SQLite workflow не изменён
- **Идемпотентность**: нормализация выполняется один раз, повторные вызовы безопасны
- **Безопасность**: не изменяет переменные окружения, не требует psycopg2
- **Тестируемость**: 12 регрессионных тестов покрывают все сценарии

### ⚠️ Потенциальные проблемы:
- **Если URL уже содержит `postgresql+psycopg://`**: нормализация не выполняется (корректно)
- **Если используется внешний PostgreSQL с кастомным драйвером**: может потребоваться явное указание драйвера в URL

### 🔍 Что проверить:
- [x] Все тесты проходят (`pytest -q`)
- [x] `/ready` возвращает корректный ответ для PostgreSQL
- [x] Alembic миграции работают (`make db-upgrade`)
- [x] SQLite dev workflow не сломан
- [x] Нет warning про `psycopg2` в логах

## Checklist

- [x] **Тесты**: 12 новых тестов добавлены, все существующие тесты зелёные
- [x] **Lint**: `ruff check .` проходит (если применимо)
- [x] **Документация**: README.md обновлён с разделом "PostgreSQL + psycopg v3"
- [x] **DRY**: нормализация вынесена в единый хелпер `utils/db_url.py`
- [x] **Рефакторинг**: копипаста удалена из `app/main.py` и `alembic/env.py`
- [x] **Production-safe**: идемпотентность, безопасность, обратная совместимость
- [x] **Разделение ответственности**: `psycopg.connect()` использует оригинальный URL, `create_engine()` — нормализованный

## Screenshots / Logs

### Пример успешного `/ready` ответа:

```bash
$ curl http://127.0.0.1:8000/ready
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql)",
    "database_migration": "ok (revision: d01c4c0f81ed)",
    "billing_service": "ok",
    "entitlement_service": "ok"
  }
}
```

### Пример работы нормализации:

```python
>>> from utils.db_url import normalize_database_url
>>> normalize_database_url("postgresql://user:pass@host:5432/db")
'postgresql+psycopg://user:pass@host:5432/db'
>>> normalize_database_url("sqlite:///./test.db")
'sqlite:///./test.db'
>>> normalize_database_url(None)
None
```

## Related Issues / Context

- Решает проблему: `/ready` endpoint возвращал warning `No module named 'psycopg2'`
- Решает проблему: Alembic миграции не работали с `DATABASE_URL=postgresql://...`
- Улучшает: DRY принцип (единый источник истины для нормализации)
- Улучшает: тестируемость (12 регрессионных тестов)

## Migration Notes

**Для существующих окружений:**
- Никаких изменений не требуется
- `DATABASE_URL` можно оставлять как `postgresql://...` или `postgres://...`
- Код автоматически нормализует для SQLAlchemy/Alembic

**Для новых окружений:**
- Можно указывать `DATABASE_URL` в стандартном формате (`postgresql://...` или `postgres://...`)
- Код автоматически нормализует для использования `psycopg v3`
