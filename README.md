# Agent Platform / CyberPlat

[![CI](https://github.com/your-org/Agent_Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/Agent_Platform/actions/workflows/ci.yml)

Production-grade multi-tenant SaaS платформа для обработки документов с event-driven архитектурой, автоматическим billing (Stripe + Kaspi) и экспортом в S3/MinIO.

## Что это

Платформа для автоматической обработки документов (OCR, извлечение данных, создание платежей) с поддержкой:
- **Multi-tenant архитектура** с строгой изоляцией данных
- **Event-driven core**: billing и usage считаются только из событий
- **Агенты**: `doc_agent` (OCR пайплайн), `payment_agent` (создание payment из invoice)
- **Billing Engine**: Stripe (стандартные подписки) + Kaspi (эмуляция подписок через Hosted Checkout + token + cron)
- **Usage-based billing** с квотами и автоматическим применением планов
- **S3/MinIO экспорт** артефактов и событий (опционально)

## Ключевые гарантии и инварианты

⚠️ **Критически важно — эти гарантии НЕЛЬЗЯ нарушать:**

- ✅ **Usage считается только из событий**: нет прямых мутаций usage вне обработки событий
- ✅ **Идемпотентность**: webhooks и cron-задачи идемпотентны (webhook event ledger, idempotency keys)
- ✅ **Tenant isolation**: все запросы tenant-scoped, строгая изоляция данных
- ✅ **Billing консистентность**: периоды подписок, renewals, invoices воспроизводимы и консистентны
- ✅ **Безопасность Git workflow**: коммиты с секретами, БД файлами, большими файлами (>1MB) блокируются автоматически

## Архитектура

### Event-Driven Core

Система построена на событиях:
- `artifact.created` — создан артефакт (document/invoice/payment)
- `document.extracted` — извлечены данные из документа
- `payment.ready` — payment готов к обработке
- `payment.invalid` — payment невалиден
- `invoice.paid` — invoice оплачен (billing)
- `subscription.updated` — подписка обновлена (billing)

Billing и usage **всегда** считаются из событий, не из прямых мутаций.

### Clean Architecture

Проект следует Clean Architecture с разделением на слои:

- **Domain** (`cyberplat/billing/domain/`): чистый бизнес-логика, события, интерфейсы (ports)
- **Application** (`cyberplat/billing/application/`): use cases (ApplyPlanUseCase, ProcessWebhookUseCase)
- **Infrastructure** (`cyberplat/billing/infrastructure/`): адаптеры (Stripe, Kaspi, DB, S3)

### Агенты

- **`doc_agent`**: OCR пайплайн для распознавания документов
- **`payment_agent`**: создание payment артефактов из invoice артефактов

## Billing

### Stripe

- **Checkout**: создание checkout session для одноразовых платежей
- **Subscriptions**: стандартные Stripe подписки
- **Webhooks**: обработка `invoice.paid`, `subscription.updated`, `checkout.session.completed`
- **Customer Portal**: управление подписками через Stripe Customer Portal

### Kaspi

Kaspi не поддерживает нативные подписки, поэтому реализована эмуляция:

- **Hosted Checkout**: создание checkout session через Kaspi API
- **Token-based recurring**: сохранение токена после успешной оплаты
- **Cron autocharge**: периодическое автоматическое списание через `kaspi_recurring.py`

### Usage-Based Billing

- **Quotas**: лимиты по метрикам (documents, invoices, payments)
- **Usage tracking**: автоматический подсчёт usage из событий
- **Plan application**: автоматическое применение планов при оплате/обновлении подписки

### Recurring Billing

Автоматическое продление подписок через единый use case (`RenewSubscriptionsUseCase`) с единым контрактом результата (`RecurringResult`):

- **Kaspi**: token-based autocharge через cron endpoint
  - Сохранение `kaspi_token` после успешной оплаты
  - Периодическое автоматическое списание через `POST /api/v1/billing/cron/charge-kaspi`
  - Создание `billing_order` и обновление `tenant_subscriptions.period_end`
  - Обработка ошибок: retry, cancel, downgrade на `plan_free`

- **Stripe**: subscription sync через cron-safe механизм
  - **НЕ инициирует** новые платежи (Stripe делает это автоматически)
  - Синхронизирует `current_period_start/end` из Stripe API
  - Обрабатывает статусы: `canceled`, `past_due`, `unpaid`, `incomplete_expired` → downgrade на `plan_free`
  - Пропускает подписки, если период уже синхронизирован (разница < 1 day)

- **Единый контракт результата**: `RecurringResult(charged, failed, skipped, errors)`
  - `charged`: количество успешно обработанных подписок
  - `failed`: количество неудачных (ошибки, отмены)
  - `skipped`: количество пропущенных (период ещё не истёк или уже синхронизирован)
  - `errors`: список ошибок для диагностики

### Планы по умолчанию

- `plan_free`: бесплатный план (ограниченные квоты)
- `plan_pro`: $29.99/месяц (высокие квоты)
- `plan_enterprise`: $99.99/месяц (unlimited квоты)

## Git Workflow

### Git Hooks (v2.1)

В репозитории настроены git hooks для безопасного workflow:

**Pre-commit hook:**
- ✅ Блокирует коммиты с секретами (в именах файлов и содержимом)
- ✅ Блокирует большие файлы (>1MB)
- ✅ Блокирует БД файлы (.db, .sqlite, .dump)
- ✅ Проверяет синтаксис Python
- ✅ Разрешает `git commit --allow-empty`

**Post-commit hook:**
- ✅ Автоматический push (включён по умолчанию)
- ✅ Не срабатывает во время rebase/merge/cherry-pick
- ✅ Отключается через `GIT_AUTO_PUSH=0` или `git config git-auto-push.enabled false`

**Сканирование содержимого на секреты:**
- Stripe keys (паттерн: `sk_live_*`, `sk_test_*`)
- AWS access keys (паттерн: `AKIA[0-9A-Z]{16}`)
- Private keys (паттерн: `-----BEGIN...PRIVATE...KEY-----` - блокирует RSA/EC ключи)
- Generic patterns (паттерны: `api_key=...`, `secret=...`, `token=...`, `password=...` с 20+ символами)

📚 **Подробная документация:**
- [`GIT_HOOKS_SECURITY.md`](GIT_HOOKS_SECURITY.md) — безопасность hooks
- [`GIT_HOOKS_IMPROVEMENTS.md`](GIT_HOOKS_IMPROVEMENTS.md) — улучшения v2.1
- [`FINAL_HOOKS_STATUS.md`](FINAL_HOOKS_STATUS.md) — финальный статус

## Конфигурация и запуск

### Установка

```bash
# Установка зависимостей
pip install -r requirements.txt
```

### Переменные окружения

Создайте `.env` файл из шаблона (⚠️ **не коммитьте секреты!**):

```bash
# Linux/Mac
cp .env.example .env

# Windows (PowerShell)
Copy-Item .env.example .env

# Windows (Git Bash)
cp .env.example .env
```

Затем заполните значения в `.env` файле. См. [`.env.example`](.env.example) для полного списка переменных.

**Минимальная конфигурация для разработки:**
```env
PLATFORM_DB_PATH=platform.db
BILLING_ENABLED=1
BILLING_DEFAULT_PLAN=plan_free
BILLING_ADMIN_KEY=dev-admin-key
```

### Запуск сервера

```bash
# Через Makefile (рекомендуется)
make run

# Или напрямую
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Или через Python
python -m app.main
```

### Тестирование

```bash
# Через Makefile
make test          # Быстрый запуск
make test-verbose  # Подробный вывод

# Или напрямую
python -m pytest -q
pytest tests/ -v
```

### Local Development & Operations

#### Быстрый старт

```bash
# 1. Установка зависимостей
make install
# или
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 2. Создание .env из шаблона
make setup-env
# или вручную: cp .env.example .env

# 3. Заполнение .env (отредактируйте .env файл)

# 4. Запуск проверок
make check  # lint + test
```

#### Доступные команды (Makefile)

```bash
make help              # Показать все команды
make install           # Установить зависимости
make test              # Запустить тесты
make lint              # Проверить код (ruff)
make check             # lint + test
make run               # Запустить сервер
make recurring-kaspi   # Запустить Kaspi recurring вручную
make recurring-stripe  # Запустить Stripe recurring вручную
make clean             # Очистить временные файлы
make setup-env         # Создать .env из шаблона
```

#### Ручной запуск recurring billing

Для тестирования или отладки recurring billing можно запустить вручную:

```bash
# Kaspi recurring
make recurring-kaspi
# или
python scripts/run_kaspi_recurring.py

# Stripe recurring (subscription sync)
make recurring-stripe
# или
python scripts/run_stripe_recurring.py
```

**Требования:**
- Kaspi: `BILLING_ENABLED=1`, `KASPI_ENABLED=1`
- Stripe: `BILLING_ENABLED=1`, `STRIPE_ENABLED=1`, `STRIPE_SECRET_KEY=...`

Скрипты выводят результат в формате `RecurringResult` и завершаются с кодом 0 (успех) или 1 (ошибка).

См. также:
- [`scripts/run_kaspi_recurring.py`](scripts/run_kaspi_recurring.py) — скрипт для Kaspi recurring
- [`scripts/run_stripe_recurring.py`](scripts/run_stripe_recurring.py) — скрипт для Stripe recurring
- [`.env.example`](.env.example) — шаблон переменных окружения

#### Локальная проверка перед коммитом

```bash
# Полная проверка
make check

# Или по отдельности
make lint   # ruff check .
make test   # python -m pytest -q
```

### CI / Quality Gate

Все изменения проходят автоматическую проверку через GitHub Actions:

- ✅ **Lint**: проверка кода через `ruff check` (E/F/I правила)
- ✅ **Тесты**: запуск всех тестов на Ubuntu и Windows (Python 3.12, 3.13)
- ✅ **Матрица**: проверка на разных ОС и версиях Python
- ✅ **Артефакты**: результаты тестов сохраняются при падении

**Обязательно**: все PR должны проходить CI gate перед merge в `main`.

```bash
# Локальная проверка перед push
ruff check .          # Проверка кода
python -m pytest -q   # Запуск тестов
```

## Deployment & Environments

### Локальный запуск через Docker

**Development (SQLite):**
```bash
# 1. Создайте .env файл из шаблона
make setup-env
# или
cp .env.example .env

# 2. Заполните .env файл (особенно секреты для billing)
# По умолчанию используется SQLite (PLATFORM_DB_PATH)

# 3. Запустите через docker-compose
docker-compose up -d

# 4. Проверьте логи
docker-compose logs -f app

# 5. Остановите
docker-compose down
```

**Staging (PostgreSQL):**
```bash
# 1. Создайте .env.staging из шаблона
cp .env.staging.example .env.staging

# 2. Заполните .env.staging (особенно POSTGRES_PASSWORD и секреты)

# 3. Запустите с staging конфигом
docker-compose --env-file .env.staging up -d

# 4. Проверьте готовность
curl http://localhost:8000/ready

# 5. Проверьте логи
docker-compose logs -f app postgres
```

### Dev vs Staging vs Production окружения

**Development (локально):**
- Используйте `docker-compose.override.yml` для hot-reload и отладки
- Код монтируется как volume для автоматической перезагрузки
- `DEBUG=1` включён по умолчанию
- **Database**: SQLite (по умолчанию, через `PLATFORM_DB_PATH`)
- PostgreSQL опционален (см. `docker-compose.override.yml.example`)

**Staging:**
- Используйте `docker-compose.yml` с `.env.staging`
- **Database**: PostgreSQL (через `DATABASE_URL`)
- Тестовые ключи Stripe/Kaspi
- Метрики и логи включены

**Production:**
- Используйте только `docker-compose.yml`
- Код копируется в образ (не монтируется)
- **Database**: PostgreSQL (через `DATABASE_URL`)
- Healthcheck включён
- Non-root пользователь для безопасности
- Resource limits (опционально)

### Настройка окружений

Все настройки через переменные окружения в `.env` файле:

**Database:**
- **Dev (по умолчанию)**: SQLite через `PLATFORM_DB_PATH` (например, `platform.db`)
- **Staging/Production**: PostgreSQL через `DATABASE_URL` (например, `postgresql://user:pass@postgres:5432/agent_platform`)
- См. [`.env.example`](.env.example) для полного списка переменных

**Billing:**
- `BILLING_ENABLED`, `STRIPE_ENABLED`, `KASPI_ENABLED`

**S3 Export:**
- `S3_EXPORT_ENABLED`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, etc.

**Port:**
- `APP_PORT` (по умолчанию `8000`)

**Observability:**
- `LOG_LEVEL`, `LOG_FORMAT`, `METRICS_ENABLED` (см. раздел Observability ниже)

### Database Migrations

**SQLite (Dev):**
- Используется по умолчанию для локальной разработки
- Миграции не требуются (схема создаётся автоматически через `ensure_schema()`)

**PostgreSQL (Staging/Production):**
- Управляется через **Alembic**
- Требует актуальной версии схемы перед запуском приложения
- `/ready` endpoint проверяет соответствие версии схемы

**PostgreSQL + psycopg v3:**
- Проект использует **psycopg v3** (`psycopg[binary]`), а не psycopg2
- В `DATABASE_URL` можно указывать `postgresql://...` или `postgres://...`
- Код автоматически нормализует URL для SQLAlchemy/Alembic: `postgresql://...` → `postgresql+psycopg://...`
- Нормализация выполняется один раз и безопасна для production
- **psycopg2 не используется и не нужен** в проекте

**Пример настройки:**
```bash
# DATABASE_URL можно указывать в стандартном формате
export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform
# или
export DATABASE_URL=postgres://postgres:password@postgres:5432/agent_platform

# Код автоматически нормализует для SQLAlchemy
# Внутри будет использоваться: postgresql+psycopg://...
```

**Команды миграций:**
```bash
make db-current      # Показать текущую версию
make db-history      # Показать историю миграций
make db-upgrade      # Применить миграции (upgrade to head)
make db-downgrade    # Откатить последнюю миграцию
make db-check        # Проверить, что revision == head (для CI/staging)
```

**Первоначальная настройка PostgreSQL:**
```bash
# 1. Установите DATABASE_URL (можно postgresql:// или postgres://)
export DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform

# 2. Примените миграции
make db-upgrade

# 3. Проверьте версию
make db-current

# 4. Проверьте готовность
curl http://localhost:8000/ready
# Должен вернуть: "database": "ok (postgresql)", "database_migration": "ok (revision: ...)"
```

См. [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) для подробностей о безопасном деплое с миграциями.

### Health Check

Приложение предоставляет health и readiness endpoints:
```bash
curl http://localhost:8000/health   # Проверка жизнеспособности процесса
curl http://localhost:8000/ready    # Проверка готовности (зависимости + schema version для PostgreSQL)
```

Docker healthcheck настроен автоматически в `docker-compose.yml`.

**Readiness для PostgreSQL:**
- Проверяет подключение к БД
- Проверяет, что версия схемы Alembic соответствует head
- Возвращает `503` если схема не актуальна

### Observability

Приложение включает базовую наблюдаемость для production:

**Логи:**
- Структурированные JSON логи (production) или pretty формат (development)
- Автоматическое добавление `request_id`, `tenant_id`, `event_id`
- Фильтрация секретов (Stripe keys, tokens, etc.)

**Request Correlation:**
- Каждый запрос получает `X-Request-ID` заголовок
- Request ID доступен в логах для трассировки

**Метрики Prometheus:**
- Endpoint `/metrics` (если `METRICS_ENABLED=1`)
- Метрики: HTTP requests, billing webhooks, recurring runs
- Tenant-safe: не включает tenant_id в labels

**Настройка:**
```env
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT=json         # json (production) или pretty (development)
METRICS_ENABLED=1       # 1 для включения, 0 для отключения
```

**Примеры:**
```bash
# Проверка метрик
curl http://localhost:8000/metrics

# Запрос с кастомным request_id
curl -H "X-Request-ID: my-request-123" http://localhost:8000/health
```

### Миграции и обновления

Проект использует SQLite для локальной разработки. При обновлении:
1. Остановите контейнеры: `docker-compose down`
2. Обновите код: `git pull`
3. Пересоберите образ: `docker-compose build`
4. Запустите: `docker-compose up -d`

⚠️ **Важно**: В production используйте внешнюю БД (PostgreSQL) и настройте бэкапы.

📚 **Подробная документация:**
- [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) — полное руководство по деплою

## S3 / MinIO экспорт

### Включение экспорта

Установите `S3_EXPORT_ENABLED=1` в `.env`. Если не установлено или `0`, экспорт отключён.

### Структура экспорта

```
{prefix}/{tenant_id}/
  ├── documents/
  │   └── {artifact_id}/
  │       └── {file_id}.pdf
  ├── invoices/
  │   └── {artifact_id}.json
  ├── payments/
  │   └── {artifact_id}.json
  └── events/
      └── {artifact_id}/
          └── {event_id}.json
```

### Условия экспорта

| Событие | Условие | Что экспортируется |
|---------|---------|-------------------|
| `artifact.created` | `kind="document"` | PDF файл |
| `document.extracted` | `kind="invoice"` | Invoice JSON |
| `payment.ready` | `kind="payment"` | Payment JSON |
| `payment.invalid` | `kind="payment"` | Payment JSON |
| Любое событие | Всегда | Event JSON |

### MinIO (локальная разработка)

```bash
# Запуск MinIO через docker-compose
docker-compose up -d

# Создание bucket
aws --endpoint-url=http://localhost:9000 s3 mb s3://agent-platform
```

## API Endpoints

### Billing

- `POST /api/v1/billing/webhook/stripe` — Stripe webhook
- `POST /api/v1/billing/webhook/kaspi` — Kaspi webhook
- `GET /api/v1/billing/portal?period=YYYY-MM` — Billing Portal (план, подписка, invoice, quota, upgrade/manage URLs)
- `GET /api/v1/billing/quota?period=YYYY-MM` — статус квот
- `GET /api/v1/billing/usage?period=YYYY-MM` — usage за период
- `GET /api/v1/billing/invoice?period=YYYY-MM` — invoice за период
- `POST /api/v1/billing/admin/reset-usage` — сброс usage (требует `BILLING_ADMIN_KEY`)

### Documents & Agents

- `POST /documents/upload` — загрузка документа
- `POST /agents/doc_agent/run` — запуск doc_agent
- `POST /api/v1/invoices/{invoice_id}/prepare-payment` — подготовка payment из invoice
- `GET /api/v1/artifacts/{artifact_id}` — получить артефакт
- `GET /api/v1/artifacts/{artifact_id}/events` — получить события артефакта

### Export

- `GET /api/v1/export/status` — статус экспорта (enabled, bucket, endpoint, prefix)

## Документация

📚 **Единая точка входа:** [`docs/README.md`](docs/README.md) — структурированный индекс всей документации проекта

### Архитектурные решения (ADR)

- [`docs/adr/README.md`](docs/adr/README.md) — индекс всех ADR
- [`docs/adr/0001-psycopg-v3-database-url-normalization.md`](docs/adr/0001-psycopg-v3-database-url-normalization.md) — решение о нормализации DATABASE_URL для psycopg v3

### Архитектура и рефакторинг

- [`REFACTORING_PLAN.md`](REFACTORING_PLAN.md) — план рефакторинга billing системы
- [`REFACTORING_SUMMARY.md`](REFACTORING_SUMMARY.md) — сводка рефакторинга
- [`REFACTORING_STATUS.md`](REFACTORING_STATUS.md) — текущий статус рефакторинга
- [`FINAL_ARCHITECTURE_STATUS.md`](FINAL_ARCHITECTURE_STATUS.md) — финальный архитектурный статус (production-ready)

### Git Hooks

- [`GIT_HOOKS_SECURITY.md`](GIT_HOOKS_SECURITY.md) — безопасность git hooks
- [`GIT_HOOKS_IMPROVEMENTS.md`](GIT_HOOKS_IMPROVEMENTS.md) — улучшения v2.1 (форматирование размера, сканирование секретов)
- [`FINAL_HOOKS_STATUS.md`](FINAL_HOOKS_STATUS.md) — финальный статус hooks
- [`GIT_HOOKS_QUICK_REFERENCE.md`](GIT_HOOKS_QUICK_REFERENCE.md) — быстрая справка
- [`GIT_AUTO_COMMIT_README.md`](GIT_AUTO_COMMIT_README.md) — автоматический commit/push
- [`QUICK_START.md`](QUICK_START.md) — быстрый старт

### Тестирование

- [`KASPI_RECURRING_TEST_SUMMARY.md`](KASPI_RECURRING_TEST_SUMMARY.md) — тестирование Kaspi recurring
- [`tests/TEST_KASPI_RECURRING.md`](tests/TEST_KASPI_RECURRING.md) — детали тестов Kaspi
- `tests/test_stripe_recurring.py` — тесты для Stripe recurring billing (6 сценариев: happy path, skip, cancel/downgrade, error handling)

## Безопасность

- ✅ **Tenant isolation**: все данные изолированы по `tenant_id`
- ✅ **Валидация tenant_id**: строгая валидация предотвращает cross-tenant доступ
- ✅ **Git hooks**: автоматическая блокировка коммитов с секретами
- ✅ **Секреты в env**: `S3_ACCESS_KEY`, `S3_SECRET_KEY`, API keys не возвращаются в API ответах
- ✅ **Идемпотентность webhooks**: защита от дублирования обработки

## Лицензия

MIT
