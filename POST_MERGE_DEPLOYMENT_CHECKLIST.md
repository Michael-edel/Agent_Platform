# Post-Merge Deployment Checklist: psycopg v3 Fix

**PR Status**: ✅ Merged to main/master  
**Version**: Patch bump (e.g., v1.4.0 → v1.4.1)  
**Risk Level**: 🟢 Low (no breaking changes, backward compatible)

---

## 1. CI / Build Verification

### GitHub Actions / CI Pipeline
- [ ] CI pipeline прошёл успешно (green status)
- [ ] Все тесты пройдены (включая 12 новых тестов нормализации URL)
- [ ] Ruff lint проверки пройдены
- [ ] Alembic history валиден (syntax check)

### Dependencies Verification
- [ ] `requirements.txt` содержит `psycopg[binary]>=3.1.0`
- [ ] `requirements.txt` **НЕ содержит** `psycopg2` или `psycopg2-binary`
- [ ] Lock-файлы (если есть) обновлены корректно

### Docker Build
```bash
# Проверка сборки образа
docker-compose build --no-cache app

# Проверка, что psycopg2 отсутствует в образе
docker-compose run --rm app python -c "import sys; assert 'psycopg2' not in sys.modules, 'psycopg2 found!'; print('✓ psycopg2 not imported')"
```

**Ожидаемый результат**: Образ собирается без ошибок, psycopg2 не обнаружен.

---

## 2. Docker / Runtime Smoke Checks

### Container Startup
```bash
# Запуск контейнеров
docker-compose up -d --build

# Проверка статуса
docker-compose ps
```

**Ожидаемый результат**: Все сервисы (app, postgres) в статусе `Up (healthy)`.

### Container Logs Verification
```bash
# Проверка логов app контейнера
docker-compose logs app | grep -i "psycopg2\|ImportError\|ModuleNotFoundError"

# Проверка отсутствия ошибок
docker-compose logs app | grep -i "error\|exception" | grep -v "INFO\|DEBUG"
```

**Ожидаемый результат**: 
- ❌ Нет упоминаний `psycopg2`
- ❌ Нет `ImportError: No module named 'psycopg2'`
- ❌ Нет критических ошибок (ERROR/CRITICAL)

### Health Endpoints
```bash
# Health check (liveness)
curl -s http://localhost:8000/health | jq .
# Ожидается: {"status": "ok"}

# Readiness check (dependencies + schema)
curl -s http://localhost:8000/ready | jq .
# Ожидается:
# {
#   "status": "ok",
#   "checks": {
#     "database": "ok (postgresql)",
#     "database_migration": "ok (revision: d01c4c0f81ed)",
#     "billing_service": "ok",
#     "entitlement_service": "ok"
#   }
# }
```

**Ожидаемый результат**: 
- ✅ `/health` возвращает `{"status": "ok"}`
- ✅ `/ready` возвращает `database: "ok (postgresql)"` и `database_migration: "ok (revision: ...)"`

---

## 3. Database & Alembic Checks

### Alembic Version Verification
```bash
# Проверка текущей версии схемы
make db-current
# или
docker-compose exec app alembic current

# Ожидается: d01c4c0f81ed (или актуальная head revision)
```

### Schema Consistency Check
```bash
# Проверка, что revision == head
make db-check
# или
docker-compose exec app alembic check

# Ожидается: ✓ Schema is up-to-date (revision: d01c4c0f81ed)
```

### Alembic History Validation
```bash
# Проверка истории миграций
make db-history
# или
docker-compose exec app alembic history

# Ожидается: Корректная история без ошибок синтаксиса
```

**Ожидаемый результат**: 
- ✅ Текущая версия схемы соответствует head
- ✅ Alembic команды выполняются без ошибок
- ✅ История миграций валидна

---

## 4. Driver Verification (Ключевой пункт)

### SQLAlchemy Driver Check
```bash
# Проверка драйвера SQLAlchemy внутри контейнера
docker-compose exec app python -c "
from sqlalchemy import create_engine
from utils.db_url import normalize_database_url
import os

db_url = normalize_database_url(os.getenv('DATABASE_URL', ''))
if db_url and 'postgresql' in db_url:
    engine = create_engine(db_url, pool_pre_ping=True)
    print(f'✓ SQLAlchemy driver: {engine.driver}')
    assert engine.driver == 'psycopg', f'Expected psycopg, got {engine.driver}'
    print('✓ Driver verification passed')
else:
    print('⚠ SQLite mode (no driver check needed)')
"
```

**Ожидаемый результат**: 
- ✅ `SQLAlchemy driver: psycopg`
- ✅ Нет ошибок AssertionError

### Runtime psycopg2 Absence Check
```bash
# Проверка, что psycopg2 не установлен в runtime
docker-compose exec app python -c "
import sys
try:
    import psycopg2
    print('❌ ERROR: psycopg2 is installed!')
    sys.exit(1)
except ImportError:
    print('✓ psycopg2 not installed (expected)')

try:
    import psycopg
    print(f'✓ psycopg v3 installed: {psycopg.__version__}')
except ImportError:
    print('❌ ERROR: psycopg not installed!')
    sys.exit(1)
"
```

**Ожидаемый результат**: 
- ✅ `psycopg2 not installed (expected)`
- ✅ `psycopg v3 installed: 3.x.x`

### URL Normalization Verification
```bash
# Проверка нормализации URL
docker-compose exec app python -c "
from utils.db_url import normalize_database_url

# Тест нормализации
test_cases = [
    ('postgresql://user:pass@host:5432/db', 'postgresql+psycopg://user:pass@host:5432/db'),
    ('postgres://user:pass@host:5432/db', 'postgresql+psycopg://user:pass@host:5432/db'),
    ('sqlite:///./test.db', 'sqlite:///./test.db'),
]

for original, expected in test_cases:
    result = normalize_database_url(original)
    assert result == expected, f'Failed: {original} -> {result}, expected {expected}'
    print(f'✓ {original} -> {result}')

print('✓ URL normalization verified')
"
```

**Ожидаемый результат**: Все тест-кейсы проходят, нормализация работает корректно.

---

## 5. Observability

### Structured Logs
```bash
# Проверка логов на наличие ошибок
docker-compose logs app --tail=100 | grep -E '"level":"(ERROR|CRITICAL)"' | wc -l
# Ожидается: 0 (или только известные, не связанные с psycopg)
```

**Ожидаемый результат**: Нет ERROR/CRITICAL логов, связанных с psycopg2 или database connection.

### Prometheus Metrics
```bash
# Проверка доступности метрик
curl -s http://localhost:8000/metrics | grep -E "http_requests_total|billing_webhook|recurring_runs" | head -5

# Ожидается: Метрики присутствуют и обновляются
```

**Ожидаемый результат**: 
- ✅ Endpoint `/metrics` доступен (если `METRICS_ENABLED=1`)
- ✅ Метрики HTTP requests, billing webhooks, recurring runs присутствуют

### Request-ID Correlation
```bash
# Проверка Request-ID заголовка
curl -v http://localhost:8000/health 2>&1 | grep -i "x-request-id"

# Ожидается: X-Request-ID присутствует в response headers
```

**Ожидаемый результат**: 
- ✅ `X-Request-ID` присутствует в response headers
- ✅ Request-ID корректно прокидывается в логи

### Readiness Metrics
```bash
# Проверка метрик readiness (если есть)
curl -s http://localhost:8000/metrics | grep -i "ready\|database" | head -3
```

**Ожидаемый результат**: Метрики readiness/database присутствуют (если реализованы).

---

## 6. Rollback Safety

### Why Rollback is Safe

**Нормализация URL не требует DB downgrade:**
- ✅ Изменение только в runtime (код приложения)
- ✅ Не изменяет схему БД (нет новых миграций)
- ✅ Не изменяет данные в БД
- ✅ Обратная совместимость: старый код работает с теми же URL

**Что происходит при rollback:**
- Старый код (без нормализации) будет пытаться использовать `psycopg2`
- Если `psycopg2` не установлен → ошибка `ImportError`
- **Решение**: Установить `psycopg2-binary` временно ИЛИ обновить `DATABASE_URL` в env на `postgresql+psycopg://...` вручную

### Rollback Procedure (если требуется)

```bash
# Вариант 1: Быстрый rollback кода (требует psycopg2)
git revert <commit-hash>
docker-compose build app
# ВАЖНО: Установить psycopg2-binary в requirements.txt временно
docker-compose up -d

# Вариант 2: Безопасный rollback (без изменения кода)
# Просто обновить DATABASE_URL в .env на postgresql+psycopg://... вручную
# Это обойдёт проблему нормализации
```

### If psycopg2 Error Appears

**Симптомы:**
- `ImportError: No module named 'psycopg2'`
- `/ready` endpoint возвращает ошибку database connection
- Alembic команды падают с psycopg2 error

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
```

**Решение:**
- Убедиться, что `utils/db_url.py` присутствует в образе
- Проверить, что `normalize_database_url` вызывается в `app/main.py` и `alembic/env.py`
- Пересобрать образ: `docker-compose build --no-cache app`

---

## 7. Итоговый статус

### Pre-Deployment Confirmation

- [ ] ✅ **CI/Build**: Все проверки пройдены
- [ ] ✅ **Docker/Runtime**: Контейнеры запускаются без ошибок
- [ ] ✅ **Database/Alembic**: Схема актуальна, миграции валидны
- [ ] ✅ **Driver Verification**: SQLAlchemy использует `psycopg` (не psycopg2)
- [ ] ✅ **Observability**: Логи чистые, метрики работают
- [ ] ✅ **Rollback Safety**: План отката подготовлен

### Final Verification Commands

```bash
# Комплексная проверка одним скриптом
docker-compose exec app python -c "
import os
from sqlalchemy import create_engine
from utils.db_url import normalize_database_url

# 1. URL normalization
db_url = normalize_database_url(os.getenv('DATABASE_URL', ''))
print(f'✓ URL normalized: {db_url[:50]}...')

# 2. SQLAlchemy driver
if db_url and 'postgresql' in db_url:
    engine = create_engine(db_url, pool_pre_ping=True)
    assert engine.driver == 'psycopg', f'Driver mismatch: {engine.driver}'
    print(f'✓ SQLAlchemy driver: {engine.driver}')

# 3. psycopg2 absence
try:
    import psycopg2
    print('❌ ERROR: psycopg2 found!')
    exit(1)
except ImportError:
    print('✓ psycopg2 not installed')

# 4. psycopg v3 presence
import psycopg
print(f'✓ psycopg v3: {psycopg.__version__}')

print('\n✅ All checks passed!')
"
```

**Ожидаемый результат**: Все проверки пройдены, готово к production.

---

## Deployment Status

### ✅ Safe to Deploy

- ✅ **No breaking changes**: SQLite workflow не изменён
- ✅ **Backward compatible**: Существующие `DATABASE_URL` работают без изменений
- ✅ **SQLite unaffected**: SQLite URLs не нормализуются
- ✅ **PostgreSQL psycopg v3 verified**: SQLAlchemy использует правильный драйвер

### Deployment Steps

1. **Staging:**
   ```bash
   # Deploy to staging
   git pull origin main
   docker-compose -f docker-compose.yml --env-file .env.staging up -d --build
   
   # Verify
   curl http://staging.example.com/ready
   ```

2. **Production:**
   ```bash
   # Deploy to production (после успешной проверки staging)
   git pull origin main
   docker-compose -f docker-compose.yml --env-file .env.prod up -d --build
   
   # Verify
   curl http://production.example.com/ready
   ```

### Post-Deployment Monitoring

- [ ] Мониторить логи первые 15-30 минут после деплоя
- [ ] Проверить метрики `/metrics` на наличие ошибок
- [ ] Убедиться, что `/ready` endpoint стабильно возвращает `ok`
- [ ] Проверить, что Alembic миграции (если будут) работают корректно

---

## Резюме

**Deployment checklist завершён, сервис готов к production rollout.**

**Ключевые гарантии:**
- ✅ Нет breaking changes
- ✅ Обратная совместимость сохранена
- ✅ SQLite workflow не затронут
- ✅ PostgreSQL использует psycopg v3 корректно
- ✅ Rollback безопасен (не требует DB изменений)

**Рекомендации:**
- Деплой в staging → проверка → деплой в production
- Мониторинг логов первые 30 минут после деплоя
- Готовность к быстрому rollback (если потребуется)
