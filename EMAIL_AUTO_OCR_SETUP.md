# Email Auto-OCR Setup

## Краткое описание

Email Auto-OCR автоматически запускает OCR для документов, полученных по email, с идемпотентностью и надёжными ретраями.

## Схема job таблицы

**Таблица:** `email_ocr_jobs`

**Поля:**
- `id` (uuid PK)
- `tenant_id` (text, index)
- `document_artifact_id` (uuid, index)
- `idempotency_key` (text, UNIQUE) - SHA256 для дедупа
- `status` (text) - queued|processing|done|failed|dead
- `attempts` (int) - количество попыток
- `max_retries` (int) - максимальное количество попыток
- `next_run_at` (timestamp) - когда можно повторить попытку
- `last_error` (text nullable) - последняя ошибка (без контента)
- `invoice_artifact_id` (text nullable) - созданный invoice artifact
- `created_at` (timestamp)
- `updated_at` (timestamp)

**Индексы:**
- `uq_email_ocr_jobs_idempotency_key` (UNIQUE)
- `idx_email_ocr_jobs_tenant_status_next_run` (composite для эффективного выбора jobs)

## Alembic миграция

**Файл:** `alembic/versions/c5f8e9a1b2d3_add_email_ocr_jobs.py`

**Применение:**
```bash
make db-upgrade
```

## Use cases

### 1. ProcessEmailOcrJobsUseCase

**Файл:** `cyberplat/product/application/process_email_ocr_jobs_use_case.py`

**Ответственность:**
- Выбор jobs готовых к обработке (status in (queued, failed), next_run_at <= now)
- Атомарный "claim" job (защита от гонок)
- Запуск OCR через doc_agent
- Обновление states
- Retry логика с exponential backoff
- Эмиссия событий

**Методы:**
- `execute(limit)` - обработать до N jobs
- `_process_single_job(job)` - обработать один job
- `_calculate_next_run_at(attempts)` - вычислить next_run_at с backoff

### 2. EmailIngestUseCase (обновлён)

**Изменения:**
- Добавлен параметр `email_ocr_job_repo` (опционально)
- Если `EMAIL_AUTO_OCR_ENABLED=1`, создаёт job после успешного ingest
- Вычисляет idempotency_key детерминированно

## Repositories

### EmailOcrJobRepository

**Файл:** `cyberplat/product/infrastructure/repositories_sqlalchemy.py`

**Методы:**
- `create_job(...)` - создать job (или вернуть существующий по idempotency_key)
- `get_job_by_idempotency_key(key)` - получить job по ключу
- `claim_job_for_processing(job_id)` - атомарно "забрать" job
- `mark_job_done(job_id, invoice_artifact_id)` - отметить как выполненный
- `mark_job_failed(job_id, error, next_run_at, attempts)` - отметить как failed
- `mark_job_dead(job_id, error)` - отметить как dead
- `get_jobs_for_processing(limit)` - получить jobs готовые к обработке

## Endpoints

### POST /api/v1/ingest/email/ocr/dispatch

**Описание:** Обработать email OCR jobs из очереди.

**Query параметры:**
- `limit` (int, default: 10) - максимальное количество jobs для обработки

**Response:**
```json
{
  "processed": 5,
  "succeeded": 4,
  "failed": 1,
  "dead": 0,
  "errors": []
}
```

**Использование:**
```bash
# Обработать до 10 jobs
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

## Интеграция в email ingest

После успешного создания document artifact:
1. Вычисляется idempotency_key
2. Создаётся job (или возвращается существующий)
3. Эмитится событие `email.ocr.queued`
4. MVP: best-effort попытка обработать один job синхронно (не блокирует ingest)

## Тесты

**Файл:** `tests/test_email_auto_ocr.py`

**Тесты:**
1. ✅ `test_idempotency_same_email_attachment_creates_single_job`
2. ✅ `test_no_duplicate_invoice_on_webhook_retry`
3. ✅ `test_dispatch_processes_job_and_creates_invoice`
4. ✅ `test_dispatch_retry_on_failure_increments_attempts_and_sets_next_run_at`
5. ✅ `test_dead_after_max_retries`
6. ✅ `test_tenant_isolation_jobs`

**Запуск:**
```bash
pytest tests/test_email_auto_ocr.py -v
```

## Пример ручной проверки

### 1. Включить auto-OCR

```bash
export EMAIL_AUTO_OCR_ENABLED=1
export EMAIL_AUTO_OCR_MAX_RETRIES=5
export EMAIL_AUTO_OCR_RETRY_BASE_SECONDS=10
```

### 2. Отправить email ingest

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@example.com",
    "to": "invoices+tenant-1@yourapp.ai",
    "subject": "Invoice #123",
    "attachments": [
      {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "content": "base64_content",
        "size": 12345
      }
    ]
  }'
```

### 3. Проверить созданный job

```bash
# Через SQL (если есть доступ)
SELECT * FROM email_ocr_jobs WHERE tenant_id = 'tenant-1';
```

### 4. Запустить dispatch

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

### 5. Проверить результат

```bash
# Проверить invoices
curl http://127.0.0.1:8000/api/v1/invoices \
  -H "X-Tenant-ID: tenant-1"
```

### 6. Повторный webhook (проверка идемпотентности)

Отправьте тот же email ingest снова. Job не должен дублироваться, invoice не должен создаваться повторно.

## Настройка cron

```bash
# Добавить в crontab
*/1 * * * * curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

Или через systemd timer / Kubernetes CronJob.

## События

### email.ocr.queued
```json
{
  "event_type": "email.ocr.queued",
  "tenant_id": "tenant-1",
  "artifact_id": "document-artifact-id",
  "payload": {
    "job_id": "job-id",
    "document_artifact_id": "document-artifact-id"
  }
}
```

### email.ocr.started
```json
{
  "event_type": "email.ocr.started",
  "tenant_id": "tenant-1",
  "artifact_id": "document-artifact-id",
  "payload": {
    "job_id": "job-id",
    "attempts": 1
  }
}
```

### email.ocr.completed
```json
{
  "event_type": "email.ocr.completed",
  "tenant_id": "tenant-1",
  "artifact_id": "invoice-artifact-id",
  "payload": {
    "job_id": "job-id",
    "document_artifact_id": "document-artifact-id",
    "invoice_artifact_id": "invoice-artifact-id",
    "attempts": 1
  }
}
```

### email.ocr.failed
```json
{
  "event_type": "email.ocr.failed",
  "tenant_id": "tenant-1",
  "artifact_id": "document-artifact-id",
  "payload": {
    "job_id": "job-id",
    "attempts": 2,
    "error": "OCR failed: ...",
    "status": "failed",
    "next_run_at": "2026-01-14T22:35:00"
  }
}
```

## Безопасность

- ✅ Idempotency key вычисляется детерминированно (без случайности)
- ✅ Tenant isolation (все операции tenant-scoped)
- ✅ Атомарный claim job (защита от гонок)
- ✅ Ограничение длины error_message (без контента)
- ✅ SHA256 вычисляется от bytes, но никогда не логируется

## Статус

✅ **Все задачи выполнены**

- ✅ Миграция создана
- ✅ Модель и репозиторий реализованы
- ✅ Use case реализован
- ✅ Endpoint dispatch создан
- ✅ Интеграция в email ingest
- ✅ Тесты добавлены
- ✅ README обновлён

Готово к использованию! 🚀
