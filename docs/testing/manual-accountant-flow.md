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
pwsh -File .\scripts\manual-accountant-flow.ps1
# или явно:
pwsh -File .\scripts\manual-accountant-flow.ps1 -TenantId "tenant-123" -PdfPath "C:\path\to\invoice.pdf"
```

Если хотите прогнать цепочку “бухгалтер → директор” одной командой — используйте `scripts/pilot-demo-v1.ps1` (см. `docs/testing/pilot-demo-v1.md`).

## Значения по умолчанию

- `TenantId`: `demo-tenant`
- `PdfPath`: `demo/demo-invoice.pdf` (файл можно пересоздать командой `python scripts/gen-demo-invoice-pdf.py`)

## Что должно получиться

- Скрипт проверяет `GET http://localhost:8000/health`.
- Загружает PDF в `POST http://localhost:8000/documents/upload` (multipart) и печатает `id=<artifact_id>`.
- Проверяет, что документ виден:
  - `GET /api/v1/documents?limit=20` (с `X-Tenant-ID`)
  - `GET /api/v1/documents/{id}` (с `X-Tenant-ID`)
- Дальше **best-effort** (без зависимости от OpenAPI):
  - пытается создать `PaymentOrder` (`POST /api/v1/payments/orders`)
  - если получилось — пытается отправить на согласование (`POST /api/v1/payments/orders/{id}/submit`)
  - если endpoint недоступен (404/405) — `PaymentId` не будет, шаг будет отмечен как пропущенный
  - если 5xx/таймаут/битый JSON — скрипт **падает** (это поломка пилота)

## Передать на согласование директору

После создания `PaymentOrder` бухгалтеру нужно передать директору:
- `PaymentId` (это `payment_id` в ответе `POST /api/v1/payments/orders`).
- Скопируйте `PaymentId` и передайте согласующему.

Примечание: если в ответе создания есть `reason`, скрипт печатает это как `PaymentCreateNote: ...` (платёж создан, но есть диагностическое сообщение).

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

