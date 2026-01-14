# Billing Enforcement (Paywall) Documentation

## Overview

Billing Enforcement — это paywall система, которая блокирует платные операции при превышении квот. Реализована на уровне application layer (use cases), без breaking changes.

## Feature Flags

**Environment Variables:**
- `BILLING_ENFORCEMENT_ENABLED=0|1` (default: 0) - Включить/выключить enforcement
- `BILLING_ENFORCEMENT_MODE=block|warn` (default: block) - Режим работы
- `BILLING_PERIOD_SOURCE=now` (default: now) - Источник периода для проверки квот

**Поведение:**
- Если `BILLING_ENFORCEMENT_ENABLED=0` → enforcement выключен, все операции разрешены
- Если `BILLING_ENFORCEMENT_ENABLED=1`:
  - `block` → операции блокируются при превышении квоты (HTTP 402)
  - `warn` → операции разрешены, но логируются и эмитятся события

## Blocked Operations

### 1. Document Upload

**Endpoint:** `POST /documents/upload`

**Метрика:** `document_upload` (units=1)

**Проверка:** Перед созданием artifact

**Пример:**
```bash
curl -X POST http://localhost:8000/documents/upload \
  -H "X-Tenant-ID: tenant-1" \
  -F "file=@invoice.pdf"
```

**Response при превышении квоты (402):**
```json
{
  "detail": {
    "detail": "Квота превышена",
    "metric": "document_upload",
    "period": "2026-01",
    "used_units": 5.0,
    "monthly_quota": 5,
    "operation": "document_upload",
    "upgrade_url": null
  }
}
```

### 2. Run OCR

**Endpoint:** `POST /api/v1/documents/{id}/run-ocr`

**Метрики:**
- `invoice_extracted` (units=1)
- `page_processed` (units=1, для MVP)

**Проверка:** Перед запуском doc_agent

**Пример:**
```bash
curl -X POST http://localhost:8000/api/v1/documents/{document_id}/run-ocr \
  -H "X-Tenant-ID: tenant-1"
```

**Response при превышении квоты (402):**
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

### 3. Email Auto-OCR

**Endpoint:** `POST /api/v1/ingest/email` (если `EMAIL_AUTO_OCR_ENABLED=1`)

**Метрики:**
- `invoice_extracted` (units=1)
- `page_processed` (units=1)

**Проверка:** Перед созданием OCR job

**Поведение:** Если квота превышена, email обрабатывается, но OCR job не создаётся (логируется warning)

### 4. Email Auto-OCR Jobs Dispatch

**Endpoint:** `POST /api/v1/ingest/email/ocr/dispatch` (admin-only)

**Метрики:**
- `invoice_extracted` (units=1)
- `page_processed` (units=1)

**Проверка:** Перед запуском OCR для каждого job

**Поведение:** Если квота превышена для tenant job:
- Job пропускается (не блокирует весь dispatch)
- Job отмечается как failed с ошибкой "Quota exceeded"
- Логируется warning

## Enforcement Logic

**Service:** `BillingEnforcementService`

**Метод:** `enforce(tenant_id, required_metrics, operation_name, period=None)`

**Алгоритм:**
1. Получает статус квот через `billing_service.get_quota_status()`
2. Для каждой требуемой метрики проверяет: `used_units + required_units > monthly_quota`
3. Если любая метрика превышена:
   - `mode=block` → выбрасывает `QuotaExceededError`
   - `mode=warn` → логирует и эмитит событие, но продолжает выполнение

**Период:**
- Если `period` не указан, используется текущий период (YYYY-MM) из `BILLING_PERIOD_SOURCE`

## Error Response Format

**HTTP Status:** `402 Payment Required`

**Response Body:**
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

**Fields:**
- `metric` - Метрика, для которой превышена квота
- `period` - Период (YYYY-MM)
- `used_units` - Текущее использование
- `monthly_quota` - Месячная квота
- `operation` - Название операции
- `upgrade_url` - URL для upgrade (если доступно, пока null)

## Events

### billing.quota.exceeded

**Triggered when:** Квота превышена при попытке выполнить платную операцию.

**Payload:**
```json
{
  "metric": "invoice_extracted",
  "period": "2026-01",
  "used_units": 10.0,
  "required_units": 1.0,
  "monthly_quota": 10,
  "operation": "run_ocr",
  "all_exceeded_metrics": [
    {
      "metric": "invoice_extracted",
      "used_units": 10.0,
      "required_units": 1.0,
      "monthly_quota": 10,
      "would_exceed": 11.0
    }
  ]
}
```

## Webhooks

**Supported Event:** `quota.exceeded`

**Mapping:** `billing.quota.exceeded` → `quota.exceeded` (для webhooks)

**Payload (безопасный, без raw данных):**
```json
{
  "event_type": "quota.exceeded",
  "tenant_id": "tenant-1",
  "timestamp": "2026-01-15T10:30:00",
  "metric": "invoice_extracted",
  "period": "2026-01",
  "used_units": 10.0,
  "required_units": 1.0,
  "monthly_quota": 10,
  "operation": "run_ocr"
}
```

## Structured Logging

**Log Event:** `billing_enforcement_blocked`

**Extra Fields:**
- `tenant_id`
- `metric`
- `period`
- `operation`
- `used_units`
- `required_units`
- `monthly_quota`

**Example:**
```python
logger.warning(
    "billing_enforcement_blocked",
    extra={
        "tenant_id": "tenant-1",
        "metric": "invoice_extracted",
        "period": "2026-01",
        "operation": "run_ocr",
        "used_units": 10.0,
        "required_units": 1.0,
        "monthly_quota": 10
    }
)
```

## Setup

### Enable Enforcement

```bash
# Prod режим (block)
export BILLING_ENFORCEMENT_ENABLED=1
export BILLING_ENFORCEMENT_MODE=block

# Dev режим (warn, для тестирования)
export BILLING_ENFORCEMENT_ENABLED=1
export BILLING_ENFORCEMENT_MODE=warn

# Disable (default)
export BILLING_ENFORCEMENT_ENABLED=0
```

### Configure Quotas

```bash
# Установить квоту для метрики (через API или напрямую в БД)
# Пример: 10 документов в месяц
curl -X POST http://localhost:8000/api/v1/billing/admin/rate \
  -H "X-Admin-Key: ${ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-1",
    "metric": "document_upload",
    "unit_price_minor": 100,
    "currency": "USD",
    "monthly_quota": 10
  }'
```

## Scenarios

### Trial Plan

**Квоты:**
- `document_upload`: 5 в месяц
- `invoice_extracted`: 3 в месяц
- `page_processed`: 10 в месяц

**Поведение:**
- После исчерпания квот операции блокируются (402)
- Клиент получает уведомление через webhook `quota.exceeded`
- Клиент может upgrade план

### Paid Plan

**Квоты:**
- `document_upload`: 100 в месяц
- `invoice_extracted`: 50 в месяц
- `page_processed`: 500 в месяц

**Поведение:**
- Операции разрешены в пределах квот
- При превышении → блокировка или предупреждение (в зависимости от mode)

### Unlimited Plan

**Квоты:**
- Все метрики: `monthly_quota = null` (без ограничений)

**Поведение:**
- Все операции разрешены
- Enforcement не блокирует (нет квот для проверки)

## Testing

### Manual Test

**1. Установить квоту:**
```bash
# Через billing service (если есть admin endpoint)
# Или напрямую в БД
```

**2. Исчерпать квоту:**
```bash
# Загрузить N документов (где N = quota)
for i in {1..5}; do
  curl -X POST http://localhost:8000/documents/upload \
    -H "X-Tenant-ID: tenant-1" \
    -F "file=@invoice.pdf"
done
```

**3. Попытаться выполнить операцию:**
```bash
curl -X POST http://localhost:8000/documents/upload \
  -H "X-Tenant-ID: tenant-1" \
  -F "file=@invoice.pdf"
```

**Ожидаемый результат:** HTTP 402 Payment Required

### Automated Tests

**Файл:** `tests/test_billing_enforcement.py`

**Тесты:**
- `test_upload_blocked_when_document_upload_quota_exceeded`
- `test_run_ocr_blocked_when_invoice_extracted_quota_exceeded`
- `test_email_auto_ocr_job_skipped_when_quota_exceeded`
- `test_enforcement_disabled_allows_operations`
- `test_warn_mode_does_not_block`
- `test_tenant_isolation_enforcement`
- `test_quota_exceeded_webhook_created`

**Запуск:**
```bash
pytest tests/test_billing_enforcement.py -v
```

## Integration Points

### Application Layer

**Use Cases:**
- `UploadDocumentUseCase` (если будет создан) → enforce `document_upload`
- `RunOCRUseCase` → enforce `invoice_extracted + page_processed`
- `EmailIngestUseCase` → enforce при auto-OCR
- `ProcessEmailOcrJobsUseCase` → enforce перед запуском OCR

**Endpoints:**
- `POST /documents/upload` → enforcement в endpoint
- `POST /api/v1/documents/{id}/run-ocr` → enforcement в endpoint
- `POST /api/v1/ingest/email` → enforcement в use case
- `POST /api/v1/ingest/email/ocr/dispatch` → enforcement в use case (для каждого job)

### Infrastructure

**Использует:**
- `BillingService.get_quota_status()` - получение статуса квот
- `EventService.emit()` - эмиссия событий
- `EntitlementService` (опционально) - для upgrade_url

## Best Practices

1. **Всегда проверяйте квоты ДО выполнения операции** (не после)
2. **Используйте structured logging** для audit trail
3. **Эмитьте события** для observability
4. **Не блокируйте admin endpoints** целиком (только отдельные tenant jobs)
5. **Warn mode для staging** (чтобы видеть превышения без блокировки)
6. **Block mode для production** (реальная защита)

## Troubleshooting

### Operations not blocked

**Проверка:**
1. `BILLING_ENFORCEMENT_ENABLED=1`?
2. `BILLING_ENFORCEMENT_MODE=block`?
3. Квота установлена для метрики?
4. Usage действительно превышает квоту?

**Debug:**
```bash
# Проверить статус квот
curl http://localhost:8000/api/v1/billing/quota?period=2026-01 \
  -H "X-Tenant-ID: tenant-1"
```

### False positives (blocked when shouldn't)

**Проверка:**
1. Правильный период? (используется текущий YYYY-MM)
2. Правильная метрика? (проверьте required_metrics)
3. Квота не null? (null = unlimited)

### Events not emitted

**Проверка:**
1. EventService инициализирован?
2. Webhook subscriber зарегистрирован?
3. Логи на ошибки эмиссии событий?

## Summary

Billing Enforcement обеспечивает:
- ✅ Paywall для платных операций
- ✅ Tenant-safe (все проверки tenant-scoped)
- ✅ Feature flags для управления
- ✅ Block и Warn режимы
- ✅ Events и webhooks для observability
- ✅ Structured logging
- ✅ No breaking changes (enforcement опциональный)

Готово к использованию! 🚀
