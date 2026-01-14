## Kaspi subscription (production-like MVP)

Этот документ описывает периодизацию и recurring для платных планов через Kaspi.

### Модель данных

- **`tenant_plans`**
  - **`plan_id`**: `trial | pro | enterprise`
  - **`expires_at`**:
    - для `trial`: дата окончания trial
    - для `pro/enterprise`: дата окончания оплаченного периода
  - **`subscription_status`**: `trial | active | past_due | canceled`
  - **`failed_charges`**: количество подряд неуспешных попыток списания

### Env переменные

- **`BILLING_PERIOD_DAYS`** (default `30`): длительность оплаченного периода.
- **`RENEW_WINDOW_DAYS`** (default `2`): окно продления — начинаем пробовать списывать, когда `expires_at <= now + window`.
- **`SUBSCRIPTION_MAX_FAILED_CHARGES`** (default `3`): после N неудач подписка отменяется.

### Upgrade (Kaspi webhook → activation)

При успешном Kaspi webhook (paid):
- `tenant_plans.plan_id = выбранный план (pro/enterprise)`
- `tenant_plans.subscription_status = active`
- `tenant_plans.failed_charges = 0`
- `tenant_plans.expires_at = now + BILLING_PERIOD_DAYS`

Событие:
- `billing.plan.upgraded`

### Recurring cron

Endpoint: `POST /api/v1/billing/cron/charge-kaspi`

Алгоритм:
- выбираем tenants:
  - `plan_id in (pro, enterprise)`
  - `subscription_status in (active, past_due)`
  - `expires_at <= now + RENEW_WINDOW_DAYS` (или expires_at отсутствует)
- пытаемся списать по `kaspi_token` (из `billing_kaspi_profiles`)
- если успех:
  - `expires_at = max(expires_at, now) + BILLING_PERIOD_DAYS` (**идемпотентно**)
  - `subscription_status = active`, `failed_charges = 0`
  - событие `billing.subscription.renewed`
- если неуспех:
  - `failed_charges += 1`
  - `subscription_status = past_due`
  - событие `billing.subscription.past_due`
  - если `failed_charges >= SUBSCRIPTION_MAX_FAILED_CHARGES`:
    - `subscription_status = canceled`
    - downgrade: `plan_id = trial`, `expires_at = now` (trial_expired сразу)
    - событие `billing.subscription.canceled`

### Enforcement поведение

`BillingEnforcementService` блокирует платные операции:
- `subscription_status = past_due` → `402` `"Оплата просрочена"`
- `subscription_status = canceled` → `402` `"Подписка отменена"`

Trial expiry по-прежнему блокируется с `402` `"Пробный период истёк"`.

### Outgoing webhooks (client webhooks)

Добавлены события:
- `subscription.renewed`
- `subscription.past_due`
- `subscription.canceled`

### Ручная проверка (curl)

1) Запустить recurring (cron):

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/billing/cron/charge-kaspi"
```

2) Проверить portal:

```bash
curl -H "X-Tenant-ID: tenant-1" "http://127.0.0.1:8000/api/v1/billing/portal?period=2026-01"
```

