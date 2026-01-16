# Security Posture

Краткое описание мер безопасности платформы для Enterprise клиентов и security questionnaires.

## Data Isolation

### Multi-tenant architecture

- **Tenant isolation**: все данные изолированы по `tenant_id`
- **No cross-tenant access**: API endpoints требуют `X-Tenant-ID` header
- **Database level**: все таблицы содержат `tenant_id` с индексами
- **Fail-closed**: отсутствие `tenant_id` → 403 Forbidden

### Tenant scoping

- **Admin Panel**: `tenant_admin` видит только свой tenant
- **API**: все запросы проверяют `tenant_id` перед доступом к данным
- **Audit log**: все действия логируются с `tenant_id`

## Secrets Management

### Environment variables only

- **No hardcoded secrets**: все секреты через `ENV`
- **Masked logs**: пароли, API ключи не выводятся в логи
- **Safe health checks**: `/ready` endpoint не возвращает секреты

### Что маскируется

- `DATABASE_URL` (показывается только схема: `postgresql://`)
- `STRIPE_API_KEY` / `STRIPE_SECRET_KEY`
- `KASPI_API_KEY`
- `ADMIN_PASSWORD`
- Webhook secrets

### Где хранятся секреты

- Production: environment variables (не в коде)
- Staging: `.env` файл (не коммитится, в `.gitignore`)
- Development: `.env.example` (шаблон без реальных значений)

## Webhooks Security

### Allowlist

- **IP allowlist**: клиенты настраивают allowlist в Stripe/Kaspi Dashboard
- **Domain validation**: webhook URLs проверяются на валидность домена
- **No public endpoints**: webhook endpoints требуют валидации подписи

### Signature Verification

**Stripe:**
- Проверка `Stripe-Signature` header
- Использование `STRIPE_WEBHOOK_SECRET`
- HMAC SHA256 verification

**Kaspi:**
- Проверка `X-Kaspi-Signature` header
- Использование `KASPI_WEBHOOK_SECRET`
- Custom signature algorithm

**Rejection:**
- Невалидная подпись → `403 Forbidden`
- Событие не обрабатывается, логируется в `billing_webhook_events` со статусом `failed`

### Idempotency

- **Event deduplication**: по `event_id` + `provider`
- **Idempotency key**: `billing_jobs` используют `idempotency_key = "usage_invoice:{invoice_id}"`
- **Safe retries**: повторная обработка того же события безопасна

## Audit Log

### Что логируется

**Admin actions:**
- Создание/изменение `Tenant`
- Изменение `TenantBillingSettings`
- Billing job actions (refresh, retry)
- Token rotation

**Billing events:**
- Webhook events (status, provider, tenant_id)
- Invoice finalization
- Payment status transitions

**Что НЕ логируется:**
- Секреты (API keys, passwords)
- Raw webhook payload (только метаданные)
- Полные `DATABASE_URL` (только схема)

### Immutable history

- **No deletions**: `admin_audit_log` — append-only
- **Timestamp**: все записи с `created_at` (UTC)
- **Actor**: `admin_user` (если доступно)
- **Action**: тип действия (create, update, delete, action)

## Access Control

### Roles

**platform_admin:**
- Доступ ко всем tenant'ам
- Управление billing settings
- Просмотр всех webhook events
- Billing job actions

**tenant_admin:**
- Доступ только к своему `tenant_id`
- Read-only просмотр своих данных
- Tenant Portal API (с `X-Tenant-Portal-Key`)

### Authentication

**Admin Panel:**
- HTTP Basic Auth (username/password из ENV)
- Session cookies (HttpOnly, SameSite=Lax)
- В production: `Secure` flag (HTTPS only)

**Tenant Portal API:**
- `X-Tenant-ID` + `X-Tenant-Portal-Key` headers
- Токен хранится как SHA256 hash (не plaintext)
- Ротация токена через Admin Panel

### Authorization

- **Fail-closed**: отсутствие прав → 403 Forbidden
- **Tenant scoping**: автоматическая фильтрация по `tenant_id`
- **No privilege escalation**: `tenant_admin` не может стать `platform_admin`

## Incident Response

### High-level process

1. **Detection**: через Prometheus alerts, Grafana dashboards, logs
2. **Triage**: определить scope (single tenant vs platform-wide)
3. **Containment**: disable provider, enable dry-run, block tenant (если нужно)
4. **Remediation**: fix root cause, retry failed jobs
5. **Post-mortem**: документировать в `admin_audit_log` (опционально)

### Security incidents

**Webhook signature failures:**
- Проверить allowlist, webhook secret
- Проверить логи на подозрительные IP
- При необходимости: rotate webhook secret

**Unauthorized access attempts:**
- Проверить `admin_audit_log` на failed logins
- Проверить Tenant Portal API на 403 errors
- При необходимости: rotate tokens, блокировать IP

**Data breach (hypothetical):**
- Изолировать affected tenant
- Audit log review
- Notify affected parties (если требуется)

## Compliance Notes

### Data retention

- **Audit logs**: хранятся неограниченно (append-only)
- **Webhook events**: retention зависит от Prometheus retention (`PROMETHEUS_RETENTION_TIME`, default 7d)
- **Usage invoices**: хранятся неограниченно (для billing history)

### GDPR / Privacy

- **Tenant data**: изолирована по `tenant_id`
- **No PII in logs**: логи не содержат персональных данных клиентов
- **Data export**: через Tenant Portal → Support Bundle (JSON, tenant-scoped)

### SOC 2 readiness

- **Access control**: роли, authentication, authorization
- **Audit trail**: `admin_audit_log` для всех изменений
- **Change management**: миграции через Alembic (versioned)
- **Monitoring**: Prometheus + Grafana для observability

## References

- [RUNBOOK.md](RUNBOOK.md) — операционная документация
- [PROD_ONBOARDING.md](PROD_ONBOARDING.md) — onboarding процесс
- Admin Panel: `/admin` (требует `ADMIN_ENABLED=true`)
