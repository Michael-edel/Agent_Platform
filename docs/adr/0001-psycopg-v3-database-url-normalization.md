# ADR-0001: psycopg v3 and DATABASE_URL normalization

**Status**: Accepted  
**Date**: 2024-12-19  
**Deciders**: Architecture Team

---

## Context

Проект Agent Platform / CyberPlat использует PostgreSQL для staging и production окружений, SQLite для локальной разработки. Для работы с PostgreSQL применяются:

- **SQLAlchemy** (>=2.0.0) — ORM и database toolkit
- **Alembic** (>=1.13.0) — управление миграциями схемы БД
- **psycopg v3** (`psycopg[binary]>=3.1.0`) — выбранный PostgreSQL драйвер

### Проблема

SQLAlchemy по умолчанию использует драйвер `psycopg2` для URL вида `postgresql://...` или `postgres://...`. В проекте:

- `psycopg2` намеренно **не используется** и **не установлен**
- Установлен только `psycopg v3` (`psycopg[binary]`)
- Стандартные `DATABASE_URL` в окружениях имеют формат `postgresql://...` или `postgres://...`

Это приводило к runtime ошибкам:
- `ImportError: No module named 'psycopg2'` в endpoint `/ready` при проверке подключения к PostgreSQL
- Ошибки при выполнении Alembic миграций (`alembic upgrade head`, `alembic current`)
- Невозможность корректной работы с PostgreSQL без ручного указания драйвера в URL

### Требования

- Сохранить совместимость с стандартными форматами `DATABASE_URL` (`postgresql://...`, `postgres://...`)
- Не требовать изменений в переменных окружения (`.env`, docker-compose, CI/CD)
- Обеспечить production-safe поведение (идемпотентность, безопасность)
- Не затрагивать SQLite workflow (локальная разработка)

---

## Decision

Принято решение о **централизованной нормализации `DATABASE_URL`** для SQLAlchemy и Alembic.

### Реализация

1. **Создан единый хелпер** `utils/db_url.py`:
   - Функция `normalize_database_url(url: Optional[str]) -> Optional[str]`
   - Единый источник истины для нормализации URL

2. **Правила нормализации**:
   - `postgresql://...` → `postgresql+psycopg://...` (замена только первого вхождения)
   - `postgres://...` → `postgresql+psycopg://...` (замена только первого вхождения)
   - `postgresql+psycopg://...` → без изменений (уже нормализован)
   - `sqlite://...` → без изменений (SQLite не требует нормализации)
   - `None` или пустая строка → без изменений
   - Пробелы в начале/конце обрезаются (`.strip()`)

3. **Разделение ответственности**:
   - **SQLAlchemy `create_engine()`** и **Alembic `config.set_main_option()`** → используют нормализованный URL (`postgresql+psycopg://...`)
   - **`psycopg.connect()`** → использует разобранные параметры из оригинального URL (не понимает схему `+psycopg`)

4. **Места применения**:
   - `app/main.py` — endpoint `/ready` (readiness check для PostgreSQL)
   - `alembic/env.py` — конфигурация Alembic для миграций

5. **Идемпотентность**:
   - Нормализация выполняется один раз (замена только первого вхождения префикса)
   - Повторные вызовы безопасны (не приводят к двойной замене)
   - Оригинальный `DATABASE_URL` в переменных окружения не изменяется

---

## Alternatives Considered

### 1. Использовать psycopg2 вместо psycopg v3

**Отклонено:**
- `psycopg2` — устаревший драйвер (legacy)
- `psycopg v3` — современный, активно развивающийся драйвер с лучшей производительностью
- Не соответствует стратегии проекта (использование современных технологий)

### 2. Требовать от всех окружений указывать `postgresql+psycopg://` вручную

**Отклонено:**
- Нарушает принцип "convention over configuration"
- Требует изменений во всех `.env` файлах, docker-compose, CI/CD конфигурациях
- Высокий риск ошибок при настройке новых окружений
- Несовместимо со стандартными практиками (большинство инструментов ожидают `postgresql://...`)

### 3. Использовать SQLAlchemy event hooks или custom dialect

**Отклонено:**
- Избыточная сложность для простой задачи
- Требует глубокой интеграции с SQLAlchemy internals
- Сложнее поддерживать и тестировать
- Не решает проблему для Alembic (который использует SQLAlchemy напрямую)

### 4. Хардкодить драйвер в `create_engine()` без нормализации URL

**Отклонено:**
- Не решает проблему для Alembic (который читает URL из конфигурации)
- Требует изменений во всех местах создания engine
- Нарушает DRY принцип (дублирование логики)
- Сложнее поддерживать и тестировать

### 5. Использовать переменную окружения `SQLALCHEMY_DATABASE_URI` с нормализованным URL

**Отклонено:**
- Требует изменений в переменных окружения
- Дублирование информации (`DATABASE_URL` и `SQLALCHEMY_DATABASE_URI`)
- Не решает проблему для Alembic (который читает из `alembic.ini` или env)

---

## Consequences

### Положительные

- ✅ **Production-safe**: идемпотентная нормализация, безопасна для повторных вызовов
- ✅ **Совместимость**: работает с docker-compose, CI/CD, cloud environments без изменений
- ✅ **Единый источник истины**: вся логика нормализации в одном месте (`utils/db_url.py`)
- ✅ **Нет зависимости от psycopg2**: проект использует только psycopg v3
- ✅ **Простая диагностика**: легко проверить нормализацию через unit-тесты
- ✅ **Безопасный rollback**: изменение только в runtime коде, не требует DB downgrade
- ✅ **Прозрачность**: явная нормализация, легко понять, что происходит

### Нейтральные / Отрицательные

- ⚠️ **Дополнительный слой логики**: функция `normalize_database_url()` требует поддержки
- ⚠️ **Тесты**: необходимо поддерживать регрессионные тесты для нормализации (12 тестов)
- ⚠️ **Разделение ответственности**: нужно помнить, что `psycopg.connect()` использует оригинальный URL, а `create_engine()` — нормализованный
- ⚠️ **Потенциальная путаница**: разработчики могут не понимать, почему URL нормализуется автоматически (решается документацией)

### Митигация рисков

- **Документация**: раздел "PostgreSQL + psycopg v3" в `README.md`
- **Тесты**: 12 регрессионных тестов покрывают все сценарии нормализации
- **Комментарии**: код содержит явные комментарии о разделении ответственности
- **ADR**: этот документ фиксирует архитектурное решение

---

## Validation

Решение проверяется следующими способами:

### 1. Unit-тесты нормализации URL

**Файл**: `tests/test_database_url_normalization.py`

**Покрытие** (12 тестов):
- `None` → `None`
- Пустая строка → `""`
- SQLite URL → без изменений
- `postgresql://...` → `postgresql+psycopg://...`
- `postgres://...` → `postgresql+psycopg://...`
- Уже нормализованный URL → без изменений
- Замена только первого вхождения (edge case)
- Другие схемы → без изменений
- Обработка пробелов (`.strip()`)
- Сложные URL с параметрами

**Команда проверки**:
```bash
python -m pytest tests/test_database_url_normalization.py -v
```

### 2. Runtime проверки

**Readiness endpoint** (`/ready`):
```bash
curl http://localhost:8000/ready
# Ожидается: "database": "ok (postgresql)", "database_migration": "ok (revision: ...)"
```

**Alembic migration status**:
```bash
make db-current
# Ожидается: d01c4c0f81ed (или актуальная head revision)
```

**SQLAlchemy driver verification**:
```bash
docker-compose exec app python -c "
from sqlalchemy import create_engine
from utils.db_url import normalize_database_url
import os
db_url = normalize_database_url(os.getenv('DATABASE_URL', ''))
engine = create_engine(db_url)
print(f'Driver: {engine.driver}')
"
# Ожидается: Driver: psycopg
```

### 3. Integration проверки

- Все существующие тесты остаются зелёными (SQLite workflow не изменён)
- CI pipeline проходит успешно (GitHub Actions)
- Docker build собирается без ошибок
- Нет упоминаний `psycopg2` в логах runtime

---

## Links / References

### Код

- **Хелпер нормализации**: [`utils/db_url.py`](../../utils/db_url.py)
- **Применение в app**: [`app/main.py`](../../app/main.py) (строки 24, 352)
- **Применение в Alembic**: [`alembic/env.py`](../../alembic/env.py) (строки 25, 27)

### Тесты

- **Регрессионные тесты**: [`tests/test_database_url_normalization.py`](../../tests/test_database_url_normalization.py) (12 тестов)

### Документация

- **README**: [`README.md`](../../README.md) — раздел "PostgreSQL + psycopg v3"
- **Deployment checklist**: [`POST_MERGE_DEPLOYMENT_CHECKLIST.md`](../../POST_MERGE_DEPLOYMENT_CHECKLIST.md)
- **Release notes**: [`RELEASE_NOTES.md`](../../RELEASE_NOTES.md)

### Зависимости

- `psycopg[binary]>=3.1.0` в [`requirements.txt`](../../requirements.txt)
- `sqlalchemy>=2.0.0` в [`requirements.txt`](../../requirements.txt)
- `alembic>=1.13.0` в [`requirements.txt`](../../requirements.txt)

---

## Notes

- Это первое архитектурное решение, зафиксированное в формате ADR в проекте
- Решение принято после анализа проблемы совместимости psycopg v3 с SQLAlchemy
- Нормализация URL является production-safe и не требует изменений в переменных окружения
- SQLite workflow полностью не затронут (нормализация применяется только к PostgreSQL URL)

---

**Related ADRs**: None (первый ADR в проекте)
