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
