# Pilot Demo v1 — один скрипт “бухгалтер → директор” (локально)

Цель: одной командой воспроизвести цепочку “бухгалтер → директор” **без UI**: загрузка PDF → (best-effort) создание/submit платежа бухгалтером → approve/reject директором.

## Предусловия

Запущен локальный стенд:

```bash
docker compose up -d
```

## Запуск (PowerShell 7)

Запуск “из коробки” (TenantId и PDF по умолчанию):

```powershell
pwsh -File .\scripts\pilot-demo-v1.ps1
```

Одобрить директором с явными параметрами:

```powershell
pwsh -File .\scripts\pilot-demo-v1.ps1 -TenantId "tenant-123" -PdfPath "C:\path\to\invoice.pdf" -DirectorAction approve
```

Отклонить директором:

```powershell
pwsh -File .\scripts\pilot-demo-v1.ps1 -DirectorAction reject -Reason "Отклонено для теста"
```

Скрипт сохраняет лог бухгалтерского прогона в `logs/pilot_demo_*.log` и печатает `PaymentId` (если payment API доступен).

## Значения по умолчанию

- `TenantId`: `demo-tenant`
- `PdfPath`: `demo/demo-invoice.pdf` (файл можно пересоздать командой `python scripts/gen-demo-invoice-pdf.py`)

## Поведение по PaymentId

- Если payment API доступен — в логе бухгалтера будет строка `PaymentId: <id>` и pilot demo запустит шаг директора.
- Если endpoint недоступен (404/405) — в логе будет строка `PaymentId не получен: endpoint недоступен ...`, а шаг директора будет пропущен.
- Если backend вернул 5xx/таймаут/битый JSON на create payment — demo **падает** (это честная поломка пилота).

## Partial success mode

Если payment API отсутствует/недоступен (HTTP 404/405), `scripts/pilot-demo-v1.ps1`:
- **не падает**
- печатает итог **PARTIAL SUCCESS** (жёлтый)
- завершает выполнение с **exit code 0**

Если есть 5xx/таймаут/битый JSON на create payment — demo падает с **exit code 1**.

## Где смотреть результат

- Swagger UI: `http://localhost:8000/docs`
- Документы:
  - `GET /api/v1/documents` (с `X-Tenant-ID`)
  - `GET /api/v1/documents/{id}` (с `X-Tenant-ID`)
- Платежи (если доступны эндпоинты):
  - `GET /api/v1/payments/orders/{order_id}` (с `X-Tenant-ID`)
- Логи:

```bash
docker compose logs -f app
docker compose logs -f worker
```

