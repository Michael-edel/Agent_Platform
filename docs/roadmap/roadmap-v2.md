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

