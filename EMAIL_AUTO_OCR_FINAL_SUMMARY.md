# Email Auto-OCR - Final Implementation Summary

## ✅ Все задачи выполнены

Добавлен AUTO-OCR для email ingestion с production-grade идемпотентностью, ретраями и наблюдаемостью.

## 1. Схема job таблицы + Alembic миграция

**Миграция:** `alembic/versions/c5f8e9a1b2d3_add_email_ocr_jobs.py`
**Модель:** `cyberplat/product/infrastructure/models.py` (EmailOcrJob)

**Таблица:** `email_ocr_jobs`

**Ключевые поля:**
- `idempotency_key` (UNIQUE) - SHA256 для дедупа
- `status` - queued|processing|done|failed|dead
- `attempts`, `max_retries` - для ретраев
- `next_run_at` - когда можно повторить попытку
- `invoice_artifact_id` - созданный invoice (после успешного OCR)

**Индексы:**
- UNIQUE на idempotency_key
- Composite index на (tenant_id, status, next_run_at) для эффективного выбора jobs

## 2. Use cases + repositories

### EmailOcrJobRepository

**Файл:** `cyberplat/product/infrastructure/repositories_sqlalchemy.py`

**Ключевые методы:**
- `create_job()` - создать или вернуть существующий по idempotency_key
- `claim_job_for_processing()` - атомарно "забрать" job (защита от гонок)
- `get_jobs_for_processing()` - получить jobs готовые к обработке

### ProcessEmailOcrJobsUseCase

**Файл:** `cyberplat/product/application/process_email_ocr_jobs_use_case.py`

**Логика:**
- Выбирает jobs где status in (queued, failed) AND next_run_at <= now
- Атомарно "забирает" job (status → processing)
- Запускает doc_agent.run() для document_artifact_id
- При успехе: status=done, сохраняет invoice_artifact_id, обновляет states
- При ошибке: attempts++, вычисляет next_run_at с exponential backoff, если attempts >= max_retries → status=dead

**Retry формула:**
```
backoff_seconds = base_seconds * 2^(attempts-1)
backoff_seconds = min(backoff_seconds, max_seconds)
backoff_seconds += jitter (±20%)
```

### Idempotency utilities

**Файл:** `cyberplat/product/infrastructure/idempotency.py`

**Формула idempotency_key:**
```
SHA256(tenant_id + from + to + subject + filename + size + content_sha256)
```

**Важно:** content_sha256 вычисляется от PDF bytes (после base64 decode), но никогда не логируется.

## 3. Новый endpoint dispatch

**Endpoint:** `POST /api/v1/ingest/email/ocr/dispatch`

**Файл:** `app/api/ingest.py`

**Параметры:**
- `limit` (query, default: 10) - максимальное количество jobs

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
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

## 4. Изменения в email ingest

**Файл:** `cyberplat/product/application/email_ingest_use_case.py`

**Изменения:**
- Добавлен параметр `email_ocr_job_repo` (опционально)
- Проверка `EMAIL_AUTO_OCR_ENABLED` env flag
- После успешного создания artifact:
  - Вычисляется idempotency_key
  - Создаётся job (или возвращается существующий)
  - Эмитится событие `email.ocr.queued`

**Файл:** `app/api/ingest.py`

**Изменения:**
- Передача `email_ocr_job_repo` в use case
- MVP: best-effort попытка обработать один job синхронно (не блокирует ingest)

## 5. Тесты

**Файл:** `tests/test_email_auto_ocr.py`

**Тесты (6 тестов):**
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

## 6. README обновление

Добавлен раздел "Email Auto-OCR" в README.md с:
- Описанием feature flags
- Объяснением идемпотентности (idempotency_key)
- Инструкциями по настройке cron
- Описанием событий
- Статусами jobs

## 7. Пример ручной проверки

### Включить auto-OCR

```bash
export EMAIL_AUTO_OCR_ENABLED=1
export EMAIL_AUTO_OCR_MAX_RETRIES=5
export EMAIL_AUTO_OCR_RETRY_BASE_SECONDS=10
export EMAIL_AUTO_OCR_RETRY_MAX_SECONDS=600
```

### Применить миграции

```bash
make db-upgrade
```

### Отправить email ingest

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
        "content": "base64_encoded_content",
        "size": 12345
      }
    ]
  }'
```

**Ожидаемый ответ:**
```json
{
  "status": "success",
  "success": true,
  "tenant_id": "tenant-1",
  "processed_attachments": 1,
  "created_artifacts": ["artifact-id"],
  "auto_ocr_dispatched": true
}
```

### Запустить dispatch

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

**Ожидаемый ответ:**
```json
{
  "processed": 1,
  "succeeded": 1,
  "failed": 0,
  "dead": 0,
  "errors": []
}
```

### Проверить результат

```bash
# Проверить invoices
curl http://127.0.0.1:8000/api/v1/invoices \
  -H "X-Tenant-ID: tenant-1"
```

### Повторный webhook (проверка идемпотентности)

Отправьте тот же email ingest снова (тот же from, to, subject, attachment).

**Ожидаемое поведение:**
- Job не дублируется (возвращается существующий job_id)
- Invoice не создаётся повторно (job уже done)
- Dispatch не обрабатывает job (status=done)

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

## Feature flags

**EMAIL_AUTO_OCR_ENABLED** (default: 0)
- Включает создание jobs после email ingest
- Если 0, jobs не создаются (OCR только вручную через UI)

**EMAIL_AUTO_OCR_MAX_RETRIES** (default: 5)
- Максимальное количество попыток перед dead

**EMAIL_AUTO_OCR_RETRY_BASE_SECONDS** (default: 10)
- Базовое время для exponential backoff

**EMAIL_AUTO_OCR_RETRY_MAX_SECONDS** (default: 600)
- Максимальное время между попытками

## Настройка cron

```bash
# Добавить в crontab (каждую минуту)
*/1 * * * * curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

Или через systemd timer / Kubernetes CronJob.

## Обновление states

При успешном OCR:
- **Document**: `ui_status="extracted"`
- **Invoice**: `ui_status="pending"`, `source_artifact_id=document_artifact_id`

Если doc_agent уже делает это - state консистентен (create_or_update).

## Безопасность

- ✅ Idempotency key вычисляется детерминированно (без случайности)
- ✅ Tenant isolation (все операции tenant-scoped)
- ✅ Атомарный claim job (защита от гонок через UPDATE ... WHERE status=...)
- ✅ Ограничение длины error_message (500 символов, без контента)
- ✅ SHA256 вычисляется от bytes, но никогда не логируется

## Интеграция

Не ломает существующий pipeline:
- Использует те же сервисы (doc_agent, artifact_service, event_service)
- Создаёт те же artifacts и states
- Эмитит те же события (artifact.created)
- OCR запускается через тот же doc_agent.run()

## Статус

✅ **Все задачи выполнены**

- ✅ Миграция создана и протестирована
- ✅ Модель и репозиторий реализованы
- ✅ Use case реализован с retry логикой
- ✅ Endpoint dispatch создан
- ✅ Интеграция в email ingest
- ✅ Тесты добавлены (6 тестов)
- ✅ README обновлён
- ✅ Документация создана

Готово к использованию! 🚀
