# Roadmap v2 — Phase 1: Payment lifecycle + timeline (read-only)

## Цель Phase 1

Сделать платёж прозрачным объектом во времени:
- понятно, что с ним происходит
- понятно, кто и когда принял решение
- без BPM, без автоматики, без сложных интеграций

## Что добавлено в Phase 1

### 1) Payment lifecycle states (минимально)

- Введены состояния `PaymentState`:
  - `DRAFT`
  - `PENDING_APPROVAL`
  - `APPROVED`
  - `REJECTED`
  - `SENT`
  - `FAILED`
  - `RECONCILED`

Правила:
- состояния не удаляются
- переходы фиксируются событиями

### 2) События lifecycle (audit)

Минимальный набор:
- `payment.created`
- `payment.state_changed`
- `payment.approved`
- `payment.rejected`
- `payment.export_failed`
- `payment.sent`
- `payment.reconciled`

События пишутся через существующий `EventService` (таблица `events`) и используются как источник Timeline.

### 3) Timeline API (read-only)

Endpoint:
- `GET /api/v1/payments/{payment_id}/timeline`

Возвращает:
- `payment_id`
- `current_state`
- `timeline[]` (по времени ASC)

## Критерий успеха Phase 1

Финансовый директор может открыть платёж и понять, что с ним было (создание → переходы → решение), без добавления автоматизации.

---

## Phase 2 (30–45 дней): Manual Reconciliation + Bank Statement Import (best-effort)

### Цель Phase 2

Ответить на главный вопрос после approve: **“Этот платёж реально ушёл?”**

Принцип: reconciliation начинается вручную; автоматизация — помощник, не источник истины.

### Что добавлено

#### 1) Manual reconciliation: “Mark as paid”

- `POST /api/v1/payments/{payment_id}/reconcile/manual`

Payload:
- `paid_at` (обязательно, `YYYY-MM-DD`)
- `source` (обязательно: `bank|1c|manual`)
- `note` (опционально)
- `statement_line_id` (опционально)

Правила:
- разрешено только если `state = APPROVED`
- переводит в `state = RECONCILED`
- пишет событие `payment.reconciled` (без silent updates)

#### 2) Bank statement import (artifacts only)

- `POST /api/v1/reconciliation/bank-statements/import` (CSV multipart)

Формат CSV (фиксированный):

```
date,amount,description
2026-01-15,1000.00,Payment INV-123
```

На этом этапе:
- создаются артефакты `bank.statement.line`
- никакого матчинга и изменений платежей автоматически

#### 3) Assisted matching (read-only suggestions)

- `GET /api/v1/reconciliation/suggestions?payment_id=...`

Возвращает suggestions по `amount == payment.amount` и `date ± N дней`, без действий.

---

## Phase 3 (45–60 дней): 1C Trust Bridge — Export summary + Import confirmation

### Цель Phase 3

Закрыть доверие между системой и учётом (1С) без «интеграционного ада»:
- система отдаёт краткую сводку по платежу (summary)
- система принимает подтверждение (confirm) и фиксирует исход

### Что добавлено

#### 1) Export payment summary (read-only)

- `GET /api/v1/integrations/1c/payments/{payment_id}/summary`

Сводка берётся из payment + событий timeline (read-only, tenant-scoped).

#### 2) Import confirmation (write, idempotent)

- `POST /api/v1/integrations/1c/payments/confirm`

Правила:
- tenant-scoped (через `X-Tenant-ID`)
- идемпотентность по ключу `(tenant_id, payment_id, external_id, result)` через `idempotency_key` в событиях
- `CONFIRMED` → state `SENT` (или оставляет `RECONCILED`, если уже было оплачено) + событие `payment.sent`
- `REJECTED` → state `FAILED` + событие `payment.sent_failed` (reason обязателен)

