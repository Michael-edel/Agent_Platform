# Inbox Implementation Summary

## ✅ Все задачи выполнены

Добавлен "Inbox" для клиента: список последних входящих писем/вложений и их статусы обработки (queued/processing/done/failed/dead).

## 1. Backend: Миграция для метаданных

**Миграция:** `alembic/versions/d7e8f9a0b1c2_add_email_metadata_to_ocr_jobs.py`

**Изменения:**
- Добавлены колонки в `email_ocr_jobs`:
  - `email_from` (text)
  - `email_to` (text)
  - `email_subject` (text)
  - `attachment_filename` (text)
  - `attachment_size` (integer)
- Добавлен индекс `idx_email_ocr_jobs_tenant_created` для эффективной сортировки

**Модель:** Обновлён `EmailOcrJob` в `cyberplat/product/infrastructure/models.py`

## 2. Backend: Обновление репозитория

**Файл:** `cyberplat/product/infrastructure/repositories_sqlalchemy.py`

**Изменения:**
- `create_job()` теперь принимает метаданные email (email_from, email_to, email_subject, attachment_filename, attachment_size)
- Добавлен метод `list_inbox_emails()` для получения списка jobs для inbox с cursor pagination
- `_job_to_dict()` теперь включает метаданные email

**Интерфейс:** Обновлён `EmailOcrJobRepository` в `cyberplat/product/domain/interfaces.py`

## 3. Backend: Обновление EmailIngestUseCase

**Файл:** `cyberplat/product/application/email_ingest_use_case.py`

**Изменения:**
- При создании job теперь передаются метаданные email для сохранения в БД

## 4. Backend: Endpoint Inbox

**Файл:** `app/api/inbox.py`

**Endpoint:** `GET /api/v1/inbox/emails`

**Параметры:**
- `limit` (query, default: 50) - максимальное количество записей
- `cursor` (query, optional) - cursor для pagination (формат: "timestamp|job-id")
- `X-Tenant-ID` (header, required) - ID тенанта

**Response:**
```json
{
  "items": [
    {
      "received_at": "2026-01-14T23:00:00",
      "from_email": "sender@example.com",
      "to_email": "invoices+tenant-1@yourapp.ai",
      "subject": "Invoice #123",
      "attachment_filename": "invoice.pdf",
      "attachment_size": 12345,
      "document_artifact_id": "doc-id",
      "job_id": "job-id",
      "job_status": "done",
      "attempts": 1,
      "next_run_at": null,
      "invoice_artifact_id": "invoice-id",
      "error": null
    }
  ],
  "cursor": "2026-01-14T23:00:00|job-id"
}
```

**DTO:**
- `InboxEmailItemDTO` - для одного email item
- `InboxEmailListResponse` - для списка с cursor

**Интеграция:** Добавлен router в `app/main.py`

## 5. Backend: Тесты

**Файл:** `tests/test_inbox_api.py`

**Тесты:**
1. ✅ `test_inbox_lists_only_tenant_jobs` - tenant isolation
2. ✅ `test_inbox_contains_document_and_invoice_links` - ссылки на document/invoice
3. ✅ `test_inbox_shows_dead_and_failed_status` - статусы failed/dead
4. ✅ `test_inbox_ordering_latest_first` - сортировка по дате (последние первыми)

**Запуск:**
```bash
pytest tests/test_inbox_api.py -v
```

## 6. Frontend: API Client

**Файл:** `frontend/lib/api.ts`

**Добавлено:**
- Интерфейсы `InboxEmailItem` и `InboxEmailListResponse`
- Функция `listInboxEmails(params?)` для получения списка inbox emails

## 7. Frontend: Inbox Page

**Файл:** `frontend/app/inbox/page.tsx`

**Функциональность:**
- Таблица с колонками: Received, From, Subject, Attachment, Status, Attempts, Links
- Status badges с цветами:
  - `queued` - серый (bg-gray-100)
  - `processing` - синий (bg-blue-100)
  - `done` - зелёный (bg-green-100)
  - `failed` - жёлтый (bg-yellow-100)
  - `dead` - красный (bg-red-100)
- Ссылки на Document и Invoice (если созданы)
- Кнопка Refresh для обновления списка
- Форматирование даты и размера файла
- Отображение ошибок (если есть)

## 8. Frontend: Навигация

**Файл:** `frontend/components/Nav.tsx`

**Изменения:**
- Добавлен пункт "Inbox" в навигацию (после Settings, перед Documents)

## 9. Документация

**Backend README:** Добавлен раздел "Inbox (Email Status)" с описанием endpoint, статусов и примером использования.

**Frontend README:** Обновлён:
- Добавлен Inbox в Features
- Добавлен шаг "Email Ingestion" в Typical Flow
- Добавлен endpoint `/api/v1/inbox/emails` в API Endpoints
- Добавлена страница `/app/inbox` в структуру проекта

## Пример использования

### Backend

```bash
# Получить inbox emails
curl http://127.0.0.1:8000/api/v1/inbox/emails?limit=50 \
  -H "X-Tenant-ID: tenant-1"
```

### Frontend

1. Отправить email с PDF на `invoices+tenant-1@yourapp.ai`
2. Открыть `/app/inbox`
3. Увидеть статус обработки (queued → processing → done)
4. Кликнуть на "Document" или "Invoice" для просмотра

## Безопасность

- ✅ Tenant-safe: все запросы фильтруются по `X-Tenant-ID`
- ✅ Не требует `X-Admin-Key` (UI endpoint)
- ✅ Не тащит raw base64/контент вложений
- ✅ Только метаданные (from, to, subject, filename, size)

## Статусы

- `queued` - готов к обработке
- `processing` - обрабатывается
- `done` - успешно завершён (invoice создан)
- `failed` - ошибка, запланирован retry
- `dead` - превышен max_retries

## Интеграция

Не ломает существующие API:
- Использует существующую таблицу `email_ocr_jobs`
- Расширяет её метаданными (backward compatible, nullable колонки)
- Использует те же репозитории и use cases

## Статус

✅ **Все задачи выполнены**

- ✅ Миграция создана
- ✅ Модель обновлена
- ✅ Репозиторий обновлён
- ✅ EmailIngestUseCase обновлён
- ✅ Endpoint создан
- ✅ Тесты добавлены (4 теста)
- ✅ Frontend страница создана
- ✅ Навигация обновлена
- ✅ API client обновлён
- ✅ README обновлён (backend + frontend)

Готово к использованию! 🚀
