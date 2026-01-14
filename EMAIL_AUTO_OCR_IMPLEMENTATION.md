# Email Auto-OCR Implementation Summary

## ✅ Реализация завершена

Добавлен AUTO-OCR для email ingestion с production-grade идемпотентностью, ретраями и наблюдаемостью.

## 1. Схема job таблицы + Alembic миграция

**Миграция:** `alembic/versions/c5f8e9a1b2d3_add_email_ocr_jobs.py`

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
- `idx_email_ocr_jobs_tenant_status_next_run` (composite)

**Применение:**
```bash
make db-upgrade
```

## 2. Use cases + repositories

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

### ProcessEmailOcrJobsUseCase

**Файл:** `cyberplat/product/application/process_email_ocr_jobs_use_case.py`

**Ответственность:**
- Выбор jobs готовых к обработке
- Атомарный claim job
- Запуск OCR через doc_agent
- Обновление states
- Retry логика с exponential backoff
- Эмиссия событий

**Методы:**
- `execute(limit)` - обработать до N jobs
- `_process_single_job(job)` - обработать один job
- `_calculate_next_run_at(attempts)` - вычислить next_run_at с backoff

### Idempotency utilities

**Файл:** `cyberplat/product/infrastructure/idempotency.py`

**Функции:**
- `compute_email_ocr_idempotency_key(...)` - вычислить idempotency_key
- `compute_attachment_sha256(bytes)` - вычислить SHA256 от PDF bytes

**Формула idempotency_key:**
```
SHA256(tenant_id + from + to + subject + filename + size + content_sha256)
```

## 3. Новый endpoint dispatch

**Endpoint:** `POST /api/v1/ingest/email/ocr/dispatch`

**Query параметры:**
- `limit` (int, default: 10) - максимальное количество jobs

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
- Проверка feature flag `EMAIL_AUTO_OCR_ENABLED`
- После успешного создания artifact создаётся job
- Вычисление idempotency_key детерминированно

**Файл:** `app/api/ingest.py`

**Изменения:**
- Передача `email_ocr_job_repo` в use case
- MVP: best-effort попытка обработать один job синхронно (не блокирует ingest)

## 5. Тесты

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

## 6. README обновление

Добавлен раздел "Email Auto-OCR" в README.md с:
- Описанием как включить
- Объяснением идемпотентности
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
        "content": "base64_content",
        "size": 12345
      }
    ]
  }'
```

### Запустить dispatch

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email/ocr/dispatch?limit=10
```

### Проверить результат

```bash
# Проверить invoices
curl http://127.0.0.1:8000/api/v1/invoices \
  -H "X-Tenant-ID: tenant-1"
```

### Повторный webhook (проверка идемпотентности)

Отправьте тот же email ingest снова. Job не должен дублироваться, invoice не должен создаваться повторно.

## События

### email.ocr.queued
Эмитируется при создании job:
- `payload`: job_id, document_artifact_id

### email.ocr.started
Эмитируется при начале обработки:
- `payload`: job_id, attempts

### email.ocr.completed
Эмитируется при успешном завершении:
- `payload`: job_id, document_artifact_id, invoice_artifact_id, attempts

### email.ocr.failed
Эмитируется при ошибке:
- `payload`: job_id, attempts, error, status ("failed"|"dead"), next_run_at

## Безопасность

- ✅ Idempotency key вычисляется детерминированно
- ✅ Tenant isolation (все операции tenant-scoped)
- ✅ Атомарный claim job (защита от гонок)
- ✅ Ограничение длины error_message (без контента)
- ✅ SHA256 вычисляется от bytes, но никогда не логируется

## Интеграция

Не ломает существующий pipeline:
- Использует те же сервисы (doc_agent, artifact_service, event_service)
- Создаёт те же artifacts и states
- Эмитит те же события (artifact.created)
- OCR запускается через тот же doc_agent.run()

## Статус

✅ **Все задачи выполнены**

- ✅ Миграция создана
- ✅ Модель и репозиторий реализованы
- ✅ Use case реализован
- ✅ Endpoint dispatch создан
- ✅ Интеграция в email ingest
- ✅ Тесты добавлены (6 тестов)
- ✅ README обновлён
- ✅ Документация создана

Готово к использованию! 🚀
