# Agent SKU

## Обзор

Agent SKU (Stock Keeping Unit) — это продуктовая единица агента в каталоге платформы.
Представляет собой описание агента как продаваемого товара, без логики выполнения.

## Назначение

- **Каталог агентов**: Централизованное хранение информации об агентах
- **Pricing**: Связь с моделью ценообразования
- **Admin**: Управление через SQLAdmin панель
- **Enablement**: Основа для подключения агентов к tenant'ам (будущий функционал)

## Модель данных

### Таблица: `agent_skus`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | Primary key |
| `code` | string | Уникальный идентификатор (e.g., `sales_assistant`) |
| `name` | string | Отображаемое имя |
| `description` | text | Описание агента (nullable) |
| `status` | enum | `active`, `deprecated`, `disabled` |
| `pricing_model` | string | `subscription`, `usage_based` |
| `created_at` | ISO timestamp | Дата создания |
| `updated_at` | ISO timestamp | Дата обновления |

### Ограничения

- `code` уникален (unique constraint)
- `code` индексирован для быстрого поиска

## Статусы

| Статус | Описание |
|--------|----------|
| `active` | Агент доступен для подключения |
| `deprecated` | Агент устарел, не рекомендуется для новых подключений |
| `disabled` | Агент отключён, недоступен |

## Pricing Models

| Модель | Описание |
|--------|----------|
| `subscription` | Фиксированная подписка |
| `usage_based` | Оплата по использованию |

## Admin Panel

Доступ через `/admin` → "Agent SKUs":

- **Просмотр списка**: code, name, status, pricing_model, created_at
- **Создание**: Добавление нового агента
- **Редактирование**: Изменение полей
- **Удаление**: Запрещено (используйте `status=disabled`)

**Доступ**: только `platform_admin`

## Важно

⚠️ **Execution НЕ реализован**

Agent SKU — это только продуктовый каталог. На данном этапе:

- Нет API для выполнения агентов
- Нет связи с tenant'ами (TenantAgent)
- Нет интеграции с billing/limits

Эти функции будут добавлены в следующих итерациях.

## Миграция

```bash
# Применить
alembic upgrade head

# Откатить
alembic downgrade -1
```

---

## TenantAgent (подключение к tenant)

### Обзор

TenantAgent — это связь между tenant'ом и AgentSKU. Определяет, какие агенты доступны конкретному tenant'у.

### Таблица: `tenant_agents`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK на tenant |
| `agent_sku_id` | UUID | FK на agent_skus |
| `status` | enum | `enabled`, `disabled`, `suspended` |
| `activated_at` | timestamp | Когда агент был включён (nullable) |
| `disabled_at` | timestamp | Когда агент был отключён (nullable) |
| `created_at` | timestamp | Дата создания |
| `updated_at` | timestamp | Дата обновления |

### Ограничения

- Уникальная пара `(tenant_id, agent_sku_id)`
- FK на `agent_skus.id`

### Статусы

| Статус | Описание |
|--------|----------|
| `enabled` | Агент доступен для вызова |
| `disabled` | Отключён вручную |
| `suspended` | Отключён по политике (неоплата и т.п.) |

### Guards (проверка доступа)

Модуль `app/agents/guards.py` предоставляет функцию:

```python
from app.agents.guards import assert_agent_enabled, AgentNotFoundError, AgentNotEnabledError

# Проверка доступа
try:
    tenant_agent = assert_agent_enabled(session, tenant_id, "sales_assistant")
except AgentNotFoundError:
    # Агент не существует
except AgentNotEnabledError:
    # Агент не включён для tenant'а
```

### Admin Panel

Доступ через `/admin` → "Tenant Agents":

- **Просмотр списка**: tenant_id, agent_sku_id, status, activated_at, disabled_at
- **Создание**: Подключение агента к tenant'у
- **Редактирование**: Изменение статуса
- **Удаление**: Запрещено (используйте `status=disabled`)

**Доступ**: только `platform_admin`

### Важно

⚠️ **Execution НЕ реализован**

На данном этапе guards готовят проверку доступа для будущего Execution API.
Сам вызов агентов будет добавлен в следующих итерациях.

---

## Execution API v1

### Обзор

Execution API позволяет запускать агентов и отслеживать статус выполнения.

⚠️ **Текущая версия**: синхронная заглушка (echo). Оркестрация и LLM будут добавлены позже.

### Эндпоинты

#### POST /api/v1/agents/{agent_code}/execute

Запуск агента.

**Headers:**
- `X-Tenant-ID`: UUID tenant'а (обязательно)

**Request:**
```json
{
  "input": { ... },
  "idempotency_key": "unique-key-123"
}
```

**Response (200):**
```json
{
  "execution_id": "uuid",
  "status": "completed",
  "result": {"ok": true, "echo": {...}}
}
```

**Errors:**
- `404`: `{"error": "agent_not_found", "agent_code": "..."}`
- `403`: `{"error": "agent_not_enabled", "agent_code": "...", "tenant_id": "..."}`

**Idempotency:**
Повторный запрос с тем же `(tenant_id, agent_code, idempotency_key)` возвращает существующий execution без создания дубликата.

#### GET /api/v1/agents/executions/{execution_id}

Получить детали выполнения.

**Headers:**
- `X-Tenant-ID`: UUID tenant'а

**Response (200):**
```json
{
  "execution_id": "uuid",
  "agent_code": "sales_assistant",
  "status": "completed",
  "created_at": "2026-01-15T...",
  "started_at": "...",
  "finished_at": "...",
  "result": {...},
  "error": null
}
```

**Tenant isolation:** Fail-closed. Execution другого tenant'а возвращает 404.

#### GET /api/v1/agents/executions

Список выполнений tenant'а.

**Headers:**
- `X-Tenant-ID`: UUID tenant'а

**Query params:**
- `agent_code` (optional): фильтр по агенту
- `limit` (optional): 1-200, default 50

**Response (200):**
```json
[
  {
    "execution_id": "uuid",
    "agent_code": "sales_assistant",
    "status": "completed",
    "created_at": "..."
  }
]
```

### Модель данных

#### Таблица: `agent_executions`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | Tenant |
| `agent_sku_id` | UUID | FK на agent_skus |
| `status` | enum | accepted, running, completed, failed, rejected |
| `idempotency_key` | string | Ключ идемпотентности |
| `input_json` | JSON | Входные данные |
| `result_json` | JSON | Результат (nullable) |
| `error_code` | string | Код ошибки (nullable) |
| `error_message` | string | Сообщение об ошибке (nullable) |
| `created_at` | timestamp | Создано |
| `updated_at` | timestamp | Обновлено |
| `started_at` | timestamp | Начало выполнения (nullable) |
| `finished_at` | timestamp | Завершение (nullable) |

### Ограничения

- UNIQUE(tenant_id, agent_sku_id, idempotency_key)
- FK на agent_skus.id

---

## Metering & Limits

### Метрики

| Метрика | Описание |
|---------|----------|
| `agent_executions` | Количество запусков агентов за период |

Метрика учитывается в `Plan.quotas` и отображается через `GET /api/v1/tenant/limits`.

### Тарификация

При успешном выполнении агента:
- Создаётся запись в `BillingUsage` с `metric=agent_executions`, `units=1`
- Usage учитывается в текущем периоде (YYYY-MM)

### Enforcement (HTTP 429)

Если лимит `agent_executions` превышен:

**Response:**
```json
{
  "error": "plan_limit_exceeded",
  "metric": "agent_executions",
  "limit": 100,
  "used": 100,
  "tenant_id": "..."
}
```

**Поведение:**
- Execution создаётся со статусом `rejected`
- `error_code = "plan_limit_exceeded"`
- Повторный запрос с тем же `idempotency_key` возвращает тот же 429 (без дублей)

### Важно

⚠️ **Текущая версия**: тарифицируется только количество запусков.
Детализация по токенам, шагам и времени будет добавлена позже.

---

## Tenant Portal API: Agents Catalog

### GET /api/v1/tenant/agents

Возвращает каталог агентов, доступных для tenant'а.

**Auth:** `X-Tenant-ID` + `X-Tenant-Portal-Key`

**Response:**
```json
{
  "items": [
    {
      "code": "sales_assistant",
      "name": "Sales Assistant",
      "description": "AI-powered sales assistant",
      "status": "active",
      "pricing_model": "subscription",
      "enabled": true,
      "tenant_status": "enabled"
    },
    {
      "code": "support_bot",
      "name": "Support Bot",
      "description": null,
      "status": "active",
      "pricing_model": "usage_based",
      "enabled": false,
      "tenant_status": null
    }
  ]
}
```

### Правила фильтрации

- Возвращаются только SKU со статусом `active`
- SKU со статусом `deprecated` или `disabled` скрыты

### Семантика полей

| Поле | Описание |
|------|----------|
| `enabled` | `true` если `TenantAgent.status == "enabled"` |
| `tenant_status` | Статус TenantAgent: `enabled`, `disabled`, `suspended`, или `null` если не подключён |
| `addon_status` | Статус подписки: `active`, `inactive`, `canceled`, `past_due`, или `null` |
| `paid` | `true` только если `addon_status == "active"` |

---

## Pricing v1: Subscription Add-on

### Обзор

Агенты являются платными add-on'ами. Для выполнения агента tenant должен иметь:
1. `TenantAgent.status == "enabled"` (включён)
2. `TenantAgentSubscription.status == "active"` (оплачен)

### Модель: TenantAgentSubscription

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | Tenant |
| `agent_sku_id` | UUID | FK на agent_skus |
| `status` | enum | `active`, `inactive`, `canceled`, `past_due` |
| `starts_at` | timestamp | Начало подписки |
| `ends_at` | timestamp | Окончание (nullable) |
| `cancel_at_period_end` | bool | Отмена в конце периода |
| `source` | string | `admin`, `kaspi`, `stripe` |
| `external_ref` | string | ID в платежной системе (nullable) |

### Статусы подписки

| Статус | Описание | Execute разрешён |
|--------|----------|------------------|
| `active` | Оплачен/активен | ✅ Да |
| `inactive` | Не активирован | ❌ Нет |
| `canceled` | Отменён | ❌ Нет |
| `past_due` | Просрочен | ❌ Нет |

### Gating: HTTP 402

Если add-on не активен, execute возвращает:

```json
HTTP 402 Payment Required
{
  "error": "agent_addon_inactive",
  "agent_code": "sales_assistant",
  "tenant_id": "...",
  "status": "inactive"
}
```

### Приоритет проверок

1. `TenantAgent.status` → 403 `agent_not_enabled`
2. `TenantAgentSubscription.status` → 402 `agent_addon_inactive`
3. Plan limits → 429 `plan_limit_exceeded`

### Source

Поле `source` указывает источник подписки:

| Source | Описание |
|--------|----------|
| `admin` | Создано вручную через Admin Panel |
| `kaspi` | Оплачено через Kaspi (будущее) |
| `stripe` | Оплачено через Stripe (будущее) |

⚠️ **Текущая версия**: только `admin`. Интеграция с платежными системами будет добавлена позже.

---

## Billing Sync (Events)

### Обзор

Синхронизация статуса подписок происходит через доменные события.
Это позволяет подключить любой источник (Admin, Kaspi, Stripe) к единой логике.

### Событие: AgentAddonSubscriptionUpdated

```python
@dataclass(frozen=True)
class AgentAddonSubscriptionUpdated:
    tenant_id: str
    agent_code: str
    status: str  # active, inactive, canceled, past_due
    source: str  # admin, kaspi, stripe
    external_ref: Optional[str] = None
    effective_at: Optional[str] = None
```

### Обработчик

`handle_agent_addon_subscription_updated(session, event)`:

1. Находит `AgentSKU` по `agent_code`
2. Upsert `TenantAgentSubscription` по `(tenant_id, agent_sku_id)`
3. Обновляет `status`, `source`, `external_ref`
4. Логика по статусам:
   - `active`: устанавливает `starts_at`, очищает `ends_at`
   - `canceled`: устанавливает `ends_at`
   - `inactive`/`past_due`: сохраняет текущие даты

### Идемпотентность

Повторная обработка того же события безопасна — состояние корректно обновляется без дублей.

### Service Function

```python
update_agent_subscription_via_event(
    session,
    tenant_id="...",
    agent_code="...",
    status="active",
    source="admin",
)
```

Создаёт событие и обрабатывает его. Используется из Admin, тестов, или API.

### Источники событий

| Source | Описание |
|--------|----------|
| `admin` | Создано вручную через Admin Panel |
| `kaspi` | Webhook от Kaspi Pay (будущее) |
| `stripe` | Webhook от Stripe (будущее) |

⚠️ **Текущая версия**: только `admin`. Webhooks будут публиковать то же событие.

---

## Webhook Mapping (Kaspi/Stripe Ready)

### Обзор

Входящие webhook payload от платежных систем нормализуются в `NormalizedBillingSignal`,
который затем преобразуется в `AgentAddonSubscriptionUpdated` событие.

### Поддерживаемые Event Types

**Stripe (строгий allowlist):**
- `checkout.session.completed`
- `invoice.paid`
- `invoice.payment_failed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

**Kaspi (строгий allowlist):**
- `SUBSCRIPTION_STATUS_CHANGED`
- `SUBSCRIPTION_CREATED`
- `SUBSCRIPTION_CANCELED`
- `PAYMENT_COMPLETED`
- `PAYMENT_FAILED`

### Требования к Payload

**Stripe:**
- `metadata.addon_type = "agent"` (обязательно)
- `metadata.tenant_id` (обязательно)
- `metadata.agent_code` (обязательно)

**Kaspi:**
- `addon_type = "agent"` (обязательно)
- `tenant_id` (обязательно)
- `agent_code` (обязательно)

### Status Mapping

| Stripe | Kaspi | Internal |
|--------|-------|----------|
| active, trialing | ACTIVE | active |
| past_due, unpaid | PAST_DUE, SUSPENDED | past_due |
| canceled | CANCELED | canceled |
| incomplete | PENDING | inactive |

Неизвестный статус → `None` (safe no-op, событие игнорируется).

### Safe No-Op

Mapper никогда не бросает исключения:
- Неизвестный `event_type` → `None`
- Неизвестный `status` → `None`
- Отсутствует `addon_type=agent` → `None`
- Кривой payload → `None` + debug log

Это гарантирует, что mapper не сломает основной webhook pipeline.

### Dry-Run Mode

Переменная окружения `BILLING_WEBHOOK_DRY_RUN=true`:
- Маппинг выполняется
- События логируются: `[DRY-RUN] Would emit AgentAddonSubscriptionUpdated...`
- БД **не изменяется**

Полезно для тестирования webhook интеграции без риска.

### Использование

```python
# Вариант 1: с явной сессией
from cyberplat.billing.application.agent_addons_mapper import process_webhook_for_agent_addons

process_webhook_for_agent_addons(
    source="stripe",
    payload=webhook_payload,
    session=db_session,
    dry_run=None,  # использует ENV
)

# Вариант 2: fire-and-forget wrapper (создаёт свою сессию)
from cyberplat.billing.application.agent_addons_mapper import try_process_agent_addon_webhook

try_process_agent_addon_webhook("stripe", webhook_payload)  # никогда не бросает
```

---

## Tenant Portal UI: /tenant/agents

### Обзор

Страница `/tenant/agents` в Tenant Portal показывает:
- Каталог доступных агентов (активные SKUs)
- Статусы: Enabled/Not enabled, Paid/Not paid/Past due
- Usage summary: количество `agent_executions` за период
- Список последних executions (для конкретного агента)

### Навигация

Ссылка "Agents" в главном меню портала.

### UI элементы

| Элемент | Описание |
|---------|----------|
| Agent Executions summary | Количество выполнений за месяц + лимит |
| Agents table | Список SKUs с бейджами статуса |
| "Enabled" badge | Зелёный если `tenant_status == "enabled"` |
| "Paid" badge | Синий если `addon_status == "active"` |
| "Past due" badge | Жёлтый warning если `addon_status == "past_due"` |
| Executions link | Ссылка на `/tenant/agents/{code}/executions` |

### Страница executions

`/tenant/agents/{agent_code}/executions` — список последних 50 выполнений:
- ID (с кнопкой копирования)
- Status (completed, rejected, failed, running)
- Created at
- Error code (если есть)

### Ручное тестирование

1. Создать AgentSKU через Admin Panel (status=active)
2. Создать TenantAgent (status=enabled)
3. Создать TenantAgentSubscription (status=active)
4. Открыть /tenant/agents
5. Проверить: агент показан, Enabled + Paid badges
6. Выполнить `POST /api/v1/agents/{code}/execute`
7. Открыть /tenant/agents/{code}/executions
8. Проверить: execution появился в списке

---

## Примеры

### Создание агента через Admin

1. Перейти в `/admin`
2. Выбрать "Agent SKUs"
3. Нажать "Create"
4. Заполнить:
   - code: `sales_assistant`
   - name: `Sales Assistant`
   - status: `active`
   - pricing_model: `subscription`
5. Сохранить

### Отключение агента

1. Открыть агент в Admin
2. Изменить status на `disabled`
3. Сохранить
