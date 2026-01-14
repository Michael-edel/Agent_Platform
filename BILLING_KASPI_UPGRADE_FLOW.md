# Billing: Kaspi Upgrade Flow (MVP)

## Goal

Production-safe коммерческий поток:

1. Клиент выбирает план **pro/enterprise** → получает `checkout_url`
2. Клиент оплачивает в Kaspi
3. Kaspi webhook подтверждает оплату → **`tenant_plans` переключается на оплаченный план**
4. Квоты/Enforcement начинают работать по новому плану

## Components

### 1) `kaspi_orders` (new table)

Таблица связывает внешний `kaspi_order_id` (external_order_id от Kaspi) с `tenant_id` и `plan_id` (из `plans.id`).

**Schema (Alembic):** `alembic/versions/a2b3c4d5e6f7_add_kaspi_orders.py`

Поля:
- `id` (uuid PK)
- `tenant_id` (index)
- `plan_id` (index) — `plans.id`: `pro` | `enterprise`
- `kaspi_order_id` (UNIQUE)
- `status` — `created|paid|failed|canceled`
- `created_at`, `updated_at`, `paid_at`
- `last_error`
- `raw_payload` (JSON string, **только safe поля**, без секретов/подписей/токенов)

### 2) Checkout endpoint

**Endpoint:** `POST /api/v1/billing/checkout/kaspi`

**Headers:**
- `X-Tenant-ID: <tenant>`

**Body:**
```json
{ "plan_id": "pro" }
```

Поддерживается backward compatibility:
- `plan_id: "pro" | "enterprise"` (новая система, `plans.id`)
- `plan_id: "plan_pro" | "plan_enterprise"` (legacy, не ломаем старых клиентов)

**Response:**
```json
{
  "checkout_url": "https://…",
  "order_id": "<internal_order_id>",
  "kaspi_order_id": "<external_order_id_from_kaspi>"
}
```

Поведение:
- валидирует `X-Tenant-ID`
- валидирует, что план существует и активен в таблице `plans`
- создаёт legacy `billing_orders` (для существующего billing слоя)
- вызывает Kaspi provider (создаёт checkout session)
- создаёт строку в `kaspi_orders` (`tenant_id`, `plan_id`, `kaspi_order_id`, `status=created`)
- эмитит событие `billing.kaspi.checkout.created`

### 3) Kaspi webhook

**Endpoint:** `POST /api/v1/billing/webhook/kaspi`

**Headers:**
- `X-Kaspi-Signature: …` (валидация подписи остаётся как есть)

Поведение (добавлено поверх текущего ProcessWebhookUseCase):
- извлекает `kaspi_order_id` из payload (`external_order_id|order_id`)
- находит `kaspi_orders` по `kaspi_order_id`
  - если не найден → **логирует warning**, возвращает 200 (не падает)
- идемпотентность:
  - если `kaspi_orders.status == "paid"` и событие paid → **no-op**, 200 OK
- если событие `payment.paid`/`order.paid`:
  - `kaspi_orders.mark_paid(...)`
  - `tenant_plans.assign_plan(tenant_id, plan_id, expires_at=null)`
  - event: `billing.plan.upgraded` (payload: tenant_id, plan_id, provider="kaspi", kaspi_order_id)
- если событие `payment.failed|payment.canceled`:
  - `kaspi_orders.mark_failed/mark_canceled`
  - event: `billing.payment.failed`

## Idempotency guarantees

1) **Webhook event ledger** (существующий): предотвращает повторную обработку одного и того же `event_id`.
2) **Order-level idempotency** (новое): `kaspi_orders.status == "paid"` блокирует повторное применение плана, даже если webhook прилетит повторно/в другом виде.

## Events & outgoing webhooks

События:
- `billing.kaspi.checkout.created`
- `billing.plan.upgraded`
- `billing.payment.failed`

Outgoing webhooks (если у клиента настроены):
- `plan.upgraded` (маппинг из `billing.plan.upgraded`)
- `payment.failed` (маппинг из `billing.payment.failed`)

## Manual verification (curl)

### 1) Create checkout
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/billing/checkout/kaspi" ^
  -H "X-Tenant-ID: tenant-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"plan_id\":\"pro\"}"
```

Ожидаемо: получите `checkout_url` и `kaspi_order_id`. Перейдите по `checkout_url` и оплатите.

### 2) Simulate webhook (paid)
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/billing/webhook/kaspi" ^
  -H "X-Kaspi-Signature: test" ^
  -H "Content-Type: application/json" ^
  -d "{\"id\":\"evt_test_1\",\"type\":\"payment.paid\",\"external_order_id\":\"<kaspi_order_id>\"}"
```

### 3) Check portal
```bash
curl "http://127.0.0.1:8000/api/v1/billing/portal" ^
  -H "X-Tenant-ID: tenant-1"
```

Ожидаемо: `plan.id` станет `pro` (или `enterprise`).

## Tests

**File:** `tests/test_kaspi_upgrade_flow.py`

Покрывает:
- checkout создаёт `kaspi_orders`
- webhook paid применяет план и идемпотентен
- webhook failed помечает заказ failed
- tenant isolation
- portal отражает upgraded план

