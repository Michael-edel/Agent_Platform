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
- **Invoice**: вычисляется on-demand из `billing_usage` (агрегация по метрикам за период YYYY-MM). Invoice не является persisted entity и не хранится в отдельной таблице.

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
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — как внести вклад в проект
- [`SECURITY.md`](SECURITY.md) — политика безопасности
- [`GIT_HOOKS_SECURITY.md`](GIT_HOOKS_SECURITY.md) — безопасность hooks
- [`GIT_HOOKS_IMPROVEMENTS.md`](GIT_HOOKS_IMPROVEMENTS.md) — улучшения v2.1
- [`FINAL_HOOKS_STATUS.md`](FINAL_HOOKS_STATUS.md) — финальный статус

> Before opening a PR, please read [CONTRIBUTING.md](CONTRIBUTING.md).

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

### Тесты в Docker

Быстрый запуск тестов в контейнере (PostgreSQL поднимется как зависимость):

```bash
docker compose --profile test run --rm app-test
```

Если контейнер `app` уже запущен, можно выполнить тесты внутри него (если образ собран с dev-зависимостями):

```bash
docker exec -it agent-platform-app python -m pytest -q
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

#### Docker: миграции и /ready degraded

Если Postgres **healthy**, приложение стартует, но `/ready` показывает **degraded** — часто причина в том, что Alembic миграции ещё не применены.

**Применить миграции в контейнере:**

```bash
docker exec -it agent-platform-app alembic upgrade head
```

**Проверить readiness:**

```bash
curl http://127.0.0.1:8000/ready
```

**Сбросить окружение в dev, если миграции применились частично (⚠️ удалит данные):**

```bash
docker compose down -v
```

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
- Проверяет подключение к БД через `psycopg.connect()` (оригинальный DATABASE_URL)
- Создаёт SQLAlchemy engine с нормализованным URL (`postgresql+psycopg://`)
- Проверяет миграции через единую функцию `check_database_migration(engine)`
- Отображает используемый драйвер в ответе (`database_driver`)

**Пример ответа `/ready` (PostgreSQL):**
```json
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql, driver: psycopg)",
    "database_driver": "psycopg",
    "database_migration": "ok (revision: abc123def456)",
    "billing_service": "ok",
    "entitlement_service": "ok"
  }
}
```

**Важно о DATABASE_URL:**
- `postgresql://...` и `postgres://...` (Heroku style) автоматически нормализуются
- `psycopg.connect()` использует оригинальный URL (без `+psycopg`)
- SQLAlchemy/Alembic используют нормализованный URL (`postgresql+psycopg://`)
- Если указан явный драйвер (`postgresql+asyncpg://`), он не модифицируется

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

## Product/UI Layer

Product/UI layer предоставляет REST API для работы с документами и инвойсами через UI, с автоматическим управлением состояниями (`artifact_states`) и экспортом.

### Архитектура

**Таблицы:**
- `artifact_states` — UI состояния артефактов (uploaded, processing, completed, confirmed, exported, error)
- `exports` — записи об экспортах инвойсов (Excel, JSON, 1C, etc.)

**Автоматическое создание состояний:**
- При загрузке документа:
  - **Новый product endpoint** (рекомендуется): `POST /api/v1/documents/upload`
  - **Legacy endpoint** (совместимость): `POST /documents/upload`
  → в обоих случаях создаётся **один и тот же** `artifact_id`, и в product таблице `artifact_states` появляется запись с `ui_status="uploaded"`.
- При запуске OCR (`POST /api/v1/documents/{document_id}/run-ocr`) → создаётся `artifact_state` для invoice с `ui_status="pending"`, `source_artifact_id=document_id`
- При событии `document.extracted` → обновляется state для document (`ui_status="extracted"`) и invoice (`source_artifact_id` заполняется)

**Экспорт:**
- Создаётся запись в `exports` (status="pending" → "processing" → "completed"/"failed")
- Генерируется файл экспорта (синхронно, MVP)
- Обновляется `artifact_state`: `ui_status="exported"`, `export_target`, `exported_at`
  - API **возвращает `file_id`** (и `download_url`), чтобы файл можно было скачать через `/files/{file_id}`.

### Endpoints

**Документы:**
- `GET /api/v1/documents?ui_status=...` — список документов (с фильтрацией по статусу)
- `GET /api/v1/documents/{document_id}` — детали документа
- `POST /api/v1/documents/{document_id}/run-ocr` — запуск OCR на документе

**Инвойсы:**
- `GET /api/v1/invoices?ui_status=...` — список инвойсов (с фильтрацией по статусу)
- `GET /api/v1/invoices/{invoice_id}` — детали инвойса
- `POST /api/v1/invoices/{invoice_id}/confirm` — подтверждение инвойса (idempotent)
- `POST /api/v1/invoices/{invoice_id}/export` — экспорт инвойса (Excel/JSON/etc.)

**Файлы:**
- `GET /files/{file_id}` — получение файла по file_id

**Заголовки:**
- `X-Tenant-ID` — обязательный заголовок для всех endpoints (multi-tenant изоляция)

### Примеры использования

**1. Загрузка документа (legacy, совместимость):**
```bash
curl -X POST http://localhost:8000/documents/upload \
  -H "X-Tenant-ID: tenant-123" \
  -F "file=@invoice.pdf"
```

**2. Запуск OCR (product):**
```bash
curl -X POST http://localhost:8000/api/v1/documents/{document_id}/run-ocr \
  -H "X-Tenant-ID: tenant-123"
```

**2b. Запуск OCR (legacy doc_agent, совместимость):**
```bash
curl -X POST http://localhost:8000/agents/doc_agent/run \
  -H "X-Tenant-ID: tenant-123" \
  -H "Content-Type: application/json" \
  -d '{"artifact_id":"{document_id}"}'
```

**3. Получение списка документов:**
```bash
curl http://localhost:8000/api/v1/documents?ui_status=uploaded \
  -H "X-Tenant-ID: tenant-123"
```

**4. Получение списка инвойсов:**
```bash
curl http://localhost:8000/api/v1/invoices?ui_status=pending \
  -H "X-Tenant-ID: tenant-123"
```

**5. Подтверждение инвойса:**
```bash
curl -X POST http://localhost:8000/api/v1/invoices/{invoice_id}/confirm \
  -H "X-Tenant-ID: tenant-123"
```

**6. Экспорт инвойса:**
```bash
curl -X POST http://localhost:8000/api/v1/invoices/{invoice_id}/export \
  -H "X-Tenant-ID: tenant-123" \
  -H "Content-Type: application/json" \
  -d '{"export_type": "excel"}'
```

**7. Получение файла (используйте `file_id` из ответа export):**
```bash
curl http://localhost:8000/files/{file_id} \
  -H "X-Tenant-ID: tenant-123" \
  -o exported_invoice.xlsx
```

### Полный сценарий (PowerShell)

```powershell
$tenant = "tenant-123"

# 1) Upload (legacy)
$upload = curl.exe -sS -X POST "http://127.0.0.1:8000/documents/upload" `
  -H "X-Tenant-ID: $tenant" `
  -F "file=@invoice.pdf" | ConvertFrom-Json

$docId = $upload.artifact_id

# 2) OCR (legacy doc_agent)
$ocr = curl.exe -sS -X POST "http://127.0.0.1:8000/agents/doc_agent/run" `
  -H "X-Tenant-ID: $tenant" `
  -H "Content-Type: application/json" `
  -d ("{`"artifact_id`":`"$docId`"}") | ConvertFrom-Json

$invId = $ocr.artifact_id

# 3) Проверка, что документ виден в Product/UI API
curl.exe -sS "http://127.0.0.1:8000/api/v1/documents/$docId" -H "X-Tenant-ID: $tenant" | Out-Null

# 4) Confirm invoice (idempotent)
curl.exe -sS -X POST "http://127.0.0.1:8000/api/v1/invoices/$invId/confirm" -H "X-Tenant-ID: $tenant" | Out-Null

# 5) Export invoice → получаем file_id
$exp = curl.exe -sS -X POST "http://127.0.0.1:8000/api/v1/invoices/$invId/export" `
  -H "X-Tenant-ID: $tenant" `
  -H "Content-Type: application/json" `
  -d "{`"export_type`":`"json`"}" | ConvertFrom-Json

$fileId = $exp.file_id

# 6) Download export
curl.exe -sS -L "http://127.0.0.1:8000/files/$fileId" -H "X-Tenant-ID: $tenant" -o export.json
```

### Локальный запуск

**Development (SQLite):**
```bash
# 1. Убедитесь, что миграции применены
make db-upgrade

# 2. Запустите сервер
make run
# или
uvicorn app.main:app --reload

# 3. Проверьте готовность
curl http://localhost:8000/ready
```

**Staging (PostgreSQL):**
```bash
# 1. Установите DATABASE_URL
export DATABASE_URL=postgresql://postgres:password@localhost:5432/agent_platform

# 2. Примените миграции
make db-upgrade

# 3. Проверьте версию схемы
make db-current

# 4. Запустите сервер
make run
```

### Проверка миграций

```bash
# Проверить текущую версию
make db-current

# Применить миграции
make db-upgrade

# Проверить, что схема актуальна (для CI/staging)
make db-check
```

**Важно:** Все таблицы (`artifact_states`, `exports`) создаются через Alembic миграции. Никакого auto-create в runtime.

### Billing integration

Product layer интегрирован с billing системой:
- Usage считается из событий (`artifact.created`, `document.extracted`)
- Квоты проверяются перед запуском OCR
- Invoice вычисляется on-demand из usage за период

**Пример получения billing информации:**
```bash
# Получить invoice за период
curl http://localhost:8000/api/v1/billing/invoice?period=2026-01 \
  -H "X-Tenant-ID: tenant-123"

# Получить quota status
curl http://localhost:8000/api/v1/billing/quota?period=2026-01 \
  -H "X-Tenant-ID: tenant-123"

# Получить billing portal (план, подписка, invoice, quota, upgrade URLs)
curl http://localhost:8000/api/v1/billing/portal?period=2026-01 \
  -H "X-Tenant-ID: tenant-123"
```

## API Endpoints

### Billing

- `POST /api/v1/billing/webhook/stripe` — Stripe webhook
- `POST /api/v1/billing/webhook/kaspi` — Kaspi webhook
- `GET /api/v1/billing/portal?period=YYYY-MM` — Billing Portal (план, подписка, invoice, quota, upgrade/manage URLs)
- `GET /api/v1/billing/quota?period=YYYY-MM` — статус квот
- `GET /api/v1/billing/usage?period=YYYY-MM` — usage за период
- `GET /api/v1/billing/invoice?period=YYYY-MM` — вычисляемый invoice за период (агрегация usage по метрикам)
- `POST /api/v1/billing/admin/reset-usage` — сброс usage (требует `BILLING_ADMIN_KEY`)

### Documents & Agents

- `POST /documents/upload` — загрузка документа
- `POST /agents/doc_agent/run` — запуск doc_agent
- `POST /api/v1/invoices/{invoice_id}/prepare-payment` — подготовка payment из invoice
- `GET /api/v1/artifacts/{artifact_id}` — получить артефакт
- `GET /api/v1/artifacts/{artifact_id}/events` — получить события артефакта

### Export

- `GET /api/v1/export/status` — статус экспорта (enabled, bucket, endpoint, prefix)

### Email Ingestion

- `POST /api/v1/ingest/email` — email ingestion webhook (SendGrid/Mailgun/SES)

**Email Ingestion** позволяет клиентам отправлять PDF счета по email и автоматически создавать document artifacts.

**Как это работает:**
1. Настройте email provider (SendGrid/Mailgun/SES) для отправки webhook на `POST /api/v1/ingest/email`
2. Создайте email адрес для каждого tenant: `invoices+tenant-1@yourapp.ai`
3. Клиент отправляет email с PDF вложением на этот адрес
4. Система автоматически:
   - Определяет tenant_id из email адреса (часть после `+` и до `@`)
   - Сохраняет PDF файл
   - Создаёт document artifact с `source="email"`
   - Создаёт artifact_state с `ui_status="uploaded"`
   - Эмитит события `email.received` и `artifact.created`

**Формат email адреса:**
- `invoices+tenant-1@yourapp.ai` → tenant_id = `tenant-1`
- `invoices+tenant-2@yourapp.ai` → tenant_id = `tenant-2`

**Ограничения:**
- Только PDF вложения (application/pdf)
- Максимальный размер вложения: 10MB
- Если tenant_id не определён → email отклоняется с событием `email.ingest.failed`
- OCR не запускается автоматически (только через UI или отдельную настройку)

**Webhook Payload Format (provider-agnostic):**
```json
{
  "from": "sender@example.com",
  "to": "invoices+tenant-1@yourapp.ai",
  "subject": "Invoice #123",
  "attachments": [
    {
      "filename": "invoice.pdf",
      "content_type": "application/pdf",
      "content": "base64_encoded_pdf_content",
      "size": 12345
    }
  ]
}
```

**Настройка у email provider:**

**SendGrid Inbound Parse:**
1. Перейдите в Settings → Inbound Parse
2. Добавьте домен и настройте webhook URL: `https://yourapp.com/api/v1/ingest/email`
3. Укажите POST destination

**Mailgun Inbound:**
1. Перейдите в Routes → Inbound
2. Создайте route с webhook URL: `https://yourapp.com/api/v1/ingest/email`
3. Настройте фильтры (опционально)

**AWS SES:**
1. Настройте SNS topic для входящих email
2. Создайте HTTP(S) subscription на `https://yourapp.com/api/v1/ingest/email`
3. Обработайте SNS message format (parser поддерживает)

**События:**
- `email.received` — email получен (payload: from, to, subject, attachments_count, processed_count)
- `email.ingest.failed` — не удалось обработать email (payload: from, to, subject, error)

### Email Auto-OCR

После успешного email ingestion можно автоматически запускать OCR для созданных document artifacts.

**Включение:**
```bash
EMAIL_AUTO_OCR_ENABLED=1
EMAIL_AUTO_OCR_MAX_RETRIES=5
EMAIL_AUTO_OCR_RETRY_BASE_SECONDS=10
EMAIL_AUTO_OCR_RETRY_MAX_SECONDS=600
```

**Как это работает:**
1. После успешного email ingest создаётся `email_ocr_job` в таблице `email_ocr_jobs`
2. Job имеет `idempotency_key` (SHA256 от tenant_id + email metadata + attachment SHA256)
3. Повторные webhook с тем же email/attachment не создают дубликаты (идемпотентность)
4. Endpoint `POST /api/v1/ingest/email/ocr/dispatch` обрабатывает jobs из очереди
5. При успешном OCR создаётся invoice artifact и обновляются states

**Idempotency:**
- Idempotency key вычисляется детерминированно из:
  - tenant_id
  - email_from, email_to, email_subject
  - attachment_filename, attachment_size
  - attachment_content_sha256 (SHA256 от PDF bytes после base64 decode)
- Одинаковый email/attachment → одинаковый key → один job
- Если job уже `done`, повторный webhook не создаёт новый job

**Retry механизм:**
- Exponential backoff с jitter: `base_seconds * 2^(attempts-1) + jitter`
- Ограничено `retry_max_seconds`
- После `max_retries` попыток job становится `dead`

**Статусы jobs:**
- `queued` — готов к обработке
- `processing` — обрабатывается
- `done` — успешно завершён (invoice создан)
- `failed` — ошибка, запланирован retry
- `dead` — превышен max_retries

**Dispatch endpoint:**
```bash
# Обработать до 10 jobs (prod режим с admin key)
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
  -H "X-Admin-Key: your-admin-key"

# Dev режим (если ADMIN_API_KEY не задан)
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

**Admin Authentication:**
- Если `ADMIN_API_KEY` задан (prod), endpoint требует заголовок `X-Admin-Key`
- Если `ADMIN_API_KEY` не задан (dev), endpoint доступен без ключа

**Настройка cron:**
```bash
# Каждую минуту обрабатывать jobs
*/1 * * * * curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

**События:**
- `email.ocr.queued` — job создан (payload: job_id, document_artifact_id)
- `email.ocr.started` — обработка начата (payload: job_id, attempts)
- `email.ocr.completed` — OCR завершён (payload: job_id, document_artifact_id, invoice_artifact_id, attempts)
- `email.ocr.failed` — ошибка (payload: job_id, attempts, error, status: "failed"|"dead", next_run_at)
- `email.ocr.recovered` — job восстановлен из stuck (payload: job_id, attempts, reason="timeout")
- `email.ocr.dead` — job стал dead (payload: job_id, reason="timeout")

**Обновление states:**
- Document: `ui_status="extracted"` (после успешного OCR)
- Invoice: `ui_status="pending"`, `source_artifact_id=document_artifact_id`

**Production Features:**
- **Admin Authentication:** Endpoint защищён `X-Admin-Key` (если `ADMIN_API_KEY` задан)
- **Recovery Stuck Jobs:** Автоматическое восстановление залипших processing jobs (timeout: `EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS`)
- **Concurrency Limit:** Ограничение параллельных jobs (`EMAIL_OCR_MAX_CONCURRENT_JOBS`, default: 3)
- **Structured Logging:** Логи с job_id, tenant_id, duration для observability

**Environment Variables:**
- `ADMIN_API_KEY` - Admin API key (обязательно в prod)
- `EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS=900` - Timeout для recovery (default: 900)
- `EMAIL_OCR_MAX_CONCURRENT_JOBS=3` - Максимальное количество параллельных jobs (default: 3)

**Подробная документация:** См. `EMAIL_AUTO_OCR_OPERATIONS.md`

### Inbox (Email Status)

**Endpoint:** `GET /api/v1/inbox/emails`

Показывает список последних входящих писем/вложений и их статусы обработки (queued/processing/done/failed/dead).

**Query параметры:**
- `limit` (int, default: 50) - максимальное количество записей
- `cursor` (string, optional) - cursor для pagination (формат: "timestamp|job-id")

**Response:**
```json
{
  "items": [
    {
      "received_at": "2026-01-14T23:00:00",
      "from_email": "sender@example.com",
      "to_email": "invoices+tenant-1@yourapp.ai",
      "subject": "Invoice #123",
      "attachment_filename": "invoice.pdf",
      "attachment_size": 12345,
      "document_artifact_id": "doc-id",
      "job_id": "job-id",
      "job_status": "done",
      "attempts": 1,
      "next_run_at": null,
      "invoice_artifact_id": "invoice-id",
      "error": null
    }
  ],
  "cursor": "2026-01-14T23:00:00|job-id"
}
```

**Статусы:**
- `queued` - готов к обработке
- `processing` - обрабатывается
- `done` - успешно завершён (invoice создан)
- `failed` - ошибка, запланирован retry
- `dead` - превышен max_retries

**Использование:**
```bash
curl http://127.0.0.1:8000/api/v1/inbox/emails?limit=50 \
  -H "X-Tenant-ID: tenant-1"
```

**Frontend:**
- Страница `/app/inbox` показывает таблицу с последними email/вложениями
- Status badges с цветами (queued=серый, processing=синий, done=зелёный, failed=жёлтый, dead=красный)
- Ссылки на Document и Invoice (если созданы)
- Кнопка Refresh для обновления списка

### Webhooks (Outgoing)

**Endpoints:**
- `GET /api/v1/webhooks` - Список webhooks (tenant-scoped)
- `POST /api/v1/webhooks` - Создать webhook
- `DELETE /api/v1/webhooks/{id}` - Удалить webhook
- `POST /api/v1/webhooks/{id}/rotate-secret` - Обновить secret
- `POST /api/v1/webhooks/dispatch` - Dispatch deliveries (admin-only)

**Supported Events:**
- `invoice.ready` - Invoice готов
- `invoice.failed` - Invoice не удалось создать
- `email.ocr.completed` - Email OCR завершён
- `email.ocr.dead` - Email OCR провалился
- `invoice.confirmed` - Invoice подтверждён

**Features:**
- HMAC-SHA256 подпись для безопасности
- Safe payload (без raw PDF/OCR JSON)
- Retry с exponential backoff
- Tenant isolation

**Подробная документация:** См. `WEBHOOKS.md`

### Billing Enforcement (Paywall)

**Feature Flags:**
- `BILLING_ENFORCEMENT_ENABLED=0|1` (default: 0) - Включить/выключить enforcement
- `BILLING_ENFORCEMENT_MODE=block|warn` (default: block) - Режим работы
- `BILLING_PERIOD_SOURCE=now` (default: now) - Источник периода

**Blocked Operations:**
- `POST /documents/upload` → метрика: `document_upload` (units=1)
- `POST /api/v1/documents/{id}/run-ocr` → метрики: `invoice_extracted` (1), `page_processed` (1)
- `POST /api/v1/ingest/email` → если auto-OCR включен
- Email auto-OCR jobs dispatch → перед запуском OCR

**Error Response (402 Payment Required):**
```json
{
  "detail": {
    "detail": "Квота превышена",
    "metric": "invoice_extracted",
    "period": "2026-01",
    "used_units": 10.0,
    "monthly_quota": 10,
    "operation": "run_ocr",
    "upgrade_url": null
  }
}
```

**Events:**
- `billing.quota.exceeded` - Квота превышена (payload: metric, period, used_units, monthly_quota, operation)

**Webhooks:**
- `quota.exceeded` - Маппинг из `billing.quota.exceeded`

**Подробная документация:** См. `BILLING_ENFORCEMENT.md`

### Billing Plans and Trial

**Plans:**
- `trial` - 14 дней бесплатно (квоты: 20/10/50)
- `pro` - $29/месяц (квоты: 100/50/500)
- `enterprise` - $99/месяц (unlimited)

**Features:**
- Автоматическое назначение trial при первом использовании tenant
- Trial expiration и блокировка операций (HTTP 402)
- Upgrade flow (MVP без реальных платежей)
- Интеграция с enforcement (квоты из планов)

**Endpoints:**
- `POST /api/v1/billing/upgrade` - Обновить план tenant
- `GET /api/v1/billing/portal` - Получить информацию о плане и trial статусе

**Events:**
- `billing.trial.expired` - Trial истёк
- `billing.plan.upgraded` - План обновлён

**Webhooks:**
- `trial.expired` - Маппинг из `billing.trial.expired`
- `plan.upgraded` - Маппинг из `billing.plan.upgraded`

**Подробная документация:** См. `BILLING_PLANS_AND_TRIAL.md`

### Оплата через Kaspi → активация плана (MVP)

Коммерческий поток:
- клиент получает `checkout_url` через `POST /api/v1/billing/checkout/kaspi`
- после оплаты Kaspi присылает webhook на `POST /api/v1/billing/webhook/kaspi`
- система **идемпотентно** переключает `tenant_plans.plan_id` на оплаченный план (`pro|enterprise`)
- enforcement начинает использовать квоты из `plans`

**Kaspi Orders linkage:**
- создаётся таблица `kaspi_orders` для связи `kaspi_order_id` → `tenant_id` + `plan_id`

**Events:**
- `billing.kaspi.checkout.created`
- `billing.plan.upgraded` → outgoing webhook `plan.upgraded`
- `billing.payment.failed` → outgoing webhook `payment.failed`

**Docs:** См. `BILLING_KASPI_UPGRADE_FLOW.md`

### Kaspi subscription (recurring, production-like MVP)

- Платные планы (`pro/enterprise`) имеют **период**: `tenant_plans.expires_at = now + BILLING_PERIOD_DAYS`.
- Cron endpoint `POST /api/v1/billing/cron/charge-kaspi`:
  - продлевает `expires_at` при успешном списании
  - при ошибках переводит в `past_due`, после `SUBSCRIPTION_MAX_FAILED_CHARGES` → `canceled` и делает downgrade
- Enforcement блокирует платные операции при `past_due/canceled` (HTTP 402).

**Docs:** См. `BILLING_KASPI_SUBSCRIPTION.md`

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
