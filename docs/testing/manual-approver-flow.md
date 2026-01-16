# Ручной прогон согласующего (директор) — approve/reject

Цель: вручную подтвердить или отклонить платёжное поручение **без UI**, через HTTP к локальному API. Скрипт работает **best-effort**: если эндпоинты отсутствуют в OpenAPI, шаги будут пропущены с понятным сообщением.

## Предусловия

- Запущен локальный стенд:

```bash
docker compose up -d
```

- У вас есть `PaymentId` (ID платёжного поручения). Его выдаёт скрипт бухгалтера при создании `PaymentOrder`.

## Запуск (PowerShell 7)

Одобрить:

```powershell
pwsh -File .\scripts\manual-approver-flow.ps1 -PaymentId "<order_id>" -Action approve
# или с явным tenant:
pwsh -File .\scripts\manual-approver-flow.ps1 -TenantId "tenant-123" -PaymentId "<order_id>" -Action approve
# если включён AUTH_ENABLED=true:
pwsh -File .\scripts\manual-approver-flow.ps1 -TenantId "tenant-123" -PaymentId "<order_id>" -Action approve -AuthToken "<token>"
```

Отклонить:

```powershell
pwsh -File .\scripts\manual-approver-flow.ps1 -PaymentId "<order_id>" -Action reject -Reason "Недостаточно оснований"
```

Если хотите прогнать цепочку “бухгалтер → директор” одной командой — используйте `scripts/pilot-demo-v1.ps1` (см. `docs/testing/pilot-demo-v1.md`).

## Значения по умолчанию

- `TenantId`: `demo-tenant`

## Что делает скрипт

- Проверяет доступность сервиса: `GET http://localhost:8000/health`
- Загружает `GET /openapi.json` и проверяет наличие путей:
  - `POST /api/v1/payments/orders/{order_id}/approve`
  - `POST /api/v1/payments/orders/{order_id}/reject`
  - `GET /api/v1/payments/orders/{order_id}`
- Выполняет `approve` или `reject` от роли **director** (в request body).
- Проверяет итоговый статус платежа через `GET`.

## Где смотреть результат

- Swagger UI: `http://localhost:8000/docs`
- Получить платёжное поручение:
  - `GET /api/v1/payments/orders/{order_id}` (с `X-Tenant-ID`)
- Логи:

```bash
docker compose logs -f app
```

