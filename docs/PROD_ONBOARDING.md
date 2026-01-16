# Production Onboarding Playbook

Практическое руководство по подключению нового tenant в production для B2B/Enterprise клиентов.

## 1. Введение

### Для кого документ

- **Sales**: передача клиента в production
- **Operations**: выполнение onboarding
- **Support**: первичная поддержка после go-live

### Когда использовать

- Переход из pilot/staging в production
- Подключение нового Enterprise клиента
- Восстановление после инцидента (re-onboarding)

## 2. Prerequisites (до включения prod)

### Чеклист

- [ ] **Tenant создан и активен**
  - Проверить: `Admin → Tenants → {tenant_id}` → `is_active = true`
  - Зафиксировать `tenant_id` в документации клиента

- [ ] **Billing settings заполнены**
  - `Admin → Tenant Billing Settings → {tenant_id}`
  - `default_provider`: `kaspi` или `stripe` (или `null` для глобального)
  - `stripe_enabled`: `true` если используется Stripe
  - `kaspi_enabled`: `true` если используется Kaspi
  - ⚠️ Нельзя выключить оба провайдера одновременно

- [ ] **Webhook URLs сгенерированы и переданы клиенту**
  - Stripe: `{PUBLIC_BASE_URL}/api/v1/billing/webhook/stripe`
  - Kaspi: `{PUBLIC_BASE_URL}/api/v1/billing/webhook/kaspi`
  - Проверить `PUBLIC_BASE_URL` в production env

- [ ] **Webhook allowlist подтверждён**
  - Клиент добавил наши IP/домен в allowlist провайдера
  - Для Stripe: настроен webhook endpoint в Stripe Dashboard
  - Для Kaspi: подтверждён список разрешённых IP

- [ ] **Dry-run billing пройден (staging)**
  - Выполнить `POST /api/v1/tenant/billing/usage-invoices/{period}/finalize` в staging
  - Убедиться: `billing_job` создан, `status = succeeded`
  - Проверить: `usage_invoices.payment_status = "paid"` (dry-run)
  - Логи: `[DRY-RUN] Would create payment...`

- [ ] **Ответственные лица зафиксированы**
  - Client: контакт для billing вопросов
  - Internal: platform_admin для tenant

**SLA и целевые показатели:** см. [SLA.md](SLA.md)

## 3. Billing cutover (ключевой раздел)

### Пошагово

#### 3.1 Staging validation (dry-run)

```bash
# Проверить флаг
echo $BILLING_DRY_RUN  # должен быть "true"

# Finalize test invoice
curl -X POST "https://staging.example.com/api/v1/tenant/billing/usage-invoices/2025-01/finalize" \
  -H "X-Tenant-ID: {tenant_id}"

# Проверить результат
# Admin → Usage Invoices → {period} → payment_status = "paid" (dry-run)
# Admin → Billing Jobs → status = "succeeded"
```

**Убедиться:**
- `billing_job` создан с `provider = "dry_run"`
- `job.status = "succeeded"`
- `usage_invoices.payment_status = "paid"`
- В логах: `[DRY-RUN]` сообщения

#### 3.2 Назначить окно включения prod billing

- Рекомендуется: рабочие часы (для мониторинга)
- Избегать: выходные, праздники
- Минимальное окно: 2 часа (для первой проверки)

#### 3.3 Production cutover

**В production:**

1. **Установить `BILLING_DRY_RUN=false`**
   ```bash
   # В docker-compose.yml или env
   BILLING_DRY_RUN=false
   ```

2. **Подтвердить provider credentials**
   - Stripe: `STRIPE_API_KEY` (live key, не test)
   - Kaspi: `KASPI_API_KEY` (production)
   - Проверить через test charge (см. ниже)

3. **Выполнить test charge / test webhook**
   ```bash
   # Test charge (Stripe)
   # Использовать Stripe Dashboard → Payments → Create test payment
   # Или через API с test amount
   
   # Test webhook
   # Stripe Dashboard → Webhooks → Send test webhook
   # Проверить в Admin → Webhook Events → status = "processed"
   ```

4. **Проверить worker**
   ```bash
   # Prometheus metrics
   curl http://localhost:9101/metrics | grep billing_worker_up
   # Должно быть: billing_worker_up{worker_id="..."} 1
   ```

## 4. Go-live checklist

### Pre-flight

- [ ] **Prometheus targets healthy**
  - `curl http://localhost:9090/api/v1/targets`
  - Все targets в состоянии `up`

- [ ] **Grafana SLA dashboard v2.1 открыт**
  - URL: `http://localhost:3000/d/billing-sla-v2`
  - Проверить: нет ошибок загрузки данных

- [ ] **SLO alerts не firing**
  - Prometheus: `curl http://localhost:9090/api/v1/alerts`
  - Проверить: `BillingSLOTimeToPaidFastBurn` и `BillingSLOTimeToPaidSlowBurn` не активны

- [ ] **Worker up, queue depth = 0**
  ```bash
  curl http://localhost:9101/metrics | grep billing_jobs_queue_depth
  # billing_jobs_queue_depth{status="pending"} 0
  # billing_jobs_queue_depth{status="pending_retry"} 0
  ```

### Post go-live (первые 24 часа)

- [ ] **First real invoice paid**
  - `Admin → Usage Invoices → {period}`
  - `payment_status = "paid"` (не dry-run)
  - `billing_jobs.status = "succeeded"`
  - `provider_ref` заполнен (Stripe PaymentIntent ID или Kaspi order ID)

- [ ] **Webhook events processed**
  - `Admin → Webhook Events → {tenant_id}`
  - Проверить: последние события со статусом `processed` (не `failed`)

- [ ] **No critical alerts**
  - Prometheus alerts: все зелёные
  - Grafana: нет красных панелей

## 5. Rollback strategy

### Когда откатываемся

- Первый invoice failed (payment_status = "failed")
- Worker не обрабатывает jobs (queue stuck)
- Webhook events массово failed
- SLO alerts firing (time-to-paid > 5m)

### Как откатываться

1. **Вернуть `BILLING_DRY_RUN=true`**
   ```bash
   # В production env
   BILLING_DRY_RUN=true
   # Перезапустить worker (если нужно)
   docker-compose restart worker
   ```

2. **Disable provider per-tenant** (опционально)
   - `Admin → Tenant Billing Settings → {tenant_id}`
   - Установить `stripe_enabled = false` или `kaspi_enabled = false`
   - Или установить `default_provider = null` (fallback на dry-run)

3. **Retry failed jobs позже**
   - `Admin → Billing Jobs → {job_id} → Mark job for retry`
   - Или через API: `POST /api/v1/tenant/billing/usage-invoices/{period}/retry`

### Что НЕ делаем

- ❌ **No manual DB edits без записи в audit log**
  - Все изменения через Admin Panel (записываются в `admin_audit_log`)
  - Или через миграции Alembic

- ❌ **Не удаляем billing_jobs вручную**
  - Использовать retry policy или mark for retry

- ❌ **Не меняем payment_status напрямую в БД**
  - Использовать "Refresh from provider" action в Admin

## 6. First 7 days monitoring

### Ежедневные проверки

**Grafana dashboards:**
- `Billing SLA / Invoices v2` (v2.1)
  - Invoice funnel (snapshot по payment_status)
  - Time-to-paid p95 (split by provider)
  - Failure reasons summary

- `Billing Worker / Jobs`
  - Queue depth (pending, pending_retry)
  - Worker health (up, last_success_timestamp)
  - Processing rate

**Prometheus alerts:**
- `BillingSLOTimeToPaidFastBurn` (page)
- `BillingSLOTimeToPaidSlowBurn` (ticket)
- `BillingWorkerDown` (page)
- `BillingJobsQueueStuck` (ticket)

### Критические алерты (эскалация)

| Алерт | Действие | Эскалация |
|-------|----------|-----------|
| `BillingSLOTimeToPaidFastBurn` | Проверить queue, worker, provider | Ops lead |
| `BillingWorkerDown` | Перезапустить worker | Ops |
| `BillingJobsQueueStuck` | Проверить failed jobs, retry | Ops |
| Массовые webhook failures | Проверить allowlist, signature | Security + Ops |

### Кому эскалировать

- **Ops**: worker down, queue stuck, DB issues
- **Security**: webhook signature failures, allowlist issues
- **Billing**: payment failures, provider issues
- **Platform Admin**: tenant-specific issues

## 7. References

- [RUNBOOK.md](RUNBOOK.md) — общая эксплуатация
- [SECURITY_POSTURE.md](SECURITY_POSTURE.md) — безопасность
- [SLA.md](SLA.md) — SLA и целевые показатели
- Grafana Dashboard v2.1: `observability/grafana/dashboards/billing_sla_dashboard_v2.json`
- Prometheus Rules: `docs/alerts/prometheus_billing_sla_rules_v2.yml`
