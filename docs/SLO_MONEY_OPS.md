# SLO для Money Ops контура

Версия: 1.0  
Применимо к: Платформе CyberPlat — денежный контур (Payment Orders, Reconciliation, 1C Integration)  
Дата вступления в силу: _________

## Область действия

Настоящий документ определяет Service Level Objectives (SLO) для следующих компонентов денежного контура:

- **Payment Orders** — платёжные поручения и согласование
- **Reconciliation** — банковские выписки и сверка транзакций
- **1C Integration** — интеграция с 1С (создание объектов, экспорт платежей)

SLO применяются только к production-окружению и только к активным tenant-клиентам.

## Определения

**SLI (Service Level Indicator)** — измеримый показатель качества сервиса.

**SLO (Service Level Objective)** — целевое значение SLI.

**Burn-rate** — скорость расходования error budget (отношение нарушений к общему времени).

**Error budget** — допустимый процент времени, когда SLO не выполняется.

## SLO определения

### SLO-1: Payment Approval Latency

**Цель:** 99% согласований платёжных поручений завершаются в течение 10 минут с момента отправки на согласование.

**SLI:** `histogram_quantile(0.99, payment_approval_latency_seconds) <= 600` (10 минут)

**Error budget:** 1% времени (≈7.2 часа в месяц)

**Метрика:** `payment_approval_latency_seconds` (histogram)

**Измерение:** Время от `submit_for_approval` до финального `approved` или `rejected`.

---

### SLO-2: Reconciliation Latency

**Цель:** 99% банковских выписок обрабатываются (upload → finalize) в течение 15 минут.

**SLI:** `histogram_quantile(0.99, reconciliation_latency_seconds) <= 900` (15 минут)

**Error budget:** 1% времени (≈7.2 часа в месяц)

**Метрика:** `reconciliation_latency_seconds` (histogram)

**Измерение:** Время от загрузки выписки (`uploaded`) до завершения обработки (`reconciled`).

---

### SLO-3: 1C Jobs Success Ratio

**Цель:** Не менее 99% jobs 1С интеграции завершаются успешно за любой 30-минутный период.

**SLI:** `sum(rate(onec_job_outcomes_total{status="succeeded"}[30m])) / sum(rate(onec_job_outcomes_total[30m])) >= 0.99`

**Error budget:** 1% jobs могут завершиться с ошибкой

**Метрика:** `onec_job_outcomes_total{status}` (counter)

**Измерение:** Отношение успешных jobs к общему количеству за 30 минут.

---

### SLO-4: Reconciliation Auto-Match Rate (KPI, не paging)

**Цель:** Не менее 70% транзакций автоматически сопоставляются с платёжными поручениями.

**SLI:** `reconciliation_auto_match_rate >= 0.70` (или вычисляемый ratio)

**Примечание:** Это KPI для мониторинга качества, не является paging alert (не вызывает инциденты).

**Метрика:** `reconciliation_auto_match_rate` (gauge/histogram) или вычисляемый из `reconciliation_transactions_total{matched}`

**Измерение:** Процент транзакций с `matched=true` от общего количества.

---

## SLI метрики

### Существующие метрики

- `payment_approval_latency_seconds` (histogram) — задержка согласования
- `reconciliation_latency_seconds` (histogram) — задержка обработки выписки
- `onec_job_outcomes_total{status}` (counter) — результаты jobs 1С
- `reconciliation_auto_match_rate` (histogram) — процент автосопоставления
- `reconciliation_transactions_total{matched}` (counter) — транзакции (сопоставленные/несопоставленные)

### Дополнительные метрики (для backlog monitoring)

- `money_ops_queue_backlog{queue="onec_jobs"}` (gauge) — глубина очереди jobs 1С
- `payment_orders_backlog{status="pending_approval"}` (gauge) — количество поручений в ожидании согласования
- `reconciliation_unmatched_total` (gauge) — количество несопоставленных транзакций

---

## Burn-rate alerts

Для каждого SLO настроены burn-rate alerts:

- **Fast burn** (5m/1h) — критический инцидент, требует немедленного реагирования
- **Slow burn** (30m/6h) — деградация, создаётся ticket для расследования

**Low-traffic guard:** Alerts не срабатывают, если событий слишком мало (< 20 за окно измерения).

---

## Связанные документы

- [SLA.md](SLA.md) — юридическое соглашение об уровне сервиса
- [RUNBOOK.md](RUNBOOK.md) — операционные playbooks для инцидентов
- Prometheus Rules: `monitoring/prometheus/recording_rules_money_ops.yml`
- Prometheus Alerts: `monitoring/prometheus/alerts_money_ops.yml`
- Grafana Dashboard: `monitoring/grafana/dashboards/money_ops_v1.json`

---

## Пересмотр SLO

Настоящие SLO могут быть пересмотрены по соглашению сторон при изменении архитектуры платформы или условий эксплуатации.
