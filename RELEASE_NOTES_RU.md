# Release Notes: Исправление совместимости PostgreSQL с psycopg v3

## 🚀 Улучшения

### Нормализация Database URL для psycopg v3

Исправлена проблема совместимости, когда SQLAlchemy и Alembic пытались использовать драйвер `psycopg2` при указании `DATABASE_URL` в формате `postgresql://...` или `postgres://...`, в то время как проект использует `psycopg v3` (`psycopg[binary]`).

**Что изменилось:**
- Автоматическая нормализация `DATABASE_URL` для SQLAlchemy/Alembic:
  - `postgresql://...` → `postgresql+psycopg://...`
  - `postgres://...` → `postgresql+psycopg://...`
- Нормализация выполняется один раз и безопасна для production (идемпотентна)
- Оригинальный `DATABASE_URL` в переменных окружения остаётся без изменений
- Можно продолжать использовать стандартные форматы PostgreSQL URL (`postgresql://...` или `postgres://...`)

**Преимущества:**
- Endpoint `/ready` теперь корректно проверяет подключение к PostgreSQL
- Миграции Alembic работают без зависимости `psycopg2`
- Не нужно вручную указывать `postgresql+psycopg://` в `DATABASE_URL`
- **psycopg2 не используется и не требуется** в проекте

## 🧪 Тесты

- Добавлено 12 регрессионных тестов для нормализации URL (`tests/test_database_url_normalization.py`)
- Тесты покрывают все сценарии: None, пустые строки, SQLite, PostgreSQL, сложные URL
- Тесты не требуют реального подключения к PostgreSQL (чистые строковые операции)
- Все существующие тесты остаются зелёными (SQLite workflow не изменён)

## 📚 Документация

- Обновлён `README.md` с разделом "PostgreSQL + psycopg v3"
- Документировано поведение автоматической нормализации URL
- Добавлены примеры для `docker-compose` и проверки endpoint `/ready`
- Уточнено, что `psycopg2` не используется и не нужен

## 🛡️ Совместимость и безопасность

### ✅ Нет Breaking Changes

- **SQLite workflow не изменён**: SQLite URL (`sqlite://...`) не нормализуются и работают точно так же, как раньше
- **Переменные окружения не изменены**: Оригинальный `DATABASE_URL` в файлах `.env` не модифицируется
- **Обратная совместимость**: Существующие PostgreSQL URL продолжают работать без изменений
- **Идемпотентность**: Нормализация безопасна для повторных вызовов

### ✅ Безопасно для Production

- Нормализация заменяет только первое вхождение префикса схемы (предотвращает двойную замену)
- Оригинальный URL сохраняется для `psycopg.connect()` (используются разобранные параметры)
- Нормализованный URL используется только для SQLAlchemy `create_engine()` и конфигурации Alembic
- Нет побочных эффектов на существующих деплоях

### ✅ Заметки по миграции

**Для существующих окружений:**
- Изменения не требуются
- Продолжайте использовать `DATABASE_URL=postgresql://...` или `DATABASE_URL=postgres://...`
- Код автоматически нормализует для SQLAlchemy/Alembic

**Для новых окружений:**
- Используйте стандартный формат PostgreSQL URL (`postgresql://...` или `postgres://...`)
- Код автоматически нормализует для использования `psycopg v3`

## Технические детали

**Изменённые файлы:**
- `utils/db_url.py` (новый): Централизованный хелпер для нормализации URL
- `app/main.py`: Использует `normalize_database_url()` в endpoint `/ready`
- `alembic/env.py`: Использует `normalize_database_url()` для конфигурации Alembic
- `tests/test_database_url_normalization.py` (новый): Регрессионные тесты
- `README.md`: Обновления документации

**Зависимости:**
- Новые зависимости не добавлены
- `psycopg[binary]>=3.1.0` уже в `requirements.txt`
- **psycopg2 не требуется и не используется**

## Проверка

После деплоя проверьте готовность PostgreSQL:

```bash
curl http://localhost:8000/ready
# Ожидается: "database": "ok (postgresql)", "database_migration": "ok (revision: ...)"
```

Проверьте, что SQLAlchemy использует правильный драйвер:

```bash
docker-compose exec app python -c "from sqlalchemy import create_engine; from utils.db_url import normalize_database_url; import os; db_url = normalize_database_url(os.getenv('DATABASE_URL', '')); engine = create_engine(db_url); print(f'Driver: {engine.driver}')"
# Ожидается: Driver: psycopg
```

---

**Полный Changelog**: См. историю коммитов для детальных изменений.
