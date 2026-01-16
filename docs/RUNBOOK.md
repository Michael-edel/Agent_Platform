# Runbook: Agent Platform / CyberPlat

Практическое руководство по эксплуатации.

## Быстрый старт (Docker)

### Минимальные переменные окружения

```bash
# PostgreSQL (production/staging)
DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform

# Heroku-style URL также поддерживается
DATABASE_URL=postgres://postgres:password@postgres:5432/agent_platform

# SQLite (dev, по умолчанию)
PLATFORM_DB_PATH=platform.db
```

**Важно:** SQLAlchemy автоматически нормализует `postgresql://` и `postgres://` в `postgresql+psycopg://` для использования psycopg v3. Ничего дополнительно настраивать не нужно.

### Запуск

```bash
# Поднять всё
docker-compose up -d

# Проверить готовность
curl http://localhost:8000/ready

# Применить миграции (PostgreSQL)
docker exec -it agent-platform-app alembic upgrade head
```

## Observability

**METRICS_ENABLED** — переменная окружения для включения/выключения Prometheus метрик.
- По умолчанию: `true`
- В тестах: `METRICS_ENABLED=false` отключает метрики и позволяет запускать приложение без `prometheus_client`

## Диагностика

### Health Endpoints

```bash
# Liveness (процесс жив)
curl http://localhost:8000/health

# Readiness (зависимости готовы)
curl http://localhost:8000/ready

# Метрики Prometheus
curl http://localhost:8000/metrics
```

### Интерпретация /ready

**status: ok** — всё работает, миграции актуальны.

**status: degraded** — приложение работает, но есть проблемы:
- `database_migration: "warning: pending migrations..."` — нужно применить миграции
- `database_migration: "warning: database has no alembic revision"` — БД не инициализирована миграциями

**Пример ответа:**
```json
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql, driver: psycopg)",
    "database_driver": "psycopg",
    "database_migration": "ok (revision: abc123)",
    "billing_service": "ok"
  }
}
```

### Скрипт диагностики

```bash
# Локально
make doctor

# В контейнере
docker exec -it agent-platform-app python scripts/doctor.py

# JSON вывод (для CI)
python scripts/doctor.py --json
```

## Миграции

### Команды

```bash
# Текущая версия в БД
alembic current

# Доступные версии (heads)
alembic heads

# Применить все миграции
alembic upgrade head

# Откатить последнюю
alembic downgrade -1

# Проверка через Makefile
make db-check
```

### Важно

- `/ready` **НЕ выполняет** миграции, только проверяет соответствие версий
- При `pending migrations` приложение работает, но `/ready` возвращает `degraded`
- Миграции нужно применять явно перед/после деплоя

## Безопасность

### Что НЕ выводится в логи/ответы

- Полный DATABASE_URL с паролем
- Credentials пользователей
- API ключи (Stripe, Kaspi)

### Что выводится безопасно

- Схема БД (`postgresql`, `sqlite`)
- Драйвер (`psycopg`)
- Хост (без user:pass)
- Статус миграций

### Где смотреть логи

```bash
# Docker
docker-compose logs -f app

# Kubernetes
kubectl logs -f deployment/agent-platform

# Формат логов
LOG_FORMAT=json   # production (структурированные)
LOG_FORMAT=pretty # development
```

## Troubleshooting

### "No module named 'psycopg2'"

**Причина:** Старая проблема, исправлена нормализацией URL.

**Почему больше не случится:** 
- `postgresql://` автоматически преобразуется в `postgresql+psycopg://`
- Проект использует `psycopg v3`, не `psycopg2`
- Проверить драйвер: `curl /ready | jq .checks.database_driver`

### "database has no alembic revision"

**Причина:** БД существует, но таблица `alembic_version` пустая или отсутствует.

**Решение:**
```bash
# Инициализировать миграции
alembic upgrade head

# Или stamp текущую версию (если схема уже актуальна вручную)
alembic stamp head
```

### "pending migrations"

**Причина:** Версия схемы в БД отличается от head в коде.

**Решение:**
```bash
# Посмотреть разницу
alembic current
alembic heads

# Применить
alembic upgrade head
```

### /ready возвращает degraded

**Действия:**
1. Проверить `database_migration` в ответе
2. Если pending — применить миграции
3. Если connection error — проверить DATABASE_URL и доступность БД
4. После исправления: `curl /ready` должен вернуть `status: ok`

## Admin Panel (SQLAdmin)

### Включение

```bash
# В .env или docker-compose.yml
ADMIN_ENABLED=true  # default: false (production-safe)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password
ADMIN_SECRET_KEY=random-secret-for-sessions
```

### Доступ

URL: `http://localhost:8000/admin`

Войти с логином/паролем из ENV.

### Роли

| Роль | Описание |
|------|----------|
| `platform_admin` | Видит все tenant'ы (по умолчанию) |
| `tenant_admin` | Видит только свой tenant |

Для `tenant_admin` добавить:
```bash
ADMIN_ROLE=tenant_admin
ADMIN_TENANT_ID=your-tenant-id
```

**Tenant scoping:** Фильтрация применяется как к спискам данных, так и к total/pagination (count query). Это предотвращает утечку информации о количестве записей других tenant'ов.

### Admin Safe Actions (billing ops)

В админке есть **безопасные действия** для операционной поддержки биллинга (только `platform_admin`):

- **Refresh from provider** (Billing Job details):
  - Делает **только чтение** статуса платежа у провайдера (Stripe `PaymentIntent.retrieve(provider_ref)`).
  - **Не создаёт** новый PaymentIntent и **не инициирует** списания.
  - Обновляет `usage_invoices.payment_status` на основе текущего статуса у провайдера.

- **Mark job for retry** (Billing Job details):
  - **Не выполняет** retry и не вызывает провайдера.
  - Только переводит job в `pending_retry` и ставит `next_attempt_at=now`, чтобы процессор jobs мог подхватить позже.

Все действия записываются в **audit log** (`admin_audit_log`) и доступны в SQLAdmin как read-only view.

## Billing Jobs retry policy (production)

`billing_jobs` обрабатываются синхронным процессором `process_due_billing_jobs()` и поддерживают retry:

- **Статусы**: `pending`, `pending_retry`, `processing`, `succeeded`, `failed`
- **max_attempts**: ограничивает число попыток (по умолчанию 5)
- **Backoff**: exponential \(base * 2^(attempt-1)\) с cap до 1 часа + jitter \(\pm 10\%\)
- **next_attempt_at**: задаёт “когда можно пробовать снова”
- **Locking**:
  - Postgres: `FOR UPDATE SKIP LOCKED`
  - SQLite: best-effort через `locked_at/locked_by` (для тестов)

### Важно про безопасность

- Retry action в админке **не делает списаний** — только переводит job в `pending_retry`.
- Если у job уже есть `provider_ref`, процессор **не создаёт новый payment**, а делает best-effort reconcile/refresh (для Stripe — `PaymentIntent.retrieve`).

## Billing Worker

Отдельный процесс для обработки `billing_jobs` (не привязан к HTTP серверу). Делает polling due jobs и применяет retry policy.

### Запуск через docker-compose

```bash
docker-compose up --build
```

Сервис: `worker` (без портов).

### ENV

- `BILLING_WORKER_ENABLED` (default: `true`)
- `BILLING_WORKER_INTERVAL_SECONDS` (default: `5`)
- `BILLING_WORKER_BATCH_SIZE` (default: `50`)
- `BILLING_WORKER_ID` (default: hostname)
- `BILLING_WORKER_JITTER_SECONDS` (default: `0`)

### Graceful shutdown

При `docker-compose stop` worker получает `SIGTERM`, выставляет stop flag, **не запускает новые итерации**, дожидается окончания текущей и выходит `0`.

## Billing Worker Metrics (Prometheus)

Worker поднимает отдельный `/metrics` HTTP endpoint (по умолчанию `:9101`) и публикует метрики pipeline.

### Scrape config (пример)

```yaml
scrape_configs:
  - job_name: "billing-worker"
    static_configs:
      - targets: ["worker:9101"]
```

### Key metrics

- `billing_worker_up{worker_id}` (gauge)
- `billing_worker_iterations_total{worker_id}` (counter)
- `billing_worker_iteration_duration_seconds{worker_id}` (histogram)
- `billing_worker_last_success_timestamp{worker_id}` (gauge)
- `billing_jobs_claimed_total{worker_id}` (counter)
- `billing_jobs_processed_total{worker_id,result}` (counter)
- `billing_jobs_retry_scheduled_total{worker_id}` (counter)
- `billing_jobs_queue_depth{status}` (gauge)
- `billing_jobs_next_attempt_lag_seconds` (gauge)

### Suggested alerts (пример)

```promql
# A) Worker down
absent(billing_worker_up) OR billing_worker_up == 0

# B) Queue stuck
billing_jobs_queue_depth{status="pending_retry"} > 0

# C) High failure rate (5m)
rate(billing_jobs_processed_total{result="failed"}[5m]) > 0

# D) No progress for 5 minutes
time() - billing_worker_last_success_timestamp > 300
```

## Observability (Prometheus + Grafana)

### Запуск (docker-compose profile)

```bash
# demo (публикует порты 9090/3000 на localhost)
docker-compose -f docker-compose.yml -f docker-compose.observability.yml --profile observability up -d --build

# staging/prod (без публикации портов наружу)
docker-compose -f docker-compose.yml -f docker-compose.observability.nopublish.yml --profile observability up -d --build
```

### URLs (local demo)

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (default: `admin/admin` **только для локального демо**)

Grafana автоматически провиженит:
- datasource Prometheus (`http://prometheus:9090`)
- dashboard “Billing Worker / Jobs”
- dashboard “Billing SLA / Invoices v2” (v2.1: transitions + provider split)

### Production note

### Security notes

- **Grafana creds**: задавайте через env:
  - `GRAFANA_ADMIN_USER`
  - `GRAFANA_ADMIN_PASSWORD`
  Шаблон: `env.example` (копировать в `.env`).
- **Не публикуйте порты** `3000/9090` наружу в staging/prod: используйте `docker-compose.observability.nopublish.yml` и/или сетевые политики.
- **Prometheus retention**: `PROMETHEUS_RETENTION_TIME` (default `7d`), данные сохраняются в volume `prometheus-data`.

## Grafana Dashboard v2.1 (SLA pack)

Файл: `observability/grafana/dashboards/billing_sla_dashboard_v2.json`

Покрывает:
- invoice funnel (snapshot по `payment_status`)
- invoice `payment_status` transitions counters (для paid/failed за период, conversion)
- time-to-paid p95 (split by provider: Stripe/Kaspi/unknown)
- failure reasons summary по jobs
- provider refresh health

### Навигация и drill-down

## SLO: Time-to-paid ≤ 5m (burn-rate alerts)

### Definition

- **SLO target**: **99%** invoices should transition to `paid` within **5 minutes** (\(\le 300s\)) after `finalized_at`.
- **Scope**: `provider!="unknown"` (Stripe/Kaspi only; unknown provider is excluded to avoid noisy alerts).
- **Signals**:
  - Histogram: `usage_invoice_time_to_paid_seconds_bucket{provider}`
  - Recording rules: `billing:sli_time_to_paid_good_rate*` / `billing:sli_time_to_paid_total_rate*`

### Alert interpretation (multi-window burn-rate)

- **Fast burn (page)**: `BillingSLOTimeToPaidFastBurn` (5m & 1h windows)
  - Means: error budget is burning very fast right now; likely an incident.
- **Slow burn (ticket)**: `BillingSLOTimeToPaidSlowBurn` (30m & 6h windows)
  - Means: sustained degradation; investigate and remediate.

Low-traffic guard is applied via `billing:sli_time_to_paid_total_rate*` thresholds to avoid alerts on near-zero traffic.

### What to check (in order)

- **Queue**:
  - `billing_jobs_queue_depth{status="pending"}` and `billing_jobs_queue_depth{status="pending_retry"}`
  - `billing_jobs_next_attempt_lag_seconds` (overdue retry lag)
- **Failures**:
  - `topk(10, sum(rate(billing_jobs_failure_reasons_total[15m])) by (provider, error_code))`
  - `sum(rate(usage_invoices_payment_status_transitions_total{to="failed"}[10m])) by (provider)`
- **Worker health / latency**:
  - `billing_worker_up`
  - `time() - max(billing_worker_last_success_timestamp)`
  - `histogram_quantile(0.95, sum(rate(billing_worker_iteration_duration_seconds_bucket[5m])) by (le))`
- **Providers / webhooks**:
  - allowlist rejections / signature issues (Stripe/Kaspi webhook handlers + event logs)
  - webhook delivery lag / retries (if provider-side visibility is available)
- **DB health**:
  - DB latency, locks, connection saturation (especially if jobs are stuck in `processing`)

### Quick mitigations (ops)

- Increase worker throughput (if CPU/DB allows):
  - raise `BILLING_WORKER_BATCH_SIZE`
  - decrease `BILLING_WORKER_INTERVAL_SECONDS`
- If business allows: **temporarily switch default provider** via `BILLING_DEFAULT_PROVIDER` (or per-tenant override).
- For isolation in **staging only**: enable `BILLING_DRY_RUN=true` to validate pipeline without real payments.

## Onboarding нового production-tenant

Для B2B/Enterprise клиентов используется полный production onboarding процесс.

**Документация:**
- [PROD_ONBOARDING.md](PROD_ONBOARDING.md) — детальный playbook (prerequisites, billing cutover, go-live, rollback)
- [SECURITY_POSTURE.md](SECURITY_POSTURE.md) — безопасность (data isolation, webhooks, audit log)

**Ключевые шаги:**
1. Prerequisites: tenant создан, billing settings заполнены, webhook URLs переданы
2. Billing cutover: dry-run validation → production cutover (`BILLING_DRY_RUN=false`)
3. Go-live: Prometheus/Grafana healthy, SLO alerts green, first invoice paid
4. Monitoring: первые 7 дней ежедневные проверки dashboards и alerts

**Grafana dashboard:** `Billing SLA / Invoices v2` (v2.1) — `observability/grafana/dashboards/billing_sla_dashboard_v2.json`

## Pilot rollout (первые 30 дней)

Для первого production-клиента используется структурированный 30-дневный пилот.

**Документация:**
- [PILOT_ROLLOUT_PLAN.md](PILOT_ROLLOUT_PLAN.md) — детальный план (scope, таймлайн, метрики успеха, exit criteria)

**Ключевые этапы:**
1. **День 0 (Go-Live):** billing cutover, webhooks подтверждены, first invoice paid
2. **Дни 1-7 (Stabilization):** ежедневный мониторинг, проверка первых оплат, SLA dashboard
3. **Дни 8-21 (Normal operation):** еженедельные проверки, анализ usage growth, SLO compliance
4. **Дни 22-30 (Review & decision):** итоговая оценка, решение go-forward/pause/exit

**Метрики успеха:**
- % paid invoices ≥ 99%
- Time-to-paid p95 ≤ 5 минут
- Billing jobs failure rate ≤ 1%
- Нет ручных правок БД

## Tenant onboarding via Admin (v1)

Операционный onboarding делается через SQLAdmin (без отдельного фронта).

### 1) Создать tenant

- `Admin → Tenants → Create`
- Поля:
  - `id`: tenant_id (используется во всех таблицах как `tenant_id`)
  - `name`
  - `is_active`

### 2) Настроить billing provider для tenant (override)

- `Admin → Tenant Billing Settings → Create/Update`
- `default_provider`:
  - `null/empty` → используется глобальный `BILLING_DEFAULT_PROVIDER`
  - `kaspi|stripe` → override для tenant
- Нельзя выключить оба провайдера одновременно (`stripe_enabled=false` и `kaspi_enabled=false`).

### 3) Webhook URLs

На карточке tenant отображаются inbound billing webhook URLs:
- Stripe: `/api/v1/billing/webhook/stripe`
- Kaspi: `/api/v1/billing/webhook/kaspi`

Base URL берётся из `PUBLIC_BASE_URL`. Если не задан — используется `http://localhost:8000`.

### Audit log

Все изменения `Tenant` и `TenantBillingSettings` записываются в `admin_audit_log` (без секретов).

Dashboard содержит кликабельные карточки для быстрого перехода:
- Tenants → список tenant plans
- Subscriptions → список подписок
- Orders → список billing orders
- Webhook Events → список webhook событий

В списках колонка `tenant_id` содержит drill-down ссылки:
- <i class="fa-solid fa-receipt"></i> → Subscriptions этого tenant
- <i class="fa-solid fa-shopping-cart"></i> → Orders этого tenant
- <i class="fa-solid fa-bell"></i> → Webhook events этого tenant

Drill-down доступен из: Tenant Plans, Subscriptions, Orders, Webhook Events, Usage.

**Безопасность ссылок:** Все URL формируются через централизованные helpers (`app/admin/links.py`) с HTML escaping и URL encoding для защиты от XSS.

### Webhook Events: поиск и фильтры

Страница Billing Webhook Events поддерживает:

**Поиск по:**
- `event_id` — идентификатор события от провайдера
- `provider` — Stripe, Kaspi и др.
- `status` — pending, processed, failed
- `tenant_id`

**Фильтры:**
- Provider (dropdown)
- Status (dropdown)
- Tenant ID
- Received At (дата)

**Сортировка:** по умолчанию newest first (`received_at` desc).

**Безопасность:** Поле `raw_json` (сырой payload webhook) скрыто как в списке, так и в детальном просмотре. Поле `error` обрезается до 200 символов с HTML escaping.

### Orders: поиск и фильтры

Billing Orders Admin поддерживает:
- **Поиск**: по provider, status, external_order_id, tenant_id
- **Фильтры**: provider, status, tenant_id, created_at
- **Сортировка**: по created_at, paid_at, amount, status (по умолчанию newest first)
- **Детальный просмотр**: доступен через can_view_details

Модель BillingOrder не содержит секретных полей (raw payload, tokens), поэтому все колонки безопасны для отображения.

### Subscriptions: поиск и фильтры

Tenant Subscriptions Admin поддерживает:
- **Поиск**: по status, provider, tenant_id, provider_subscription_id
- **Фильтры**: provider, status, tenant_id, plan_id, created_at
- **Сортировка**: по current_period_start/end, created_at, updated_at (по умолчанию newest first)
- **Детальный просмотр**: доступен через can_view_details

Модель TenantSubscription не содержит секретных полей, все колонки безопасны для отображения.

### Admin Search

Unified Search (`/admin/search`) позволяет искать по ключевым идентификаторам:

| Сущность | Поля поиска |
|----------|-------------|
| Webhook Events | event_id, provider |
| Orders | external_order_id, provider |
| Subscriptions | provider_subscription_id, provider |

**Tenant scoping:**
- `platform_admin` — глобальный поиск
- `tenant_admin` — только в рамках своего tenant_id
- Без tenant_id при tenant_admin → пустые результаты (fail-closed)

Результаты ограничены 20 записями на секцию. Каждый результат имеет ссылку "Details" для перехода на детальную страницу записи.

### Dashboard: System Diagnostics

Dashboard показывает системную диагностику (без HTTP-запросов к /ready):

| Поле | Описание |
|------|----------|
| Readiness Status | `OK` / `Degraded` / `Unknown` |
| Database Driver | Драйвер SQLAlchemy (например `psycopg`) |
| Migrations Status | `Up to date` / `Pending` |

**Значения статусов:**
- **OK** — база данных доступна, миграции актуальны
- **Degraded** — есть pending migrations или проблемы с БД; приложение работает, но требует внимания
- **Unknown** — ошибка при проверке (см. поле Error)

При `Degraded` рекомендуется выполнить:
```bash
alembic upgrade head
```

### Dashboard: Quick Links

Блок Quick Links предоставляет быстрый доступ к часто используемым представлениям:

| Ссылка | Описание |
|--------|----------|
| Webhook Events (newest) | Все webhook события, новые первыми |
| Webhook Errors | Webhook события с `status=failed` (ошибочные) |
| Orders (newest) | Все заказы, новые первыми |
| Subscriptions | Все подписки |

Все ссылки соблюдают tenant scoping — tenant_admin увидит только данные своего tenant.

### Dashboard: Recent Errors (24h)

Секция показывает последние 10 ошибок за 24 часа:
- **Webhook Events**: события со статусом `failed`
- **Orders**: заказы со статусом `failed`

Данные tenant-scoped: tenant_admin видит только ошибки своего tenant. Поле `error` обрезается до 100 символов, `raw_json` не отображается.

## Tenant Admin API (v1)

Read-only self-service API для tenant'ов с per-tenant токенами.

### Аутентификация

| Header | Описание |
|--------|----------|
| `X-Tenant-ID` | ID tenant'а (обязателен) |
| `X-Tenant-Portal-Key` | Per-tenant токен (обязателен) |

**Получение токена:**
1. Войти в Admin Panel (`/admin`)
2. Перейти в "Rotate Token" (`/admin/rotate-token`)
3. Ввести `tenant_id` и нажать "Rotate Token"
4. Скопировать токен — он показывается только один раз

**Ротация/ревокация:**
- При ротации старый токен немедленно отзывается
- Новый токен генерируется и показывается один раз
- В БД хранится только хэш токена (SHA256)

### Endpoints

**GET /api/v1/tenant/subscription**
- Возвращает: plan_id, subscription_status, provider, current_period_end

**GET /api/v1/tenant/usage?period=YYYY-MM**
- Возвращает: агрегаты usage по метрикам за период (по умолчанию текущий месяц)

**GET /api/v1/tenant/status**
- Возвращает: webhooks_failed_24h, orders_failed_24h, last_error_at, status (ok/degraded/unknown)

**GET /api/v1/tenant/limits?period=YYYY-MM**
- Возвращает: plan_id, limits (metric/limit/used/remaining/utilization), notes
- Лимиты берутся из поля `quotas` в таблице `plans` (JSON)
- Формат quotas: `{"documents": 1000, "ocr_pages": 5000, ...}`

### Безопасность

- Каждый tenant имеет свой уникальный токен
- Токен показывается только при создании/ротации
- При DB ошибках — 503 (fail-closed)
- Неверный токен — 403

## Tenant Portal UI (v1)

Браузерный интерфейс для tenant'ов (server-rendered, read-only).

### Включение

**ENV**: `TENANT_PORTAL_SESSION_SECRET` — секрет для session cookies (обязателен).

Если не задан — все страницы `/tenant/*` вернут 503.

### URL

- `/tenant/login` — страница входа
- `/tenant/` — dashboard (статус, подписка, лимиты)
- `/tenant/usage` — usage по периодам (с колонками limit/remaining)
- `/tenant/subscription` — детали подписки
- `/tenant/help` — онбординг, контакты поддержки, download Support Bundle
- `/tenant/support-bundle.json` — скачать JSON bundle для поддержки

### Лимиты и предупреждения

На dashboard отображаются лимиты текущего плана:
- **OK**: использование < 80%
- **Warning**: использование >= 80% (желтый баннер)
- **Critical**: использование >= 100% (красный баннер)

### Логин

1. Tenant вводит `tenant_id` + `portal_key`
2. Portal key получается через Admin → Rotate Token
3. После успешного входа создаётся HttpOnly session cookie

### Support Bundle

Bundle содержит tenant-scoped метаданные для обращения в поддержку:
- Subscription и plan
- Status за 24h (ошибки)
- Limits и remaining
- Recent events (только метаданные: id, provider, status, timestamp)

**Не содержит:** токены, пароли, DSN, raw_json, payload, headers.

### ENV для поддержки

| Переменная | Описание |
|------------|----------|
| `TENANT_SUPPORT_EMAIL` | Email поддержки (опционально) |
| `TENANT_SUPPORT_URL` | URL портала поддержки (опционально) |
| `APP_VERSION` / `GIT_SHA` | Версия приложения для bundle (опционально) |

## Plan Limits Enforcement

Платформа применяет soft enforcement лимитов по плану.

### Enforced метрики

- `documents` — количество загруженных документов
- `ocr_pages` — количество OCR страниц
- `webhooks` — количество webhook событий

### Поведение

| Состояние | Действие |
|-----------|----------|
| < 80% лимита | Операция выполняется |
| >= 80% лимита | Warning в UI |
| >= 100% лимита | Операция отклоняется |

### Где применяется

- Webhook ingestion (`/api/v1/billing/webhook/stripe`, `/api/v1/billing/webhook/kaspi`)
- Order creation
- Document upload events

### HTTP ответы при превышении

При превышении лимита операция возвращает:

```json
{
  "error": "plan_limit_exceeded",
  "metric": "documents",
  "limit": 1000,
  "used": 1000,
  "tenant_id": "t1"
}
```

HTTP код: `429 Too Many Requests`

### Что сохраняется в БД

При отклонении из-за лимита:
- `BillingWebhookEvent.status = "rejected"` или `"failed"`
- `BillingWebhookEvent.error = "plan_limit_exceeded:documents"`
- Без `raw_json` / `payload`

### Idempotency

Повторный запрос с тем же `event_id`:
- Возвращает тот же HTTP 429
- Не создаёт дублей записей
- Не увеличивает usage

### Что видит клиент

- Dashboard показывает critical баннер
- Help страница объясняет ситуацию
- Рекомендация: upgrade плана или обратиться в поддержку

### Безопасность

- Cookie: `HttpOnly`, `SameSite=Lax`
- В prod (`ENV=prod`): `Secure` (HTTPS only)
- Токен не передаётся через URL
- Токен не отображается после логина (только prefix)

При переходе по drill-down ссылкам tenant scoping сохраняется:
- `platform_admin` видит все записи выбранного tenant
- `tenant_admin` видит только свой tenant (scoping применяется серверно)

### Dashboard

Страница `/admin/` (первый пункт меню) показывает key metrics:
- **Tenants** — количество tenant'ов в системе
- **Subscriptions** — количество подписок
- **Orders** — количество billing orders
- **Webhook Events** — количество webhook событий

Scoping по ролям:
- `platform_admin` — видит глобальную статистику
- `tenant_admin` — видит только данные своего tenant

### Возможности

- **Plans / Billing Plans** — CRUD тарифных планов
- **Tenant Subscriptions** — просмотр подписок (read-only)
- **Billing Orders / Events** — просмотр платежей и webhook'ов (read-only)
- **Webhooks / Deliveries** — просмотр конфигурации webhooks (read-only)

### Безопасность

- Admin panel отключена по умолчанию (`ADMIN_ENABLED=false`)
- Без `ADMIN_PASSWORD` логин невозможен
- Доступ deny-by-default: если роль не задана в сессии, доступ запрещён
- Секреты (webhook secrets, tokens) скрыты в UI
- Платёжные данные доступны только для просмотра
