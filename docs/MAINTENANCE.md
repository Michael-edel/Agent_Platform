# Long-term Maintenance Notes

Руководство для долгосрочной поддержки проекта Agent Platform / CyberPlat.

---

## Supported Stack

### Python Version

- **Current**: Python 3.13
- **Also supported**: Python 3.12 (CI matrix)
- **Minimum**: Python 3.12

### Database Drivers

- **PostgreSQL**: `psycopg[binary]>=3.1.0` (psycopg v3)
- **psycopg2**: **intentionally not supported**
- **SQLite**: встроенный `sqlite3` (стандартная библиотека Python)

### ORM / Migrations

- **SQLAlchemy**: `>=2.0.0`
- **Alembic**: `>=1.13.0` (для PostgreSQL миграций)
- **SQLite**: миграции не требуются (схема создаётся автоматически)

---

## Database URLs

### DATABASE_URL Format

**Допустимые форматы:**
- `postgresql://user:pass@host:5432/db`
- `postgres://user:pass@host:5432/db`
- `sqlite:///./platform.db`
- `sqlite:///:memory:`

**Нормализация:**
- Код автоматически нормализует PostgreSQL URL для SQLAlchemy/Alembic
- `postgresql://...` → `postgresql+psycopg://...` (для SQLAlchemy)
- `postgres://...` → `postgresql+psycopg://...` (для SQLAlchemy)
- SQLite URL не нормализуются
- Оригинальный `DATABASE_URL` в переменных окружения не изменяется

**Важно:**
- `psycopg2` **intentionally not supported**
- Если требуется `psycopg2` — это ошибка конфигурации или кода
- Проверка: `engine.driver == "psycopg"` (не `psycopg2`)

**Документация:**
- [`adr/0001-psycopg-v3-database-url-normalization.md`](adr/0001-psycopg-v3-database-url-normalization.md) — архитектурное решение
- [`../utils/db_url.py`](../utils/db_url.py) — реализация нормализации

---

## Dependency Policy

### Обновление зависимостей

**Правила:**
1. **Minor/Patch updates**: безопасны, можно обновлять без ADR
2. **Major updates**: требуют анализа и, возможно, ADR
3. **Security updates**: приоритет, обновлять немедленно

### SQLAlchemy Major Upgrade

Если планируется major upgrade SQLAlchemy (например, 2.0 → 3.0):

1. **Проверить breaking changes** в changelog SQLAlchemy
2. **Обновить тесты** для проверки совместимости
3. **Проверить Alembic** совместимость
4. **Создать ADR** (если требуется изменение архитектуры)
5. **Обновить CI matrix** (если требуется новая версия Python)

### psycopg v3 Major Upgrade

Если планируется major upgrade psycopg v3 (например, 3.1 → 4.0):

1. **Проверить breaking changes** в changelog psycopg
2. **Проверить SQLAlchemy** совместимость
3. **Обновить тесты** нормализации URL (если изменился формат URL)
4. **Создать ADR** (если требуется изменение архитектуры)
5. **Обновить `/ready` checks** (если изменился API подключения)

### Добавление новых зависимостей

**Правила:**
1. Добавить в `requirements.txt` с минимальной версией
2. Обновить `requirements-dev.txt` (если dev-only зависимость)
3. Обновить CI workflow (если требуется)
4. Обновить Dockerfile (если требуется системная зависимость)
5. **Создать ADR** (если зависимость критична для архитектуры)

---

## Testing Strategy

### SQLite для Unit/Integration тестов

- **По умолчанию**: все тесты используют SQLite
- **Преимущества**: быстрые, не требуют внешних зависимостей
- **Ограничения**: некоторые PostgreSQL-специфичные функции не тестируются

### PostgreSQL проверки

**Через docker-compose:**
- Запуск PostgreSQL контейнера для интеграционных тестов
- Проверка `/ready` endpoint
- Проверка Alembic миграций

**Команды:**
```bash
# Локальная проверка с PostgreSQL
docker-compose up -d postgres
export DATABASE_URL=postgresql://postgres:password@localhost:5432/agent_platform
python -m pytest tests/ -v
```

### Регрессионные тесты для Infra-логики

**Обязательны для:**
- Нормализация URL (`tests/test_database_url_normalization.py`)
- Database connection logic
- Migration scripts (Alembic)

**Правила:**
- Тесты должны быть детерминированными
- Не требуют реального подключения к БД (где возможно)
- Покрывают edge cases (None, пустые строки, сложные URL)

---

## Operational Notes

### Проверка Readiness

**Endpoint:** `GET /ready`

**Ожидаемый ответ для PostgreSQL:**
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

**Проверка:**
```bash
curl http://localhost:8000/ready
```

**Если `/ready` возвращает ошибку:**
1. Проверить логи контейнера: `docker-compose logs app`
2. Проверить подключение к PostgreSQL: `docker-compose exec app python -c "import psycopg; ..."`
3. Проверить версию схемы: `make db-current`
4. Проверить нормализацию URL: см. диагностику ниже

### Диагностика проблем с БД

**Проблема: `ImportError: No module named 'psycopg2'`**

**Диагностика:**
```bash
# 1. Проверить, что DATABASE_URL нормализуется
docker-compose exec app python -c "
from utils.db_url import normalize_database_url
import os
url = os.getenv('DATABASE_URL', '')
print(f'Original: {url}')
print(f'Normalized: {normalize_database_url(url)}')
"

# 2. Проверить SQLAlchemy driver
docker-compose exec app python -c "
from sqlalchemy import create_engine
from utils.db_url import normalize_database_url
import os
url = normalize_database_url(os.getenv('DATABASE_URL', ''))
engine = create_engine(url)
print(f'Driver: {engine.driver}')
"
# Ожидается: Driver: psycopg
```

**Решение:**
- Убедиться, что `utils/db_url.py` присутствует в образе
- Проверить, что `normalize_database_url` вызывается в `app/main.py` и `alembic/env.py`
- Пересобрать образ: `docker-compose build --no-cache app`

**Проблема: Schema version mismatch**

**Диагностика:**
```bash
# Проверить текущую версию
make db-current

# Проверить head revision
make db-history | head -1

# Применить миграции
make db-upgrade
```

**Решение:**
- Применить миграции: `make db-upgrade`
- Если миграции не применяются — проверить Alembic конфигурацию
- Если требуется rollback — использовать `make db-downgrade`

### Метрики и логи

**Метрики:**
- Endpoint: `GET /metrics` (если `METRICS_ENABLED=1`)
- Формат: Prometheus exposition format
- Метрики: HTTP requests, billing webhooks, recurring runs

**Логи:**
- Формат: JSON (production) или pretty (development)
- Уровень: настраивается через `LOG_LEVEL`
- Request-ID: автоматически добавляется в заголовки и логи

**Проверка:**
```bash
# Метрики
curl http://localhost:8000/metrics | grep -E "http_requests_total|billing_webhook" | head -5

# Логи
docker-compose logs app --tail=100 | grep -E '"level":"(ERROR|CRITICAL)"'
```

---

## When Touching Database Layer

⚠️ **Критически важно** — при любых изменениях в database layer:

### Обязательные действия

1. **Обновить тесты:**
   - Регрессионные тесты для нормализации URL (если изменяется `utils/db_url.py`)
   - Тесты для `/ready` endpoint (если изменяется readiness check)
   - Тесты для Alembic (если изменяется `alembic/env.py`)
   - Тесты для invoice (если изменяется агрегация в `billing_usage` или `get_invoice()`)

2. **Проверить `/ready` endpoint:**
   - Убедиться, что endpoint корректно проверяет новую логику
   - Проверить, что ошибки обрабатываются gracefully

3. **Обновить документацию:**
   - README.md (если изменяется поведение)
   - ADR (если это архитектурное изменение)
   - MAINTENANCE.md (если изменяется dependency policy)

4. **Создать ADR (если требуется):**
   - Любые архитектурные изменения в database layer
   - Изменения в стратегии нормализации URL
   - Переход на другой драйвер БД

### Что требует особого внимания

**Изменения в:**
- `utils/db_url.py` — нормализация URL
- `app/main.py` — `/ready` endpoint, database connection
- `alembic/env.py` — конфигурация Alembic
- `requirements.txt` — зависимости (psycopg, sqlalchemy, alembic)
- `cyberplat/billing_service.py` — `get_invoice()` или агрегация `billing_usage` (invoice computed, не требует миграций, но требует обновления тестов)

**Проверка после изменений:**
```bash
# 1. Все тесты зелёные
python -m pytest -q

# 2. /ready работает
curl http://localhost:8000/ready

# 3. SQLAlchemy использует правильный драйвер
docker-compose exec app python -c "
from sqlalchemy import create_engine
from utils.db_url import normalize_database_url
import os
url = normalize_database_url(os.getenv('DATABASE_URL', ''))
engine = create_engine(url)
assert engine.driver == 'psycopg', f'Expected psycopg, got {engine.driver}'
print('✓ Driver verification passed')
"
```

---

## Code Ownership

**Критические зоны:**
- `/cyberplat/billing/` — billing система (Clean Architecture)
- `/app/main.py` — главный FastAPI application
- `/.github/` — CI/CD, PR templates, CODEOWNERS

**См. также:**
- [`.github/CODEOWNERS`](../.github/CODEOWNERS) — назначенные владельцы

---

## Long-term Support Checklist

При работе с проектом в долгосрочной перспективе:

- [ ] Поддерживать актуальность ADR (обновлять статус, добавлять новые)
- [ ] Документировать breaking changes в ADR
- [ ] Обновлять MAINTENANCE.md при изменении dependency policy
- [ ] Проверять совместимость при major upgrades зависимостей
- [ ] Поддерживать регрессионные тесты для infra-логики
- [ ] Обновлять `/ready` checks при изменении database layer

---

**Последнее обновление**: 2024-12-19
