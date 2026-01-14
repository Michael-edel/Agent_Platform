# Webhooks Implementation Summary

## ✅ Все задачи выполнены

Добавлены клиентские webhooks (outgoing) для уведомления клиентов о важных событиях в системе.

## 1. Миграция и модели

**Миграция:** `alembic/versions/e9f0a1b2c3d4_add_webhooks.py`

**Таблицы:**
- `webhooks` - конфигурация webhooks для tenant
- `webhook_deliveries` - история доставок

**Модели:** `cyberplat/product/infrastructure/models.py`
- `Webhook` - модель для webhooks
- `WebhookDelivery` - модель для deliveries

## 2. Domain interfaces

**Файл:** `cyberplat/product/domain/interfaces.py`

**Интерфейсы:**
- `WebhookRepository` - методы для управления webhooks
- `WebhookDeliveryRepository` - методы для управления deliveries

## 3. Репозитории

**Файл:** `cyberplat/product/infrastructure/webhook_repositories_sqlalchemy.py`

**Реализации:**
- `WebhookRepositoryImpl` - CRUD для webhooks
- `WebhookDeliveryRepositoryImpl` - CRUD для deliveries + получение pending deliveries

## 4. Event → Webhook pipeline

**Файл:** `cyberplat/product/infrastructure/webhook_subscriber.py`

**Функциональность:**
- Subscriber для EventService
- Создаёт deliveries при эмиссии событий: `invoice.ready`, `invoice.failed`, `email.ocr.completed`, `email.ocr.dead`, `invoice.confirmed`
- Формирует безопасный payload (без raw PDF/OCR JSON)
- Регистрируется в `app/main.py` при старте приложения

## 5. Dispatcher

**Use Case:** `cyberplat/product/application/dispatch_webhooks_use_case.py`

**Функциональность:**
- Выбирает pending deliveries (queued/failed, next_run_at <= now)
- Отправляет HTTP POST на webhook.url
- HMAC-SHA256 подпись (header: X-Signature)
- Retry с exponential backoff + jitter
- Эмитит события: `webhook.sent`, `webhook.failed`, `webhook.dead`

**Endpoint:** `POST /api/v1/webhooks/dispatch` (admin-only)

## 6. Tenant API

**Файл:** `app/api/webhooks.py`

**Endpoints:**
- `GET /api/v1/webhooks` - Список webhooks (tenant-scoped)
- `POST /api/v1/webhooks` - Создать webhook
- `DELETE /api/v1/webhooks/{id}` - Удалить webhook
- `POST /api/v1/webhooks/{id}/rotate-secret` - Обновить secret

Все endpoints требуют `X-Tenant-ID` и tenant-scoped.

## 7. Тесты

**Файл:** `tests/test_webhooks.py`

**Тесты:**
1. ✅ `test_webhooks_are_tenant_isolated` - tenant isolation
2. ✅ `test_delivery_retry_on_failure` - delivery retries
3. ✅ `test_signature_correctness` - HMAC подпись
4. ✅ `test_one_event_creates_one_delivery_per_webhook` - idempotency
5. ✅ `test_dispatch_requires_admin_key_when_set` - admin protection
6. ✅ `test_dispatch_allows_without_key_in_dev_mode` - dev mode
7. ✅ `test_payload_does_not_contain_raw_data` - safe payload

## 8. Документация

**Файлы:**
- `WEBHOOKS.md` - полная документация по webhooks
- `README.md` - обновлён раздел Webhooks

**Содержание:**
- Supported events
- Payload format
- HMAC signature (алгоритм, примеры Python/Node.js)
- Tenant API (все endpoints)
- Admin dispatch endpoint
- Retry policy
- Environment variables
- Example webhook handlers (Python Flask, Node.js Express)
- Events details
- Best practices
- Troubleshooting

## Пример использования

### Создать webhook

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "X-Tenant-ID: tenant-1" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhook",
    "events": ["invoice.ready", "invoice.confirmed"]
  }'
```

**Response:**
```json
{
  "id": "webhook-123",
  "url": "https://example.com/webhook",
  "events": ["invoice.ready", "invoice.confirmed"],
  "active": true,
  "created_at": "2026-01-15T10:00:00"
}
```

**Важно:** Secret генерируется автоматически, но не возвращается в response (для безопасности). Используйте rotate-secret для получения нового secret.

### Dispatch deliveries

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/dispatch?limit=10 \
  -H "X-Admin-Key: your-admin-key"
```

### Верификация webhook на стороне клиента

```python
import hmac
import hashlib
import json

def verify_webhook_signature(payload: dict, signature: str, secret: str) -> bool:
    payload_json = json.dumps(payload, sort_keys=True)
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        payload_json.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)
```

## Безопасность

- ✅ Tenant isolation (все операции tenant-scoped)
- ✅ HMAC-SHA256 подпись для верификации
- ✅ Safe payload (без raw PDF/OCR JSON)
- ✅ Admin-only dispatch endpoint
- ✅ Secret rotation

## Retry Policy

- Exponential backoff: `base * 2^(attempts-1) + jitter`
- Max retries: 5 (настраивается через `WEBHOOK_MAX_RETRIES`)
- Max backoff: 600 секунд (настраивается через `WEBHOOK_RETRY_MAX_SECONDS`)

## Статус

✅ **Все задачи выполнены**

- ✅ Миграция создана
- ✅ Модели созданы
- ✅ Domain interfaces добавлены
- ✅ Репозитории реализованы
- ✅ Event → Webhook pipeline (subscriber)
- ✅ Dispatcher use case реализован
- ✅ Admin dispatch endpoint создан
- ✅ Tenant API endpoints созданы
- ✅ Тесты добавлены (7 тестов)
- ✅ Документация создана

Готово к использованию! 🚀
