# Release Checklist

Чеклист для проверки перед релизом в production.

## Pre-release

- [ ] Все тесты зелёные (`make test`)
- [ ] Lint проверка пройдена (`make lint`)
- [ ] Миграции Alembic применены (`alembic upgrade head`)
- [ ] `/ready` endpoint возвращает `status: ok`
- [ ] Документация обновлена (если нужно)

## New tenant onboarded

- [ ] Tenant создан и активен (`Admin → Tenants`)
- [ ] Billing settings настроены (`Admin → Tenant Billing Settings`)
- [ ] Webhook URLs переданы клиенту
- [ ] Webhook allowlist подтверждён
- [ ] Dry-run billing пройден в staging
- [ ] Ответственные лица зафиксированы

## Billing cutover completed

- [ ] `BILLING_DRY_RUN=false` установлен в production
- [ ] Provider credentials подтверждены (Stripe/Kaspi)
- [ ] Test charge / test webhook выполнен
- [ ] Worker up и обрабатывает jobs
- [ ] First real invoice paid (`payment_status = "paid"`)
- [ ] Webhook events processed (status = "processed")

## SLO alerts green

- [ ] Prometheus targets healthy
- [ ] Grafana SLA dashboard v2.1 открыт и работает
- [ ] `BillingSLOTimeToPaidFastBurn` не firing
- [ ] `BillingSLOTimeToPaidSlowBurn` не firing
- [ ] `BillingWorkerDown` не firing
- [ ] `BillingJobsQueueStuck` не firing

## Post-release (first 24h)

- [ ] Мониторинг dashboards (Grafana)
- [ ] Проверка алертов (Prometheus)
- [ ] Нет критических инцидентов
- [ ] Client support готов (если нужно)

## References

- [PROD_ONBOARDING.md](docs/PROD_ONBOARDING.md) — детальный onboarding playbook
- [RUNBOOK.md](docs/RUNBOOK.md) — операционная документация
- [SECURITY_POSTURE.md](docs/SECURITY_POSTURE.md) — безопасность
