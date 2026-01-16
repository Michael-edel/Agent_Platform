# Pilot Rollout Plan (30 дней)

Пошаговый план сопровождения первого production-клиента в течение 30 дней.

## 1. Цель пилота

- **Проверить корректность биллинга и оплат**
  - Invoice finalization работает
  - Billing jobs обрабатываются успешно
  - Webhooks от провайдеров приходят и обрабатываются
  - Payment status transitions корректны

- **Проверить observability (SLA/SLO)**
  - Grafana dashboards показывают метрики
  - Prometheus alerts работают
  - Time-to-paid соответствует SLO (≤ 5 минут)

- **Подтвердить готовность платформы к масштабированию**
  - Billing worker обрабатывает нагрузку
  - Retry/backoff policy работает
  - Нет ручных вмешательств в БД

## 2. Scope пилота

### Зафиксировать

- **Количество tenants:** 1 (обычно)
- **Список агентов:**
  - Free: `demo.text_stats` (если доступен)
  - Paid: список платных агентов (если используются)
- **Лимиты usage:**
  - План: `{plan_id}` (например, `plan_basic`)
  - Quotas: `{quotas_json}` (например, `{"documents": 1000, "ocr_pages": 5000}`)
- **Платёжный провайдер:** `kaspi` или `stripe`

### Вне scope

⚠️ **Всё, что вне scope — не является частью SLA пилота:**
- Новые агенты (не включённые в список)
- Изменение плана/лимитов (только по согласованию)
- Смена платёжного провайдера (только по согласованию)
- Интеграции с внешними системами клиента
- Кастомные features (не входящие в стандартный функционал)

## 3. Таймлайн (30 дней)

### День 0 — Go-Live

**Чеклист:**
- [ ] Billing cutover выполнен (`BILLING_DRY_RUN=false`)
- [ ] Dry-run отключён
- [ ] Webhooks подтверждены (allowlist настроен)
- [ ] First invoice finalized
- [ ] Billing job создан и succeeded
- [ ] Payment status = "paid" (реальный, не dry-run)
- [ ] Prometheus targets healthy
- [ ] Grafana dashboard v2.1 открыт

**Ответственные:** Ops, Platform Admin

### Дни 1–7 — Stabilization

**Ежедневные проверки:**
- [ ] Мониторинг billing jobs (queue depth, failures)
- [ ] Проверка первых оплат (payment_status transitions)
- [ ] SLA dashboard v2.1 (time-to-paid, failure reasons)
- [ ] SLO alerts (не firing)
- [ ] Webhook events (processed, не failed)

**Метрики для отслеживания:**
- `billing_jobs_queue_depth{status="pending"}` = 0
- `billing_jobs_queue_depth{status="pending_retry"}` = 0
- `usage_invoices_payment_status_transitions_total{to="paid"}` > 0
- `usage_invoice_time_to_paid_seconds_bucket` (p95 ≤ 300s)

**Ответственные:** Ops (ежедневно), Platform Admin (еженедельно)

### Дни 8–21 — Normal operation

**Еженедельные проверки:**
- [ ] Регулярные invoice finalize (ежемесячные или по расписанию)
- [ ] Usage growth (анализ тенденций)
- [ ] Анализ SLO (time-to-paid, failure rate)
- [ ] Проверка retry policy (если были failed jobs)

**Метрики для отслеживания:**
- Invoice finalization rate (ожидаемая частота)
- Usage trends (рост/стабильность)
- SLO compliance (99% invoices paid within 5m)

**Ответственные:** Ops (еженедельно), Platform Admin (по запросу)

### Дни 22–30 — Review & decision

**Итоговая оценка:**
- [ ] Итоги usage и billing (статистика за 30 дней)
- [ ] Обсуждение масштабирования (готовность к новым tenants)
- [ ] Решение: go-forward / pause / exit

**Документы для review:**
- Grafana dashboard snapshot (30 дней)
- Prometheus metrics summary
- Список инцидентов (если были)
- Рекомендации по улучшениям

**Ответственные:** Platform Admin, Sales, Client

## 4. Метрики успеха (Success criteria)

### Обязательные критерии

- [ ] **% paid invoices ≥ 99%**
  - Формула: `(paid_invoices / total_invoices) * 100 ≥ 99`
  - Исключения: только технические ошибки провайдера (не платформы)

- [ ] **Time-to-paid p95 ≤ 5 минут**
  - Метрика: `histogram_quantile(0.95, usage_invoice_time_to_paid_seconds_bucket) ≤ 300`
  - Scope: только invoices с `provider != "unknown"`

- [ ] **Billing jobs failure rate ≤ 1%**
  - Формула: `(failed_jobs / total_jobs) * 100 ≤ 1`
  - Исключения: только невосстановимые ошибки провайдера

- [ ] **Нет ручных правок БД**
  - Все изменения через Admin Panel (audit log)
  - Или через миграции Alembic (versioned)

### Дополнительные критерии (желательные)

- [ ] **Worker uptime ≥ 99.9%**
  - Метрика: `billing_worker_up == 1` (кроме плановых перезапусков)

- [ ] **Webhook events processed rate ≥ 99%**
  - Формула: `(processed_events / total_events) * 100 ≥ 99`

- [ ] **Нет критических инцидентов**
  - Определение: инцидент, требующий ручного вмешательства или rollback

## 5. Операционные обязанности

### CyberPlat отвечает за

- **Стабильность billing pipeline**
  - Billing worker работает и обрабатывает jobs
  - Retry/backoff policy применяется корректно
  - Ошибки логируются и мониторятся

- **SLA/SLO мониторинг**
  - Grafana dashboards доступны и актуальны
  - Prometheus alerts настроены и работают
  - Метрики собираются корректно

- **Техническая поддержка**
  - Реакция на инциденты (см. раздел "Эскалация")
  - Еженедельные check-ins (дни 1-7: ежедневно)
  - Документирование проблем и решений

### Клиент отвечает за

- **Корректность платёжных реквизитов**
  - Актуальные данные в Stripe/Kaspi
  - Достаточный баланс для оплат
  - Уведомления об изменении реквизитов

- **Подтверждение webhooks**
  - Allowlist настроен и актуален
  - Webhook endpoints доступны
  - Уведомления об изменении конфигурации

- **Своевременная оплата**
  - Оплата invoices в срок (согласно контракту)
  - Уведомления о проблемах с оплатой

## 6. Эскалация и инциденты

### Что считается инцидентом

- **Критический:**
  - Billing worker down > 5 минут
  - Массовые failed billing jobs (> 10% за час)
  - SLO breach (time-to-paid p95 > 5 минут) > 1 час
  - Payment status stuck (не переходит в paid) > 1 час

- **Высокий приоритет:**
  - Отдельные failed billing jobs (требуют retry)
  - Webhook events failed (требуют investigation)
  - SLO alerts firing (требуют мониторинга)

- **Средний приоритет:**
  - Медленные transitions (time-to-paid > 5 минут, но < 10 минут)
  - Единичные webhook failures (не критично)

### Канал эскалации

- **Критический:** Slack/Email → Ops Lead → Platform Admin
- **Высокий приоритет:** Slack/Email → Ops
- **Средний приоритет:** Еженедельный check-in

### Временные рамки реакции

- **Критический:** ≤ 1 час (начало investigation)
- **Высокий приоритет:** ≤ 4 часа (начало investigation)
- **Средний приоритет:** ≤ 24 часа (в рамках check-in)

## 7. Exit criteria

### Успешный пилот (go-forward)

**Условия:**
- Все обязательные метрики успеха выполнены (≥ 99% paid, time-to-paid ≤ 5m, failure rate ≤ 1%)
- Нет критических инцидентов (или все разрешены)
- Клиент подтверждает готовность к стандартному контракту

**Действия:**
- Переход в стандартный контракт
- Увеличение scope (если нужно)
- Масштабирование на новые tenants (если применимо)

### Неуспешный пилот (exit)

**Условия:**
- Обязательные метрики не выполнены (> 1% failure rate, time-to-paid > 5m)
- Критические инциденты не разрешены
- Клиент не готов к стандартному контракту

**Действия:**
- Отключение биллинга (`BILLING_DRY_RUN=true` или disable provider)
- Сохранение данных (tenant остаётся в БД, но billing отключён)
- Post-mortem (анализ причин, рекомендации)
- Возможность повторного пилота (после исправлений)

### Пауза (pause)

**Условия:**
- Частичное выполнение метрик (некоторые критерии не выполнены)
- Требуется доработка платформы или процессов

**Действия:**
- Пилот приостановлен (billing может остаться включённым, но без SLA)
- План доработок зафиксирован
- Возобновление после исправлений

## 8. References

- [PROD_ONBOARDING.md](PROD_ONBOARDING.md) — onboarding процесс
- [RUNBOOK.md](RUNBOOK.md) — операционная документация
- Grafana Dashboard v2.1: `observability/grafana/dashboards/billing_sla_dashboard_v2.json`
- Prometheus Rules: `docs/alerts/prometheus_billing_sla_rules_v2.yml`
