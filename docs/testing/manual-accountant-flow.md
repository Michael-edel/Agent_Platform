# Ручной бухгалтерский прогон (локально)

Цель: выполнить управляемый “ручной прогон” через HTTP к локальному API без внешних сервисов и без OCR: загрузить PDF, получить ID документа, проверить что он виден через UI API, и (best-effort) пройти минимальный платёжный контур.

## Предусловия

- Запущен локальный стенд:

```bash
docker compose up -d
```

- Есть реальный PDF (счёт/инвойс) на диске.

## Запуск (PowerShell 7)

```powershell
pwsh -File .\scripts\manual-accountant-flow.ps1 -PdfPath "C:\path\to\invoice.pdf"
```

## Что должно получиться

- Скрипт проверяет `GET http://localhost:8000/health`.
- Загружает PDF в `POST http://localhost:8000/documents/upload` (multipart) и печатает `id=<artifact_id>`.
- Проверяет, что документ виден:
  - `GET /api/v1/documents?limit=20` (с `X-Tenant-ID`)
  - `GET /api/v1/documents/{id}` (с `X-Tenant-ID`)
- Дальше **best-effort** (если эндпоинты доступны в OpenAPI):
  - создаёт `PaymentOrder`
  - отправляет на согласование
  - пытается одобрить
  - пытается экспортировать (csv)
  Если эндпоинта нет или контракт не подходит — шаг печатается как “Пропущено”.

## Передать на согласование директору

После создания `PaymentOrder` бухгалтеру нужно передать директору:
- `PaymentId` (это `id` платёжного поручения в ответе create payment order).

Пример запуска скрипта согласующего:

```powershell
pwsh -File .\scripts\manual-approver-flow.ps1 -PaymentId "<order_id>" -Action approve
# или отклонить:
pwsh -File .\scripts\manual-approver-flow.ps1 -PaymentId "<order_id>" -Action reject -Reason "Недостаточно оснований"
```

## Где смотреть результат

- Swagger UI: `http://localhost:8000/docs`
- Документы (список/детали):
  - `GET /api/v1/documents`
  - `GET /api/v1/documents/{id}`
- Логи:

```bash
docker compose logs -f app
docker compose logs -f worker
```

