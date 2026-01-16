# Pilot Demo v1 — один скрипт “бухгалтер → директор” (локально)

Цель: одной командой воспроизвести цепочку “бухгалтер → директор” **без UI**: загрузка PDF → (best-effort) создание/submit платежа бухгалтером → approve/reject директором.

## Предусловия

Запущен локальный стенд:

```bash
docker compose up -d
```

## Запуск (PowerShell 7)

Одобрить директором:

```powershell
pwsh -File .\scripts\pilot-demo-v1.ps1 -PdfPath "C:\path\to\invoice.pdf" -DirectorAction approve
```

Отклонить директором:

```powershell
pwsh -File .\scripts\pilot-demo-v1.ps1 -PdfPath "C:\path\to\invoice.pdf" -DirectorAction reject -Reason "Отклонено для теста"
```

Скрипт сохраняет лог бухгалтерского прогона в `logs/pilot_demo_*.log` и печатает `PaymentId`.

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

