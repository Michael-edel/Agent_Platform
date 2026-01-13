# Agent Platform

Multi-tenant платформа для обработки документов с поддержкой агентов (doc_agent, payment_agent) и автоматическим экспортом в S3/MinIO.

## Возможности

- **Multi-tenant архитектура**: Строгая изоляция данных по tenant_id
- **Агенты**: 
  - `doc_agent`: Распознавание документов через OCR пайплайн
  - `payment_agent`: Создание payment артефактов из invoice артефактов
- **События**: Система событий (artifact.created, document.extracted, payment.ready, payment.invalid)
- **S3 экспорт**: Автоматический экспорт артефактов и событий в S3/MinIO (опционально)

## Установка

```bash
# Установка зависимостей
pip install -r requirements.txt
```

## Конфигурация

### Billing и Webhooks (Payment Integration)

#### Переменные окружения для billing:

```env
# Billing Configuration
BILLING_ENABLED=1
BILLING_DEFAULT_PLAN=plan_free

# Stripe Webhook
STRIPE_WEBHOOK_SECRET=whsec_...

# Kaspi Webhook (опционально)
KASPI_WEBHOOK_SECRET=...

# YooMoney Webhook (опционально)
YOOMONEY_WEBHOOK_SECRET=...
```

#### Планы по умолчанию:

- **plan_free**: Бесплатный план (ограниченные квоты)
- **plan_pro**: Pro план ($29.99/месяц, высокие квоты)
- **plan_enterprise**: Enterprise план ($99.99/месяц, unlimited квоты)

#### Webhook endpoints:

- `POST /api/v1/billing/webhook/stripe` - Stripe webhook (обязательно требует `STRIPE_WEBHOOK_SECRET`)
- `POST /api/v1/billing/webhook/kaspi` - Kaspi webhook (каркас)
- `POST /api/v1/billing/webhook/yoomoney` - YooMoney webhook (каркас)

#### Как работает автоматическое снятие лимитов:

1. При создании Checkout Session / Subscription в Stripe добавляйте metadata:
   ```json
   {
     "metadata": {
       "tenant_id": "tenant-123",
       "plan_id": "plan_pro"
     }
   }
   ```

2. При успешной оплате Stripe отправляет webhook `invoice.paid`

3. Система автоматически:
   - Обновляет `tenant_subscriptions`
   - Применяет квоты из плана к `billing_rates.monthly_quota`
   - Снимает лимиты для tenant

4. При отмене подписки (`subscription.canceled`) автоматически откатывает на `plan_free`

#### Пример Stripe event payload:

См. `examples/stripe_webhook_event_example.json` для примера события `invoice.paid`.

Важно: при создании Checkout Session или Subscription в Stripe обязательно добавляйте metadata:
```json
{
  "metadata": {
    "tenant_id": "tenant-123",
    "plan_id": "plan_pro"
  }
}
```

#### Локальная проверка webhook (PowerShell):

```powershell
$base = "http://127.0.0.1:8000"
$headers = @{
    "Stripe-Signature" = "t=1234567890,v1=test_signature"
    "Content-Type" = "application/json"
}

# Пример события invoice.paid
$event = @{
    id = "evt_test123"
    type = "invoice.paid"
    data = @{
        object = @{
            id = "in_test"
            customer = "cus_test"
            subscription = "sub_test"
            period_start = [int](Get-Date).AddDays(-30).ToUniversalTime().Subtract((Get-Date "1970-01-01")).TotalSeconds
            period_end = [int](Get-Date).ToUniversalTime().Subtract((Get-Date "1970-01-01")).TotalSeconds
            metadata = @{
                tenant_id = "tenant-123"
                plan_id = "plan_pro"
            }
        }
    }
} | ConvertTo-Json -Depth 10

# Отправка webhook (для тестирования подпись можно отключить, установив STRIPE_WEBHOOK_SECRET="")
Invoke-RestMethod "$base/api/v1/billing/webhook/stripe" -Method POST -Headers $headers -Body $event
```

#### Примеры использования Billing API:

```powershell
$base = "http://127.0.0.1:8000"
$tenant = "tenant-123"
$headers = @{ "X-Tenant-ID" = $tenant }
$period = (Get-Date).ToString("yyyy-MM")

# Получить статус квот по всем метрикам
Invoke-RestMethod "$base/api/v1/billing/quota?period=$period" -Headers $headers | ConvertTo-Json -Depth 50

# Получить тарифы (tenant-specific + default)
Invoke-RestMethod "$base/api/v1/billing/rates" -Headers $headers | ConvertTo-Json -Depth 50

# Получить использование за период
Invoke-RestMethod "$base/api/v1/billing/usage?period=$period" -Headers $headers | ConvertTo-Json -Depth 50

# Получить invoice за период
Invoke-RestMethod "$base/api/v1/billing/invoice?period=$period" -Headers $headers | ConvertTo-Json -Depth 50

# Admin: Сброс usage (требует BILLING_ADMIN_KEY в ENV)
$adminHeaders = @{ 
    "X-Admin-Key" = "dev-admin-key"
    "Content-Type" = "application/json"
}
$resetBody = @{
    tenant_id = $tenant
    period = $period
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$base/api/v1/billing/admin/reset-usage" -Headers $adminHeaders -Body $resetBody | ConvertTo-Json

# Получить Billing Portal (план, подписка, invoice, quota, upgrade/manage URLs)
Invoke-RestMethod "$base/api/v1/billing/portal?period=$period" -Headers $headers | ConvertTo-Json -Depth 80
```

**Примечание**: Для использования admin endpoint `reset-usage` необходимо установить переменную окружения `BILLING_ADMIN_KEY`. Если ключ не установлен, endpoint вернет ошибку 501.

**Billing Portal** (`GET /api/v1/billing/portal?period=YYYY-MM`) возвращает:
- Текущий план tenant'а (plan_id, plan_name)
- Статус подписки (active/canceled/none)
- Invoice за период
- Quota статус за период
- Upgrade URL (для апгрейда плана через Stripe)
- Manage URL (для управления подпиской через Stripe Customer Portal)

Если `STRIPE_ENABLED != "1"`, то `upgrade_url` и `manage_url` будут `null`.

### S3/MinIO экспорт (опционально)

Экспорт в S3/MinIO включается через переменные окружения. Если `S3_EXPORT_ENABLED` не установлен или равен `0`, экспорт отключен.

#### Пример .env для MinIO:

```env
# S3 Export Configuration
S3_EXPORT_ENABLED=1
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=agent-platform
S3_REGION=us-east-1
S3_PREFIX=prod
```

#### Переменные окружения:

- `S3_EXPORT_ENABLED`: `1` для включения, `0` или не установлено для отключения
- `S3_ENDPOINT_URL`: URL эндпоинта (для MinIO: `http://127.0.0.1:9000`, для AWS S3 - не указывать)
- `S3_ACCESS_KEY`: Access key для S3
- `S3_SECRET_KEY`: Secret key для S3
- `S3_BUCKET`: Имя bucket
- `S3_REGION`: Регион (по умолчанию `us-east-1`)
- `S3_PREFIX`: Префикс для ключей (например, `prod`, `dev`)

### Docker Compose для MinIO (опционально)

```yaml
version: '3.8'

services:
  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data

volumes:
  minio_data:
```

Запуск:

```bash
docker-compose up -d
```

Создание bucket через MinIO Console (http://localhost:9001) или через AWS CLI:

```bash
aws --endpoint-url=http://localhost:9000 s3 mb s3://agent-platform
```

## Структура экспорта в S3

При включенном экспорте данные автоматически выгружаются в S3 по следующей структуре:

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

### Примеры ключей:

- `prod/tenant-123/documents/abc-123/abc-123_test.pdf` - PDF документа
- `prod/tenant-123/invoices/def-456.json` - Invoice JSON
- `prod/tenant-123/payments/ghi-789.json` - Payment JSON
- `prod/tenant-123/events/def-456/event-001.json` - Событие

## API Endpoints

### Проверка статуса экспорта

```bash
GET /api/v1/export/status
```

Ответ:

```json
{
  "enabled": true,
  "bucket": "agent-platform",
  "endpoint_url": "http://127.0.0.1:9000",
  "prefix": "prod",
  "region": "us-east-1"
}
```

### Основные endpoints

- `POST /documents/upload` - Загрузка документа
- `POST /agents/doc_agent/run` - Запуск doc_agent
- `POST /api/v1/invoices/{invoice_id}/prepare-payment` - Подготовка payment из invoice
- `GET /api/v1/artifacts/{artifact_id}` - Получить артефакт
- `GET /api/v1/artifacts/{artifact_id}/events` - Получить события артефакта

## Типы событий и экспорт

| Событие | Условие экспорта | Что экспортируется |
|---------|------------------|-------------------|
| `artifact.created` | `kind="document"` | PDF файл |
| `document.extracted` | `kind="invoice"` | Invoice JSON |
| `payment.ready` | `kind="payment"` | Payment JSON |
| `payment.invalid` | `kind="payment"` | Payment JSON |
| Любое событие | Всегда | Event JSON |

## Запуск

```bash
# Запуск сервера
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Тестирование

```bash
# Запуск тестов
pytest tests/ -v
```

## Безопасность

- **Tenant isolation**: Все экспортируемые данные изолированы по `tenant_id`
- **Валидация tenant_id**: Строгая валидация предотвращает cross-tenant доступ
- **Секреты**: `S3_ACCESS_KEY` и `S3_SECRET_KEY` не возвращаются в API ответах

## Лицензия

MIT