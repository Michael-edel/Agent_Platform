# Project Documentation

Единая точка входа в документацию проекта Agent Platform / CyberPlat.

---

## 📚 Быстрая навигация

- **[Архитектура](#архитектура)** — Clean Architecture, Event-driven design, Multi-tenant
- **[ADR (Architecture Decision Records)](#adr-architecture-decision-records)** — зафиксированные архитектурные решения
- **[Database & Migrations](#database--migrations)** — PostgreSQL, SQLite, Alembic, psycopg v3
- **[Observability](#observability)** — логирование, метрики, health checks
- **[Deployment](#deployment)** — Docker, docker-compose, CI/CD
- **[Operations](#operations)** — post-merge checklist, rollback, maintenance

---

## Архитектура

### Clean Architecture Overview

Проект следует принципам Clean Architecture с разделением на слои:

- **Domain Layer** (`cyberplat/billing/domain/`): чистый бизнес-логика, события, интерфейсы (ports)
- **Application Layer** (`cyberplat/billing/application/`): use cases (ApplyPlanUseCase, ProcessWebhookUseCase, RenewSubscriptionsUseCase)
- **Infrastructure Layer** (`cyberplat/billing/infrastructure/`): адаптеры (Stripe, Kaspi, DB, S3)

**Документация:**
- [`../FINAL_ARCHITECTURE_STATUS.md`](../FINAL_ARCHITECTURE_STATUS.md) — финальный архитектурный статус (production-ready)
- [`../REFACTORING_PLAN.md`](../REFACTORING_PLAN.md) — план рефакторинга billing системы
- [`../REFACTORING_SUMMARY.md`](../REFACTORING_SUMMARY.md) — сводка рефакторинга
- [`../REFACTORING_STATUS.md`](../REFACTORING_STATUS.md) — текущий статус рефакторинга

### Event-Driven Design

Система построена на событиях:
- `artifact.created`, `document.extracted`, `payment.ready`, `payment.invalid`
- `invoice.paid`, `subscription.updated` (billing)
- Billing и usage **всегда** считаются из событий, не из прямых мутаций

### Multi-tenant Model

- Строгая изоляция данных по `tenant_id`
- Все запросы tenant-scoped
- Критический инвариант: нет cross-tenant доступа

### Billing & Subscriptions

- **Stripe**: стандартные подписки, webhooks, Customer Portal
- **Kaspi**: эмуляция подписок через Hosted Checkout + token + cron autocharge
- **Usage-based billing**: автоматический подсчёт usage из событий
- **Recurring billing**: единый use case для всех провайдеров
- **Invoice**: вычисляется из `billing_usage` (не хранится как отдельная таблица), используется для отображения и billing preview

---

## ADR (Architecture Decision Records)

ADR фиксируют важные архитектурные решения проекта и причины их принятия.

**Индекс ADR:** [`adr/README.md`](adr/README.md)

**Текущие ADR:**
- [ADR-0001: psycopg v3 and DATABASE_URL normalization](adr/0001-psycopg-v3-database-url-normalization.md) — решение о нормализации DATABASE_URL для совместимости с psycopg v3

**Как добавить новый ADR:**
1. Создать файл `docs/adr/000X-title.md`
2. Использовать формат: Context / Decision / Consequences
3. Обновить [`adr/README.md`](adr/README.md)

---

## Database & Migrations

### PostgreSQL / SQLite Strategy

- **Development**: SQLite (по умолчанию, через `PLATFORM_DB_PATH`)
- **Staging/Production**: PostgreSQL (через `DATABASE_URL`)
- Миграции не требуются для SQLite (схема создаётся автоматически)
- PostgreSQL управляется через **Alembic**

### psycopg v3 Usage

- Проект использует **psycopg v3** (`psycopg[binary]`), а не psycopg2
- `DATABASE_URL` можно указывать в стандартном формате (`postgresql://...` или `postgres://...`)
- Код автоматически нормализует URL для SQLAlchemy/Alembic: `postgresql://...` → `postgresql+psycopg://...`
- **psycopg2 не используется и не нужен**

**Документация:**
- [`adr/0001-psycopg-v3-database-url-normalization.md`](adr/0001-psycopg-v3-database-url-normalization.md) — архитектурное решение
- [`../README.md`](../README.md) — раздел "PostgreSQL + psycopg v3"

### Alembic Migrations

- Управление схемой БД для PostgreSQL
- `/ready` endpoint проверяет соответствие версии схемы
- Команды: `make db-current`, `make db-upgrade`, `make db-check`

**Документация:**
- [`../README.md`](../README.md) — раздел "Database Migrations"
- [`../DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — секция "Database Migrations (Alembic)"

---

## Observability

### Logging

- Структурированные JSON логи (production) или pretty формат (development)
- Автоматическое добавление `request_id`, `tenant_id`, `event_id`
- Фильтрация секретов (Stripe keys, tokens, etc.)

**Настройка:**
```env
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT=json         # json (production) или pretty (development)
```

### Metrics

- Endpoint `/metrics` (Prometheus exposition format)
- Метрики: HTTP requests, billing webhooks, recurring runs
- Tenant-safe: не включает tenant_id в labels

**Настройка:**
```env
METRICS_ENABLED=1       # 1 для включения, 0 для отключения
```

### Health & Readiness Endpoints

- `/health` — проверка жизнеспособности процесса
- `/ready` — проверка готовности (зависимости + schema version для PostgreSQL)

**Документация:**
- [`../README.md`](../README.md) — раздел "Observability"

---

## Deployment

### Docker & docker-compose

- Multi-stage Docker build (Python 3.13-slim)
- Non-root пользователь для безопасности
- Healthcheck настроен автоматически
- PostgreSQL сервис в docker-compose

**Документация:**
- [`../Dockerfile`](../Dockerfile)
- [`../docker-compose.yml`](../docker-compose.yml)
- [`../DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — полное руководство по деплою

### CI/CD

- GitHub Actions workflow (`.github/workflows/ci.yml`)
- Матрица: Ubuntu + Windows, Python 3.12 + 3.13
- Проверки: ruff lint, pytest, Alembic syntax validation

**Документация:**
- [`../README.md`](../README.md) — раздел "CI / Quality Gate"
- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

---

## Operations

### Post-Merge Checklist

Чеклист для безопасного деплоя после merge PR:

**Документация:**
- [`../POST_MERGE_DEPLOYMENT_CHECKLIST.md`](../POST_MERGE_DEPLOYMENT_CHECKLIST.md) — полный чеклист

**Ключевые проверки:**
- CI/Build verification
- Docker/Runtime smoke checks
- Database & Alembic checks
- Driver verification (SQLAlchemy использует psycopg v3)
- Observability (логи, метрики)

### Rollback Strategy

- Rollback безопасен (не требует DB downgrade)
- Изменения только в runtime коде
- Процедура отката описана в deployment checklist

**Документация:**
- [`../POST_MERGE_DEPLOYMENT_CHECKLIST.md`](../POST_MERGE_DEPLOYMENT_CHECKLIST.md) — раздел "Rollback Safety"

### Maintenance Notes

Долгосрочная поддержка проекта:

**Документация:**
- [`MAINTENANCE.md`](MAINTENANCE.md) — maintenance notes, dependency policy, testing strategy

---

## Дополнительная документация

### Git Workflow

- Git Hooks v2.1 (pre-commit, post-commit)
- Автоматическая блокировка секретов, больших файлов, БД файлов
- Безопасный autopush

**Документация:**
- [`../GIT_HOOKS_SECURITY.md`](../GIT_HOOKS_SECURITY.md)
- [`../GIT_HOOKS_IMPROVEMENTS.md`](../GIT_HOOKS_IMPROVEMENTS.md)
- [`../FINAL_HOOKS_STATUS.md`](../FINAL_HOOKS_STATUS.md)

### Тестирование

- SQLite для unit/integration тестов
- PostgreSQL проверяется через docker-compose
- Регрессионные тесты для infra-логики

**Документация:**
- [`../KASPI_RECURRING_TEST_SUMMARY.md`](../KASPI_RECURRING_TEST_SUMMARY.md)
- [`../tests/TEST_KASPI_RECURRING.md`](../tests/TEST_KASPI_RECURRING.md)

### Quick Start

- [`../QUICK_START.md`](../QUICK_START.md) — быстрый старт для новых разработчиков

---

## Структура документации

```
docs/
├── README.md (этот файл)
├── MAINTENANCE.md
└── adr/
    ├── README.md
    └── 0001-psycopg-v3-database-url-normalization.md
```

**Корневые документы:**
- [`../README.md`](../README.md) — главный README проекта
- [`../DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — руководство по деплою
- [`../FINAL_ARCHITECTURE_STATUS.md`](../FINAL_ARCHITECTURE_STATUS.md) — архитектурный статус

---

**Последнее обновление**: 2024-12-19
