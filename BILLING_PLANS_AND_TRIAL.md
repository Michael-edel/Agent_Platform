# Billing Plans and Trial Documentation

## Overview

Система тарифных планов с автоматическим trial и upgrade flow. Подготовка к реальным платежам (Stripe / Kaspi).

## Plans (Тарифные планы)

### Trial Plan

**ID:** `trial`

**Квоты:**
- `document_upload`: 20 в месяц
- `invoice_extracted`: 10 в месяц
- `page_processed`: 50 в месяц

**Цена:** Бесплатно (14 дней)

**Особенности:**
- Автоматически назначается при первом использовании tenant
- Истекает через `TRIAL_DAYS` дней (default: 14)
- После истечения блокируются все платные операции

### Pro Plan

**ID:** `pro`

**Квоты:**
- `document_upload`: 100 в месяц
- `invoice_extracted`: 50 в месяц
- `page_processed`: 500 в месяц

**Цена:** $29.00/месяц (2900 minor units)

**Особенности:**
- Paid план (не истекает)
- Upgrade доступен до Enterprise

### Enterprise Plan

**ID:** `enterprise`

**Квоты:**
- Все метрики: `null` (unlimited)

**Цена:** $99.00/месяц (9900 minor units)

**Особенности:**
- Unlimited usage
- Максимальный план (upgrade недоступен)

## Automatic Trial

**Поведение:**
- При первом появлении `tenant_id` в системе автоматически назначается `plan_id="trial"`
- `expires_at = now + TRIAL_DAYS` (env, default 14)
- Назначение происходит при первой платной операции (upload, run-ocr, email ingest)

**Environment Variable:**
- `TRIAL_DAYS=14` (default: 14)

**Пример:**
```python
# При первом upload для tenant-1
POST /documents/upload
X-Tenant-ID: tenant-1

# Автоматически создаётся:
# tenant_plans: tenant_id=tenant-1, plan_id=trial, expires_at=2026-01-30T...
```

## Trial Expiration

**Проверка:**
- Enforcement проверяет `is_trial_expired()` перед каждой платной операцией
- Если trial истёк → всегда блокируется (независимо от `BILLING_ENFORCEMENT_MODE`)

**Error Response (402):**
```json
{
  "detail": {
    "detail": "Пробный период истёк",
    "trial_expired": true,
    "expires_at": "2026-01-15T10:00:00",
    "upgrade_url": "/billing/upgrade"
  }
}
```

**Event:**
- `billing.trial.expired` - эмитируется при попытке выполнить операцию с истёкшим trial

**Webhook:**
- `trial.expired` - маппинг из `billing.trial.expired`

## Upgrade Flow (MVP)

**Endpoint:** `POST /api/v1/billing/upgrade`

**Request:**
```json
{
  "plan_id": "pro"
}
```

**Response:**
```json
{
  "success": true,
  "tenant_id": "tenant-1",
  "plan_id": "pro",
  "message": "Plan upgraded to Pro"
}
```

**Поведение:**
- Только tenant-scoped (требует `X-Tenant-ID`)
- Валидирует, что план активен
- Обновляет `tenant_plans`: `plan_id = выбранный`, `expires_at = null`
- Эмитит событие `billing.plan.upgraded`

**Webhook:**
- `plan.upgraded` - маппинг из `billing.plan.upgraded`

**Важно:**
- MVP: без реальных платежей (просто обновляет план)
- Endpoint будет использоваться UI
- В будущем: интеграция с Stripe/Kaspi для реальных платежей

## Billing Portal API

**Endpoint:** `GET /api/v1/billing/portal?period=YYYY-MM`

**Response:**
```json
{
  "tenant_id": "tenant-1",
  "period": "2026-01",
  "plan": {
    "id": "trial",
    "name": "Trial"
  },
  "subscription": {
    "status": "active",
    "subscription_id": null
  },
  "invoice": {
    "total_amount_minor": 0,
    "currency": "USD",
    "lines": []
  },
  "quota": {
    "metrics": {
      "document_upload": {
        "used_units": 5.0,
        "monthly_quota": 20,
        "remaining_units": 15,
        "is_exceeded": false
      }
    }
  },
  "links": {
    "upgrade_url": "/billing/upgrade",
    "manage_url": null
  },
  "trial": {
    "days_left": 10,
    "expires_at": "2026-01-30T10:00:00"
  },
  "upgrade_available": true
}
```

**Новые поля:**
- `trial` - информация о trial (days_left, expires_at), null если не trial
- `upgrade_available` - доступен ли upgrade (false для enterprise)

## Enforcement Integration

**BillingEnforcementService обновлён:**
- Использует квоты из `tenant_plans` → `plans` (приоритет над `billing_rates`)
- Проверяет `is_trial_expired()` перед проверкой квот
- Если trial истёк → всегда блокирует (TrialExpiredError)

**Метод `_get_quota_status()`:**
- Если используется система планов → получает квоты из плана
- Fallback на `billing_rates` (старая система)

## Events

### billing.trial.expired

**Triggered when:** Trial истёк и tenant пытается выполнить платную операцию.

**Payload:**
```json
{
  "expires_at": "2026-01-15T10:00:00",
  "operation": "document_upload"
}
```

### billing.plan.upgraded

**Triggered when:** Tenant обновил план через upgrade endpoint.

**Payload:**
```json
{
  "plan_id": "pro",
  "plan_name": "Pro",
  "previous_plan_id": null
}
```

## Webhooks

**Supported Events:**
- `trial.expired` - маппинг из `billing.trial.expired`
- `plan.upgraded` - маппинг из `billing.plan.upgraded`

**Payload (безопасный):**
```json
{
  "event_type": "trial.expired",
  "tenant_id": "tenant-1",
  "timestamp": "2026-01-16T10:30:00",
  "expires_at": "2026-01-15T10:00:00",
  "operation": "document_upload"
}
```

## Database Schema

### plans

```sql
CREATE TABLE plans (
    id TEXT PRIMARY KEY,  -- trial, pro, enterprise
    name TEXT NOT NULL,
    description TEXT,
    quotas TEXT NOT NULL,  -- JSON: {"document_upload": 20, ...}
    price_minor INTEGER,  -- null для trial
    currency TEXT,  -- null для trial
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

### tenant_plans

```sql
CREATE TABLE tenant_plans (
    tenant_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    started_at TEXT NOT NULL,  -- ISO timestamp
    expires_at TEXT,  -- ISO timestamp, null для paid планов
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);
```

## Setup

### Run Migration

```bash
alembic upgrade head
```

### Seed Default Plans

Миграция автоматически создаёт default планы:
- `trial` - 14 дней, квоты: 20/10/50
- `pro` - $29/месяц, квоты: 100/50/500
- `enterprise` - $99/месяц, unlimited

### Configure Trial Days

```bash
export TRIAL_DAYS=14  # default
```

## Usage Examples

### Check Trial Status

```bash
curl http://localhost:8000/api/v1/billing/portal \
  -H "X-Tenant-ID: tenant-1"
```

### Upgrade Plan

```bash
curl -X POST http://localhost:8000/api/v1/billing/upgrade \
  -H "X-Tenant-ID: tenant-1" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "pro"}'
```

### Test Trial Expiration

```bash
# 1. Назначить trial с истёкшим сроком (через БД или API)
# 2. Попытаться выполнить операцию
curl -X POST http://localhost:8000/documents/upload \
  -H "X-Tenant-ID: tenant-1" \
  -F "file=@invoice.pdf"

# Ожидаемый результат: HTTP 402 Payment Required
```

## Testing

**Файл:** `tests/test_billing_plans.py`

**Тесты:**
- `test_trial_assigned_on_first_tenant_use`
- `test_trial_expires_and_blocks_operations`
- `test_upgrade_changes_plan_and_unblocks`
- `test_enforcement_uses_plan_quotas`
- `test_tenant_isolation_plans`
- `test_trial_expired_webhook_emitted`

**Запуск:**
```bash
pytest tests/test_billing_plans.py -v
```

## Future: Real Payments

**Подготовка:**
- Upgrade endpoint готов к интеграции с Stripe/Kaspi
- Планы имеют `price_minor` и `currency`
- Portal API возвращает `upgrade_url` (можно использовать Stripe Checkout)

**Следующие шаги:**
1. Интеграция Stripe Checkout в upgrade endpoint
2. Обработка webhooks от Stripe для подтверждения платежа
3. Автоматическое обновление плана после успешного платежа
4. Аналогично для Kaspi

## Summary

Система планов обеспечивает:
- ✅ Автоматический trial для новых tenant
- ✅ Trial expiration и блокировка операций
- ✅ Upgrade flow (MVP без реальных платежей)
- ✅ Интеграция с enforcement (квоты из планов)
- ✅ Portal API с trial статусом
- ✅ Events и webhooks
- ✅ Подготовка к реальным платежам

Готово к использованию! 🚀
