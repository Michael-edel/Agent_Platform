# Email Ingestion Setup

## Краткое описание

Email Ingestion позволяет клиентам отправлять PDF счета по email и автоматически создавать document artifacts в системе.

**Выбранный способ tenant resolution:**
- **Из email адреса получателя**: `invoices+tenant-1@yourapp.ai` → tenant_id = `tenant-1`
- Формат: `{prefix}+{tenant_id}@{domain}`
- Извлекается часть после `+` и до `@`

**Альтернативные способы (задокументированы, но не реализованы):**
- Custom header от email provider (если поддерживается)
- Fallback: reject email с событием `email.ingest.failed`

## Новые/изменённые файлы

### Созданные:
- `cyberplat/product/application/email_ingest_use_case.py` - Use case для email ingestion
- `cyberplat/product/infrastructure/email_parser.py` - Парсер email payload (provider-agnostic)
- `app/api/ingest.py` - API endpoint для email ingestion
- `tests/test_email_ingestion.py` - Тесты для email ingestion
- `EMAIL_INGESTION_SETUP.md` - Этот файл

### Изменённые:
- `app/main.py` - Добавлен router для email ingestion, сохранение сервисов в app.state
- `README.md` - Добавлен раздел "Email Ingestion"

## Реализация

### 1. EmailIngestUseCase

**Файл:** `cyberplat/product/application/email_ingest_use_case.py`

**Ответственность:**
- Валидация email payload
- Определение tenant_id из email адреса
- Обработка PDF вложений
- Создание artifacts и states
- Эмиссия событий

**Методы:**
- `execute(email_payload)` - главный метод обработки
- `_resolve_tenant_id(email_to)` - определение tenant_id
- `_process_pdf_attachment(...)` - обработка одного PDF вложения

**Валидации:**
- Максимальный размер вложения: 10MB
- Только `application/pdf`
- Обязательный tenant_id

### 2. EmailPayloadParser

**Файл:** `cyberplat/product/infrastructure/email_parser.py`

**Ответственность:**
- Парсинг payload от различных email providers
- Provider-agnostic интерфейс

**Поддерживаемые форматы:**
- SendGrid Inbound Parse
- Mailgun Inbound
- AWS SES (через SNS)
- Generic format (fallback)

### 3. API Endpoint

**Файл:** `app/api/ingest.py`

**Endpoint:** `POST /api/v1/ingest/email`

**Request:**
```json
{
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
}
```

**Response (success):**
```json
{
  "status": "success",
  "success": true,
  "tenant_id": "tenant-1",
  "processed_attachments": 1,
  "created_artifacts": ["artifact-id-1"],
  "errors": []
}
```

**Response (error):**
```json
{
  "status": "error",
  "success": false,
  "tenant_id": null,
  "processed_attachments": 0,
  "created_artifacts": [],
  "errors": ["Could not resolve tenant_id from email address"]
}
```

## Тесты

**Файл:** `tests/test_email_ingestion.py`

**Тесты:**
1. ✅ `test_email_ingest_success_single_pdf` - успешная обработка одного PDF
2. ✅ `test_email_ingest_multiple_pdfs` - обработка нескольких PDF
3. ✅ `test_email_ingest_invalid_tenant` - невалидный tenant_id
4. ✅ `test_email_ingest_non_pdf_attachment` - не-PDF вложение
5. ✅ `test_email_ingest_creates_artifact_and_state` - создание artifact и state
6. ✅ `test_tenant_isolation_email_ingest` - tenant isolation
7. ✅ `test_parse_sendgrid_format` - парсинг SendGrid формата
8. ✅ `test_parse_mailgun_format` - парсинг Mailgun формата
9. ✅ `test_parse_generic_format` - парсинг generic формата

**Запуск тестов:**
```bash
pytest tests/test_email_ingestion.py -v
```

## Проверка вручную

### 1. Подготовка тестового PDF

Создайте минимальный PDF файл или используйте существующий.

### 2. Кодирование в base64

```bash
# Linux/Mac
base64 -i invoice.pdf

# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("invoice.pdf"))
```

### 3. Отправка curl запроса

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@example.com",
    "to": "invoices+tenant-1@yourapp.ai",
    "subject": "Test Invoice",
    "attachments": [
      {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "content": "JVBERi0xLjQKJeLjz9MKMyAwIG9iago8PAovVHlwZSAvQ2F0YWxvZwovUGFnZXMgMSAwIFIKPj4KZW5kb2JqCjEgMCBvYmoKPDwKL1R5cGUgL1BhZ2VzCi9LaWRzIFsyIDAgUl0KL0NvdW50IDEKPD4KZW5kb2JqCjIgMCBvYmoKPDwKL1R5cGUgL1BhZ2UKL01lZGlhQm94IFswIDAgNjEyIDc5Ml0KL1BhcmVudCAxIDAgUgo+PgplbmRvYmoKeHJlZgowIDMKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTQgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSAzCi9Sb290IDEgMCBSCj4+CnN0YXJ0eHJlZgo3NwolJUVPRgo=",
        "size": 123
      }
    ]
  }'
```

### 4. Проверка результата

**Успешный ответ:**
```json
{
  "status": "success",
  "success": true,
  "tenant_id": "tenant-1",
  "processed_attachments": 1,
  "created_artifacts": ["<artifact-id>"],
  "errors": []
}
```

**Проверка созданного artifact:**
```bash
curl http://127.0.0.1:8000/api/v1/documents \
  -H "X-Tenant-ID: tenant-1"
```

### 5. Пример с ошибкой (невалидный tenant)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/email \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@example.com",
    "to": "invoices@yourapp.ai",
    "subject": "Test Invoice",
    "attachments": [
      {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "content": "base64_content",
        "size": 123
      }
    ]
  }'
```

**Ожидаемый ответ (400):**
```json
{
  "detail": "Could not resolve tenant_id from email address: invoices@yourapp.ai"
}
```

## События

### email.received

Эмитируется при успешном получении email:
```json
{
  "event_type": "email.received",
  "tenant_id": "tenant-1",
  "payload": {
    "from": "sender@example.com",
    "to": "invoices+tenant-1@yourapp.ai",
    "subject": "Invoice #123",
    "attachments_count": 1,
    "pdf_attachments_count": 1,
    "processed_count": 1,
    "errors_count": 0
  }
}
```

### email.ingest.failed

Эмитируется при ошибке обработки (невалидный tenant, нет вложений, etc.):
```json
{
  "event_type": "email.ingest.failed",
  "tenant_id": null,
  "payload": {
    "from": "sender@example.com",
    "to": "invoices@yourapp.ai",
    "subject": "Invoice",
    "error": "Could not resolve tenant_id from email address: invoices@yourapp.ai"
  }
}
```

## Безопасность

- ✅ Максимальный размер вложения: 10MB
- ✅ Только `application/pdf` вложения
- ✅ Строгая валидация tenant_id (не пустой, не "string")
- ✅ Логирование всех отклонённых попыток
- ✅ События для аудита

## Интеграция с существующим pipeline

Email ingestion **не ломает** существующий pipeline:
- Создаёт те же artifacts (kind="document", source="email")
- Использует тот же механизм сохранения файлов
- Создаёт те же artifact_states (ui_status="uploaded")
- Эмитит те же события (artifact.created)
- OCR запускается вручную через UI (как и для upload)

## Следующие шаги (опционально)

1. **Автоматический OCR**: Добавить настройку для автоматического запуска OCR после email ingestion
2. **Webhook signature verification**: Проверка подписи от email provider
3. **Rate limiting**: Ограничение количества email в минуту на tenant
4. **Email notifications**: Уведомления клиенту об успешной обработке
5. **Retry mechanism**: Повторная обработка при временных ошибках
