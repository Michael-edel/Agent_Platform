# Deployment Guide

Руководство по развёртыванию Agent Platform / CyberPlat в различных окружениях.

## Содержание

1. [Архитектура окружений](#архитектура-окружений)
2. [Подготовка к деплою](#подготовка-к-деплою)
3. [Локальная разработка (Dev)](#локальная-разработка-dev)
4. [Staging окружение](#staging-окружение)
5. [Production окружение](#production-окружение)
6. [Выкатка новой версии](#выкатка-новой-версии)
7. [Откат версии](#откат-версии)
8. [Health Checks и мониторинг](#health-checks-и-мониторинг)
9. [Безопасное обновление billing/webhooks/cron](#безопасное-обновление-billingwebhookscron)
10. [Troubleshooting](#troubleshooting)

---

## Архитектура окружений

### Dev (Локальная разработка)
- **Цель**: Быстрая разработка и тестирование
- **Особенности**:
  - Hot-reload включён
  - Код монтируется как volume
  - **Database**: SQLite (по умолчанию, через `PLATFORM_DB_PATH`)
  - Debug режим
  - Все логи в stdout

### Staging
- **Цель**: Тестирование перед production
- **Особенности**:
  - Полная копия production окружения
  - **Database**: PostgreSQL (через `DATABASE_URL`)
  - Тестовые ключи Stripe/Kaspi
  - Отдельная БД
  - Мониторинг включён

### Production
- **Цель**: Рабочее окружение для пользователей
- **Особенности**:
  - Оптимизированный Docker образ
  - **Database**: PostgreSQL (через `DATABASE_URL`)
  - Non-root пользователь
  - Healthcheck
  - Resource limits
  - Логирование в централизованную систему
  - Бэкапы БД

---

## Подготовка к деплою

### 1. Требования

- Docker 20.10+
- Docker Compose 2.0+
- Git
- Доступ к репозиторию

### 2. Переменные окружения

Создайте `.env` файл на основе `.env.example`:

```bash
cp .env.example .env
```

**Критичные переменные для production:**
```env
# Database (PostgreSQL для staging/production)
DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform
POSTGRES_DB=agent_platform
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<secure-password>
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Для dev (SQLite) используйте:
# PLATFORM_DB_PATH=platform.db

# Billing (обязательно)
BILLING_ENABLED=1
BILLING_ADMIN_KEY=<secure-random-key>

# Stripe (если используется)
STRIPE_ENABLED=1
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Kaspi (если используется)
KASPI_ENABLED=1
KASPI_API_KEY=...
KASPI_WEBHOOK_SECRET=...

# S3 Export (если используется)
S3_EXPORT_ENABLED=1
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET=agent-platform-prod
```

⚠️ **Безопасность**: Никогда не коммитьте `.env` файл с реальными секретами!

### 3. Docker образ

Соберите образ:
```bash
docker-compose build
```

Или для production:
```bash
docker build -t agent-platform:latest .
```

---

## Локальная разработка (Dev)

### Быстрый старт

```bash
# 1. Создайте docker-compose.override.yml для dev
cp docker-compose.override.yml.example docker-compose.override.yml

# 2. Запустите
docker-compose up -d

# 3. Смотрите логи
docker-compose logs -f app

# 4. Остановите
docker-compose down
```

### Особенности Dev окружения

- **Hot-reload**: Изменения в коде автоматически перезагружают сервер
- **Volume mount**: Код монтируется из локальной директории
- **Debug режим**: Подробные логи и stack traces
- **Локальная БД**: SQLite файл в `./data/platform.db` (по умолчанию)
- **PostgreSQL опционален**: Можно использовать PostgreSQL в dev, раскомментировав в `docker-compose.override.yml`

### Отладка

```bash
# Войти в контейнер
docker-compose exec app bash

# Запустить тесты внутри контейнера
docker-compose exec app python -m pytest -q

# Проверить логи
docker-compose logs app | grep ERROR
```

---

## Staging окружение

### Настройка

1. Создайте `.env.staging` из шаблона:
   ```bash
   cp .env.staging.example .env.staging
   ```

2. Заполните `.env.staging`:
   - `POSTGRES_PASSWORD` (реальный пароль для staging)
   - Тестовые ключи Stripe/Kaspi
   - `DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform`

3. Запустите с staging конфигом:
   ```bash
   docker-compose --env-file .env.staging up -d
   ```

4. Проверьте готовность:
   ```bash
   curl http://localhost:8000/ready
   # Должен показать: "database": "ok (postgresql)"
   ```

### Тестирование перед production

- ✅ Все тесты проходят
- ✅ Webhooks работают (тестовые ключи)
- ✅ Recurring billing работает
- ✅ S3 экспорт работает
- ✅ Healthcheck проходит

---

## Production окружение

### Рекомендации

1. **Используйте PostgreSQL** (обязательно для production)
   - Настройте `DATABASE_URL=postgresql://user:pass@postgres:5432/agent_platform`
   - Используйте сильные пароли
   - Рассмотрите внешний managed PostgreSQL (AWS RDS, Google Cloud SQL, etc.)

2. **Настройте бэкапы** БД регулярно
   - Автоматические бэкапы через `pg_dump` или managed service
   - Тестируйте восстановление из бэкапов

3. **Используйте secrets management** (AWS Secrets Manager, HashiCorp Vault)
   - Храните `POSTGRES_PASSWORD`, `STRIPE_SECRET_KEY`, etc. в secrets manager

4. **Настройте мониторинг** (Prometheus, Grafana, или облачные решения)
   - Метрики доступны на `/metrics` endpoint

5. **Настройте логирование** в централизованную систему (ELK, CloudWatch, etc.)

### Docker Compose для Production

Создайте `docker-compose.prod.yml`:

```yaml
version: '3.8'

services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    restart: always
    env_file:
      - .env.prod
    volumes:
      - app-data:/app/data
      - app-out:/app/out
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '1.0'
          memory: 1G
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

Запуск:
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## Выкатка новой версии

### Manual Steps

1. **Проверка перед деплоем:**
   ```bash
   # Локально
   make check  # lint + test
   
   # В CI
   # Убедитесь, что все проверки прошли
   ```

2. **Подготовка:**
   ```bash
   # Получите последние изменения
   git pull origin main
   
   # Проверьте изменения
   git log --oneline HEAD~5..HEAD
   ```

3. **Бэкап (критично для production):**
   ```bash
   # Бэкап PostgreSQL (staging/production)
   docker-compose exec postgres pg_dump -U postgres agent_platform > backup-$(date +%Y%m%d-%H%M%S).sql
   
   # Или для внешней БД (если DATABASE_URL указан)
   pg_dump $DATABASE_URL > backup-$(date +%Y%m%d-%H%M%S).sql
   
   # Для SQLite (dev, если используется)
   docker-compose exec app sqlite3 /app/data/platform.db ".backup /app/data/backup-$(date +%Y%m%d-%H%M%S).db"
   ```

4. **Применить миграции БД (если есть новые, для PostgreSQL):**
   ```bash
   # Установите DATABASE_URL
   export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform
   
   # Проверьте текущую версию
   make db-current
   
   # Примените миграции
   make db-upgrade
   
   # Проверьте, что миграции применены
   make db-check
   ```

5. **Остановка сервисов (опционально, если нужен downtime):**
   ```bash
   docker-compose down
   ```

6. **Обновление кода:**
   ```bash
   git pull origin main
   ```

7. **Пересборка образа:**
   ```bash
   docker-compose build --no-cache
   ```

8. **Запуск:**
   ```bash
   docker-compose up -d
   ```

9. **Проверка готовности:**
   ```bash
   # Проверьте, что приложение готово
   curl http://localhost:8000/ready
   # Должен вернуть: "database_migration": "ok (revision: ...)"
   ```

8. **Проверка:**
   ```bash
   # Логи
   docker-compose logs -f app
   
   # Healthcheck
   curl http://localhost:8000/health
   
   # Проверка работы API
   curl http://localhost:8000/docs
   ```

### Zero-Downtime Deployment (опционально)

Для production рекомендуется использовать:
- **Blue-Green deployment**: Два идентичных окружения, переключение через load balancer
- **Rolling updates**: Постепенное обновление контейнеров
- **Kubernetes**: Для автоматического управления deployment

---

## Откат версии

### Быстрый откат

1. **Остановите текущую версию:**
   ```bash
   docker-compose down
   ```

2. **Вернитесь к предыдущей версии:**
   ```bash
   git checkout <previous-commit-hash>
   # или
   git checkout <previous-tag>
   ```

3. **Восстановите БД из бэкапа (если нужно):**
   ```bash
   # Для SQLite
   cp /app/data/backup-YYYYMMDD-HHMMSS.db /app/data/platform.db
   
   # Для PostgreSQL
   psql $DATABASE_URL < backup-YYYYMMDD-HHMMSS.sql
   ```

4. **Пересоберите и запустите:**
   ```bash
   docker-compose build
   docker-compose up -d
   ```

5. **Проверьте:**
   ```bash
   docker-compose logs -f app
   curl http://localhost:8000/health
   ```

---

## Database Migrations (Alembic)

### Обзор

**SQLite (Dev):**
- Используется по умолчанию для локальной разработки
- Схема создаётся автоматически через `ensure_schema()` в сервисах
- Миграции **не требуются** для SQLite

**PostgreSQL (Staging/Production):**
- Управляется через **Alembic**
- Требует актуальной версии схемы перед запуском приложения
- `/ready` endpoint автоматически проверяет соответствие версии схемы

### Команды миграций

```bash
# Показать текущую версию
make db-current

# Показать историю миграций
make db-history

# Применить миграции (upgrade to head)
make db-upgrade

# Откатить последнюю миграцию
make db-downgrade

# Проверить, что revision == head (для CI/staging)
make db-check
```

### Initial Migration (первый запуск)

При первом развёртывании PostgreSQL:

```bash
# 1. Установите DATABASE_URL
export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform

# 2. Примените начальную миграцию
make db-upgrade

# 3. Проверьте версию
make db-current
# Должно показать: d01c4c0f81ed (initial_schema)
```

### Safe Deploy Flow (безопасный деплой)

**Порядок действий при обновлении:**

1. **Бэкап БД:**
   ```bash
   docker-compose exec postgres pg_dump -U postgres agent_platform > backup-$(date +%Y%m%d-%H%M%S).sql
   ```

2. **Применить миграции:**
   ```bash
   export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform
   make db-upgrade
   ```

3. **Проверить готовность:**
   ```bash
   curl http://localhost:8000/ready
   # Должен вернуть: "database_migration": "ok (revision: ...)"
   ```

4. **Деплой приложения:**
   ```bash
   docker-compose up -d app
   ```

5. **Мониторинг:**
   - Проверьте логи: `docker-compose logs -f app`
   - Проверьте метрики: `curl http://localhost:8000/metrics`

### Rollback (откат миграций)

Если миграция вызвала проблемы:

```bash
# 1. Откатить последнюю миграцию
make db-downgrade

# 2. Проверить версию
make db-current

# 3. Откатить приложение (если нужно)
docker-compose down
docker-compose up -d app
```

⚠️ **Важно**: Откат миграций может привести к потере данных, если миграция удаляла таблицы/колонки. Всегда делайте бэкап перед миграциями.

### Schema Version Mismatch

Если `/ready` показывает `"database_migration": "not_up_to_date"`:

**Симптомы:**
```json
{
  "status": "degraded",
  "checks": {
    "database": "error: schema version mismatch (current: abc123, expected: def456)",
    "database_migration": "not_up_to_date"
  }
}
```

**Решение:**
```bash
# 1. Проверить текущую версию
make db-current

# 2. Применить миграции
make db-upgrade

# 3. Проверить готовность снова
curl http://localhost:8000/ready
```

### Миграции в CI

CI автоматически проверяет валидность миграций:
- `alembic history` выполняется для проверки синтаксиса
- Миграции не применяются автоматически (только проверка)

Для staging/production миграции применяются вручную перед деплоем.

## PostgreSQL Staging/Production

### Настройка PostgreSQL в Docker Compose

PostgreSQL сервис уже настроен в `docker-compose.yml`:
- **Image**: `postgres:16-alpine`
- **Healthcheck**: `pg_isready` для проверки готовности
- **Volume**: `pg-data` для персистентности данных
- **Port**: 5432 (открыт для dev/staging, в production можно закрыть)

### Подключение приложения к PostgreSQL

1. **Установите переменные окружения:**
   ```env
   DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform
   POSTGRES_DB=agent_platform
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your-secure-password
   POSTGRES_HOST=postgres
   POSTGRES_PORT=5432
   ```

2. **Проверьте подключение:**
   ```bash
   curl http://localhost:8000/ready
   # Должен вернуть: "database": "ok (postgresql)"
   ```

### Бэкапы PostgreSQL

**Создание бэкапа:**
```bash
# Внутри контейнера postgres
docker-compose exec postgres pg_dump -U postgres agent_platform > backup-$(date +%Y%m%d-%H%M%S).sql

# Или извне (если порт открыт)
pg_dump -h localhost -p 5432 -U postgres agent_platform > backup.sql
```

**Восстановление из бэкапа:**
```bash
# Внутри контейнера
docker-compose exec -T postgres psql -U postgres agent_platform < backup.sql

# Или извне
psql -h localhost -p 5432 -U postgres agent_platform < backup.sql
```

**Автоматические бэкапы (cron):**
```bash
# Добавьте в crontab или используйте docker-compose с volume для бэкапов
0 2 * * * docker-compose exec -T postgres pg_dump -U postgres agent_platform | gzip > /backups/backup-$(date +\%Y\%m\%d).sql.gz
```

### Миграции (устарело, см. раздел "Database Migrations" выше)

⚠️ **Примечание**: См. раздел "Database Migrations (Alembic)" выше для актуальной информации.

**Текущее состояние:**
- SQLite (dev): схема создаётся автоматически, миграции не требуются
- PostgreSQL (staging/prod): управляется через Alembic, требует применения миграций перед запуском

### Volumes и персистентность

**PostgreSQL данные:**
- Volume `pg-data` хранит данные PostgreSQL
- Расположение: Docker volume (по умолчанию)
- Для production: рассмотрите внешний volume или managed service

**Проверка volume:**
```bash
docker volume inspect agent-platform_pg-data
```

## Health Checks и мониторинг

### Health Endpoint

Приложение предоставляет health и readiness endpoints:
```bash
curl http://localhost:8000/health   # Проверка жизнеспособности
curl http://localhost:8000/ready   # Проверка готовности (включая БД)
```

**Ожидаемый ответ `/health`**: `{"status": "ok", ...}`

**Ожидаемый ответ `/ready`**: 
```json
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql)" or "ok (sqlite)",
    "billing_service": "ok",
    "entitlement_service": "ok"
  }
}
```

### Docker Healthcheck

Настроен автоматически в `Dockerfile` и `docker-compose.yml`:
- **Interval**: 30s
- **Timeout**: 10s
- **Retries**: 3
- **Start period**: 40s

**PostgreSQL healthcheck:**
- **Interval**: 10s
- **Timeout**: 5s
- **Retries**: 5
- **Start period**: 10s

### Мониторинг метрик

Рекомендуется мониторить:
- **CPU и Memory usage**
- **Request rate и latency**
- **Error rate**
- **Database connections**
- **Billing operations** (webhooks, recurring jobs)

### Логирование

```bash
# Просмотр логов
docker-compose logs -f app

# Поиск ошибок
docker-compose logs app | grep ERROR

# Экспорт логов
docker-compose logs app > app-logs-$(date +%Y%m%d).log
```

---

## Безопасное обновление billing/webhooks/cron

### Webhooks

⚠️ **Критично**: Webhooks должны быть идемпотентными!

**Перед обновлением:**
1. Убедитесь, что все webhook события обработаны
2. Проверьте `billing_webhook_events` таблицу на незавершённые события

**Во время обновления:**
- Webhooks могут приходить во время деплоя
- Идемпотентность гарантирует, что повторная обработка безопасна

**После обновления:**
```bash
# Проверьте логи на ошибки webhook
docker-compose logs app | grep -i webhook

# Проверьте статус событий
docker-compose exec app sqlite3 /app/data/platform.db "SELECT provider, status, COUNT(*) FROM billing_webhook_events GROUP BY provider, status;"
```

### Recurring Billing (Cron)

**Перед обновлением:**
1. Дождитесь завершения текущего recurring job
2. Проверьте, что нет активных процессов

**Во время обновления:**
- Recurring jobs должны быть идемпотентными
- Если cron запускается во время деплоя, он должен корректно обработать ситуацию

**После обновления:**
```bash
# Вручную запустите recurring для проверки
docker-compose exec app python scripts/run_kaspi_recurring.py
docker-compose exec app python scripts/run_stripe_recurring.py
```

### Рекомендуемый порядок обновления

1. **Staging**: Сначала протестируйте на staging
2. **Production (off-peak)**: Обновляйте в период низкой нагрузки
3. **Мониторинг**: Следите за метриками после обновления
4. **Rollback plan**: Будьте готовы откатиться при проблемах

---

## Troubleshooting

### Контейнер не запускается

```bash
# Проверьте логи
docker-compose logs app

# Проверьте конфигурацию
docker-compose config

# Проверьте переменные окружения
docker-compose exec app env | grep -E "BILLING|STRIPE|KASPI"
```

### Healthcheck падает

```bash
# Проверьте, что приложение запущено
docker-compose ps

# Проверьте логи
docker-compose logs app | tail -50

# Проверьте порт
curl http://localhost:8000/health
```

### Проблемы с БД

```bash
# Проверьте права доступа
docker-compose exec app ls -la /app/data

# Проверьте целостность БД
docker-compose exec app sqlite3 /app/data/platform.db "PRAGMA integrity_check;"
```

### Webhooks не работают

```bash
# Проверьте секреты
docker-compose exec app env | grep WEBHOOK_SECRET

# Проверьте логи webhook
docker-compose logs app | grep -i webhook

# Проверьте таблицу событий
docker-compose exec app sqlite3 /app/data/platform.db "SELECT * FROM billing_webhook_events ORDER BY created_at DESC LIMIT 10;"
```

### Recurring billing не работает

```bash
# Запустите вручную для диагностики
docker-compose exec app python scripts/run_kaspi_recurring.py

# Проверьте env переменные
docker-compose exec app env | grep -E "KASPI|STRIPE|BILLING"
```

---

## Дополнительные ресурсы

- [README.md](README.md) — общая документация проекта
- [.env.example](.env.example) — шаблон переменных окружения
- [Makefile](Makefile) — стандартные команды для разработки

---

**Последнее обновление**: 2024
