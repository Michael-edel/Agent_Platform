# Webhooks Documentation

## Overview

CyberPlat поддерживает outgoing webhooks для уведомления клиентов о важных событиях в системе.

## Supported Events

- `invoice.ready` - Invoice готов (OCR завершён)
- `invoice.failed` - Invoice не удалось создать (ошибка OCR)
- `email.ocr.completed` - Email OCR завершён успешно
- `email.ocr.dead` - Email OCR провалился после всех попыток
- `invoice.confirmed` - Invoice подтверждён клиентом

## Webhook Payload Format

Все webhook payloads имеют следующий формат:

```json
{
  "event_type": "invoice.ready",
  "tenant_id": "tenant-1",
  "artifact_id": "artifact-123",
  "timestamp": "2026-01-15T10:30:00",
  "job_id": "job-456",
  "status": "ready"
}
```

**Важно:** Payload НЕ содержит:
- Raw OCR JSON
- PDF content
- Base64 encoded data
- Большие бинарные данные

Только IDs, статусы, timestamps и короткие сообщения об ошибках.

## HMAC Signature

Все webhook requests подписываются с помощью HMAC-SHA256.

**Header:** `X-Signature`

**Алгоритм:**
1. Сериализуем payload в JSON (сортировка ключей для детерминированности)
2. Вычисляем HMAC-SHA256: `HMAC-SHA256(secret, json_payload)`
3. Отправляем hex string в заголовке `X-Signature`

**Пример (Python):**
```python
import hmac
import hashlib
import json

payload = {"event_type": "invoice.ready", "tenant_id": "tenant-1"}
payload_json = json.dumps(payload, sort_keys=True)
secret = "your-webhook-secret"

signature = hmac.new(
    secret.encode('utf-8'),
    payload_json.encode('utf-8'),
    hashlib.sha256
).hexdigest()

# signature = "abc123..."
```

**Пример (Node.js):**
```javascript
const crypto = require('crypto');

const payload = {event_type: "invoice.ready", tenant_id: "tenant-1"};
const payloadJson = JSON.stringify(payload);
const secret = "your-webhook-secret";

const signature = crypto
  .createHmac('sha256', secret)
  .update(payloadJson)
  .digest('hex');

// signature = "abc123..."
```

**Верификация на стороне клиента:**
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

## Tenant API

### List Webhooks

```bash
GET /api/v1/webhooks
Headers:
  X-Tenant-ID: tenant-1
```

**Response:**
```json
{
  "webhooks": [
    {
      "id": "webhook-123",
      "url": "https://example.com/webhook",
      "events": ["invoice.ready", "invoice.confirmed"],
      "active": true,
      "created_at": "2026-01-15T10:00:00"
    }
  ]
}
```

### Create Webhook

```bash
POST /api/v1/webhooks
Headers:
  X-Tenant-ID: tenant-1
  Content-Type: application/json
Body:
{
  "url": "https://example.com/webhook",
  "events": ["invoice.ready", "invoice.confirmed"]
}
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

**Важно:** Secret генерируется автоматически и возвращается только при создании. Сохраните его!

### Delete Webhook

```bash
DELETE /api/v1/webhooks/{webhook_id}
Headers:
  X-Tenant-ID: tenant-1
```

**Response:**
```json
{
  "success": true,
  "message": "Webhook deleted"
}
```

### Rotate Secret

```bash
POST /api/v1/webhooks/{webhook_id}/rotate-secret
Headers:
  X-Tenant-ID: tenant-1
```

**Response:**
```json
{
  "success": true,
  "secret": "new-secret-token",
  "message": "Secret rotated. Save this secret - it will not be shown again."
}
```

## Admin Dispatch Endpoint

**Endpoint:** `POST /api/v1/webhooks/dispatch`

**Admin-only:** Требует `X-Admin-Key` заголовок (если `ADMIN_API_KEY` задан).

**Query параметры:**
- `limit` (int, default: 10) - максимальное количество deliveries

**Response:**
```json
{
  "processed": 5,
  "sent": 4,
  "failed": 1,
  "dead": 0,
  "errors": []
}
```

**Использование:**
```bash
curl -X POST http://localhost:8000/api/v1/webhooks/dispatch?limit=10 \
  -H "X-Admin-Key: your-admin-key"
```

**Cron setup:**
```bash
*/1 * * * * curl -X POST http://localhost:8000/api/v1/webhooks/dispatch?limit=10 \
  -H "X-Admin-Key: ${ADMIN_API_KEY}"
```

## Retry Policy

**Exponential Backoff:**
- Base: `WEBHOOK_RETRY_BASE_SECONDS` (default: 10)
- Formula: `base * 2^(attempts-1) + jitter`
- Max: `WEBHOOK_RETRY_MAX_SECONDS` (default: 600)
- Jitter: ±20%

**Max Retries:**
- `WEBHOOK_MAX_RETRIES` (default: 5)

**Statuses:**
- `queued` - готов к отправке
- `sent` - успешно отправлен
- `failed` - ошибка, запланирован retry
- `dead` - превышен max_retries

## Environment Variables

- `WEBHOOK_MAX_RETRIES=5` - Максимальное количество попыток
- `WEBHOOK_RETRY_BASE_SECONDS=10` - Базовое время для backoff
- `WEBHOOK_RETRY_MAX_SECONDS=600` - Максимальное время между попытками
- `WEBHOOK_HTTP_TIMEOUT_SECONDS=30` - HTTP timeout для запросов

## Example Webhook Handler

**Python (Flask):**
```python
from flask import Flask, request, jsonify
import hmac
import hashlib
import json

app = Flask(__name__)
WEBHOOK_SECRET = "your-webhook-secret"

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.json
    signature = request.headers.get('X-Signature')
    
    # Verify signature
    payload_json = json.dumps(payload, sort_keys=True)
    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode('utf-8'),
        payload_json.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature):
        return jsonify({"error": "Invalid signature"}), 401
    
    # Process webhook
    event_type = payload.get('event_type')
    artifact_id = payload.get('artifact_id')
    
    if event_type == 'invoice.ready':
        # Handle invoice ready
        print(f"Invoice ready: {artifact_id}")
    
    return jsonify({"success": True}), 200
```

**Node.js (Express):**
```javascript
const express = require('express');
const crypto = require('crypto');

const app = express();
app.use(express.json());

const WEBHOOK_SECRET = 'your-webhook-secret';

app.post('/webhook', (req, res) => {
  const payload = req.body;
  const signature = req.headers['x-signature'];
  
  // Verify signature
  const payloadJson = JSON.stringify(payload);
  const expectedSignature = crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(payloadJson)
    .digest('hex');
  
  if (signature !== expectedSignature) {
    return res.status(401).json({error: 'Invalid signature'});
  }
  
  // Process webhook
  const eventType = payload.event_type;
  const artifactId = payload.artifact_id;
  
  if (eventType === 'invoice.ready') {
    console.log(`Invoice ready: ${artifactId}`);
  }
  
  res.json({success: true});
});
```

## Events Details

### invoice.ready

**Triggered when:** Invoice artifact создан после успешного OCR.

**Payload:**
```json
{
  "event_type": "invoice.ready",
  "tenant_id": "tenant-1",
  "artifact_id": "invoice-artifact-id",
  "timestamp": "2026-01-15T10:30:00",
  "document_artifact_id": "document-artifact-id"
}
```

### invoice.failed

**Triggered when:** Invoice не удалось создать (ошибка OCR).

**Payload:**
```json
{
  "event_type": "invoice.failed",
  "tenant_id": "tenant-1",
  "artifact_id": "document-artifact-id",
  "timestamp": "2026-01-15T10:30:00",
  "error": "OCR failed: ..."
}
```

### email.ocr.completed

**Triggered when:** Email OCR завершён успешно.

**Payload:**
```json
{
  "event_type": "email.ocr.completed",
  "tenant_id": "tenant-1",
  "artifact_id": "invoice-artifact-id",
  "timestamp": "2026-01-15T10:30:00",
  "job_id": "job-123",
  "document_artifact_id": "document-artifact-id",
  "invoice_artifact_id": "invoice-artifact-id",
  "attempts": 1
}
```

### email.ocr.dead

**Triggered when:** Email OCR провалился после всех попыток.

**Payload:**
```json
{
  "event_type": "email.ocr.dead",
  "tenant_id": "tenant-1",
  "artifact_id": "document-artifact-id",
  "timestamp": "2026-01-15T10:30:00",
  "job_id": "job-123",
  "attempts": 5,
  "error": "Max retries exceeded",
  "reason": "timeout"
}
```

### invoice.confirmed

**Triggered when:** Invoice подтверждён клиентом.

**Payload:**
```json
{
  "event_type": "invoice.confirmed",
  "tenant_id": "tenant-1",
  "artifact_id": "invoice-artifact-id",
  "timestamp": "2026-01-15T10:30:00"
}
```

## Best Practices

1. **Always verify signature** - Не обрабатывайте webhooks без проверки подписи
2. **Idempotency** - Обработка webhook должна быть идемпотентной (повторные доставки не должны создавать дубликаты)
3. **Fast response** - Отвечайте 200 OK быстро (< 5 секунд), обработку делайте асинхронно
4. **Logging** - Логируйте все входящие webhooks для audit trail
5. **Error handling** - Возвращайте 200 OK даже при ошибках обработки (чтобы не было retries), логируйте ошибки

## Troubleshooting

### Webhook не доставляется

1. Проверить, что webhook активен:
   ```bash
   curl http://localhost:8000/api/v1/webhooks \
     -H "X-Tenant-ID: tenant-1"
   ```

2. Проверить deliveries:
   ```sql
   SELECT * FROM webhook_deliveries 
   WHERE webhook_id = 'webhook-id' 
   ORDER BY created_at DESC;
   ```

3. Проверить логи dispatch:
   ```bash
   # Запустить dispatch вручную
   curl -X POST http://localhost:8000/api/v1/webhooks/dispatch?limit=10 \
     -H "X-Admin-Key: ${ADMIN_API_KEY}"
   ```

### Invalid signature

1. Проверить, что secret совпадает
2. Проверить, что payload сериализуется с сортировкой ключей
3. Проверить, что используется HMAC-SHA256

### Delivery stuck in queued

1. Проверить, что cron запускается
2. Проверить, что dispatch endpoint доступен
3. Проверить логи на ошибки

## Summary

Webhooks позволяют клиентам получать уведомления о важных событиях в системе:
- ✅ Tenant-safe (только свои webhooks)
- ✅ HMAC-SHA256 подпись для безопасности
- ✅ Safe payload (без raw данных)
- ✅ Retry с exponential backoff
- ✅ Production-grade reliability

Готово к использованию! 🚀
