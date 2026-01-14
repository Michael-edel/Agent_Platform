# Отчёт по аудиту DB слоя (ШАГ 1)

## Найденные места создания engine/session

### 1. Product Layer (SQLAlchemy) - **ГЛАВНЫЙ DB МОДУЛЬ**
**Файл:** `cyberplat/product/infrastructure/database.py`
- **Функции:**
  - `get_database_url()` - получение DATABASE_URL с нормализацией
  - `get_engine()` - создание SQLAlchemy engine (singleton)
  - `get_sessionmaker()` - создание sessionmaker (singleton)
  - `get_db_session()` - FastAPI dependency для получения Session
- **Использование:**
  - `app/api/product.py` - использует `get_db_session()` как dependency
  - `cyberplat/product/infrastructure/repositories_sqlalchemy.py` - принимает Session через DI
  - `cyberplat/product/infrastructure/artifact_state_subscriber.py` - использует `get_sessionmaker()`
- **Статус:** ✅ Правильно, использует SQLAlchemy, нормализованный URL, dependency injection

### 2. Legacy SQLite3 Repositories (Product Layer) - **ТРЕБУЕТ УДАЛЕНИЯ**
**Файл:** `cyberplat/product/infrastructure/repositories.py`
- **Проблемы:**
  - Использует sqlite3 напрямую (не SQLAlchemy)
  - Создаёт schema в runtime (`_ensure_schema()`, `CREATE TABLE IF NOT EXISTS`)
  - Не используется в коде (уже заменён на `repositories_sqlalchemy.py`)
- **Статус:** ❌ Legacy, должен быть удалён/заархивирован

### 3. Core App Services (Legacy SQLite3) - **НЕ ТРОГАЕМ**
**Файлы:**
- `cyberplat/artifact_service.py` - sqlite3 для artifacts
- `cyberplat/event_service.py` - sqlite3 для events
- `cyberplat/billing_service.py` - sqlite3 для billing
- `cyberplat/billing_entitlements.py` - sqlite3 для entitlements
- `storage/database.py` - sqlite3 для inventory
- **Статус:** ⚠️ Legacy, но не входит в scope задачи (core app)

### 4. Временный Engine в /ready endpoint
**Файл:** `app/main.py` (строки 405-409)
- **Использование:** Только для проверки миграций Alembic в readiness check
- **Статус:** ✅ OK, временный engine для проверки

## Вывод: Главный DB модуль

**ГЛАВНЫЙ DB МОДУЛЬ:** `cyberplat/product/infrastructure/database.py`

**Обоснование:**
1. Уже используется product layer (endpoints, repositories, subscribers)
2. Правильная архитектура: SQLAlchemy, dependency injection, нормализованный URL
3. Поддерживает и SQLite (dev) и PostgreSQL (prod)
4. Интегрирован с Alembic миграциями

**Действия:**
- ✅ Product layer уже использует главный DB модуль
- ❌ Legacy `repositories.py` (sqlite3) должен быть удалён
- ✅ Репозитории в `repositories_sqlalchemy.py` правильно используют Session через DI

## Структура зависимостей

```
app/api/product.py
  └─> get_db_session() [dependency]
      └─> get_sessionmaker()
          └─> get_engine()
              └─> get_database_url() [нормализация URL]

repositories_sqlalchemy.py
  └─> Session [injected via DI]
      └─> get_db_session()

artifact_state_subscriber.py
  └─> get_sessionmaker()
      └─> get_engine()
```

## Проверка tenant-scoped запросов

✅ Все методы репозиториев принимают `tenant_id` как обязательный параметр
✅ Все запросы фильтруют по `tenant_id` (WHERE tenant_id = :tenant_id)
✅ Уникальные constraints включают tenant_id где необходимо
