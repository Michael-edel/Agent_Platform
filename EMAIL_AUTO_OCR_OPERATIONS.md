# Email Auto-OCR Operations Guide

## Production-Grade Features

### 1. Admin Authentication

**Endpoint:** `POST /api/v1/ingest/email/ocr/dispatch`

**Защита:**
- Если `ADMIN_API_KEY` задан (prod режим), endpoint требует заголовок `X-Admin-Key`
- Если `ADMIN_API_KEY` не задан (dev режим), endpoint доступен без ключа

**Использование:**
```bash
# Prod режим
export ADMIN_API_KEY="your-secret-key-here"

# Вызов dispatch
curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
  -H "X-Admin-Key: your-secret-key-here"
```

**Безопасность:**
- НЕ используйте `X-Tenant-ID` для этого endpoint (это admin/internal endpoint)
- Храните `ADMIN_API_KEY` в секретах (env vars, secrets manager)
- В prod всегда задавайте `ADMIN_API_KEY`

### 2. Recovery Stuck Processing Jobs

**Проблема:**
Если процесс упал во время OCR, job остаётся в `status=processing` и больше никогда не обрабатывается.

**Решение:**
Автоматический recovery при каждом вызове dispatch:
- Находит jobs со `status=processing` и `updated_at < now() - timeout`
- Переводит их в `failed` с retry или `dead` если превышен max_retries

**Настройка:**
```bash
export EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS=900  # 15 минут (default)
```

**События:**
- `email.ocr.recovered` - job восстановлен (payload: job_id, attempts, reason="timeout")
- `email.ocr.dead` - job стал dead из-за timeout (payload: job_id, reason="timeout")

**Проверка stuck jobs:**
```sql
SELECT * FROM email_ocr_jobs 
WHERE status = 'processing' 
AND updated_at < datetime('now', '-' || :timeout_seconds || ' seconds');
```

### 3. Concurrency Limit

**Настройка:**
```bash
export EMAIL_OCR_MAX_CONCURRENT_JOBS=3  # default: 3
```

**Поведение:**
- Dispatch не запускает новые jobs, если уже обрабатывается `MAX_CONCURRENT_JOBS`
- Response включает `skipped_due_to_limit` - количество пропущенных jobs

**Response:**
```json
{
  "processed": 2,
  "succeeded": 2,
  "failed": 0,
  "dead": 0,
  "skipped_due_to_limit": 5,
  "errors": []
}
```

**Мониторинг:**
```sql
SELECT COUNT(*) FROM email_ocr_jobs WHERE status = 'processing';
```

### 4. Environment Variables

**Обязательные (prod):**
- `ADMIN_API_KEY` - Admin API key для защиты dispatch endpoint

**Опциональные:**
- `EMAIL_AUTO_OCR_ENABLED=1` - Включить auto-OCR (default: 0)
- `EMAIL_AUTO_OCR_MAX_RETRIES=5` - Максимальное количество попыток (default: 5)
- `EMAIL_AUTO_OCR_RETRY_BASE_SECONDS=10` - Базовое время для backoff (default: 10)
- `EMAIL_AUTO_OCR_RETRY_MAX_SECONDS=600` - Максимальное время между попытками (default: 600)
- `EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS=900` - Timeout для recovery stuck jobs (default: 900)
- `EMAIL_OCR_MAX_CONCURRENT_JOBS=3` - Максимальное количество параллельных jobs (default: 3)

### 5. Cron Setup

**Рекомендуемая частота:** каждую минуту

```bash
# Crontab
*/1 * * * * curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
  -H "X-Admin-Key: ${ADMIN_API_KEY}"
```

**Systemd Timer:**
```ini
[Unit]
Description=Email OCR Dispatch
After=network.target

[Service]
Type=oneshot
Environment="ADMIN_API_KEY=your-key"
ExecStart=/usr/bin/curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
  -H "X-Admin-Key: ${ADMIN_API_KEY}"

[Timer]
OnCalendar=*:0/1
Persistent=true
```

**Kubernetes CronJob:**
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: email-ocr-dispatch
spec:
  schedule: "*/1 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: dispatch
            image: curlimages/curl:latest
            command:
            - /bin/sh
            - -c
            - |
              curl -X POST http://api-service:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
                -H "X-Admin-Key: ${ADMIN_API_KEY}"
            env:
            - name: ADMIN_API_KEY
              valueFrom:
                secretKeyRef:
                  name: admin-secrets
                  key: api-key
          restartPolicy: OnFailure
```

### 6. Troubleshooting

#### Jobs не обрабатываются

**Проверка:**
1. Проверить, что cron запускается:
   ```bash
   # Проверить логи cron
   journalctl -u cron | grep dispatch
   ```

2. Проверить, что jobs в очереди:
   ```sql
   SELECT COUNT(*) FROM email_ocr_jobs 
   WHERE status IN ('queued', 'failed') 
   AND (next_run_at IS NULL OR next_run_at <= datetime('now'));
   ```

3. Проверить concurrency limit:
   ```sql
   SELECT COUNT(*) FROM email_ocr_jobs WHERE status = 'processing';
   ```

4. Проверить stuck jobs:
   ```sql
   SELECT * FROM email_ocr_jobs 
   WHERE status = 'processing' 
   AND updated_at < datetime('now', '-900 seconds');
   ```

#### Jobs залипли в processing

**Решение:**
- Recovery автоматически запускается при каждом dispatch
- Если jobs всё ещё stuck, проверьте `EMAIL_OCR_PROCESSING_TIMEOUT_SECONDS`
- Можно вручную восстановить:
  ```sql
  UPDATE email_ocr_jobs 
  SET status = 'failed', 
      attempts = attempts + 1,
      last_error = 'Manual recovery',
      next_run_at = datetime('now', '+10 seconds')
  WHERE status = 'processing' 
  AND updated_at < datetime('now', '-900 seconds');
  ```

#### Dispatch возвращает 403

**Причина:** Неверный или отсутствующий `X-Admin-Key`

**Решение:**
1. Проверить, что `ADMIN_API_KEY` задан в env
2. Проверить, что заголовок `X-Admin-Key` отправляется
3. Проверить, что значение совпадает с `ADMIN_API_KEY`

#### Concurrency limit достигнут

**Причина:** Слишком много jobs обрабатывается одновременно

**Решение:**
1. Увеличить `EMAIL_OCR_MAX_CONCURRENT_JOBS` (если ресурсы позволяют)
2. Проверить, нет ли stuck jobs (они занимают слоты)
3. Дождаться завершения текущих jobs

### 7. Safe Restart

**Перед перезапуском:**
1. Проверить количество processing jobs:
   ```sql
   SELECT COUNT(*) FROM email_ocr_jobs WHERE status = 'processing';
   ```

2. Если jobs обрабатываются, подождать их завершения или увеличить timeout

**После перезапуска:**
1. Recovery автоматически восстановит stuck jobs при первом dispatch
2. Проверить логи на наличие recovered jobs

### 8. Monitoring

**Structured Logging:**
- `email_ocr_job_started` - job начат (extra: job_id, tenant_id, document_artifact_id, attempts)
- `email_ocr_job_completed` - job завершён (extra: job_id, invoice_artifact_id, duration_seconds)
- `email_ocr_job_failed` - job упал (extra: job_id, error, attempts)

**Events:**
- `email.ocr.queued` - job создан
- `email.ocr.started` - обработка начата
- `email.ocr.completed` - OCR завершён
- `email.ocr.failed` - ошибка (retry scheduled)
- `email.ocr.recovered` - job восстановлен из stuck
- `email.ocr.dead` - job стал dead

**Metrics (TODO):**
- `email_ocr_jobs_total{status}` - количество jobs по статусам
- `email_ocr_job_duration_seconds` - длительность обработки
- `email_ocr_retries_total` - количество ретраев

### 9. Manual Testing

**Проверка dispatch:**
```bash
# Без ключа (dev режим)
curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10

# С ключом (prod режим)
curl -X POST http://localhost:8000/api/v1/ingest/email/ocr/dispatch?limit=10 \
  -H "X-Admin-Key: your-key"
```

**Проверка recovery:**
1. Создать job в processing со старым updated_at:
   ```sql
   UPDATE email_ocr_jobs 
   SET status = 'processing',
       updated_at = datetime('now', '-1000 seconds')
   WHERE id = 'job-id';
   ```

2. Запустить dispatch - job должен быть восстановлен

**Проверка concurrency limit:**
1. Создать несколько jobs в processing:
   ```sql
   UPDATE email_ocr_jobs 
   SET status = 'processing'
   WHERE id IN ('job-1', 'job-2', 'job-3');
   ```

2. Запустить dispatch - должен вернуть `skipped_due_to_limit > 0`

### 10. Best Practices

1. **Всегда задавайте `ADMIN_API_KEY` в prod**
2. **Настройте cron с правильной частотой** (каждую минуту)
3. **Мониторьте stuck jobs** (логи, события)
4. **Настройте алерты** на большое количество failed/dead jobs
5. **Регулярно проверяйте concurrency limit** (не должен быть слишком низким)
6. **Используйте structured logging** для анализа
7. **Храните `ADMIN_API_KEY` в secrets manager** (не в коде)

## Summary

Production-grade auto-OCR pipeline включает:
- ✅ Admin authentication для dispatch endpoint
- ✅ Automatic recovery stuck processing jobs
- ✅ Concurrency limit для безопасности
- ✅ Structured logging для observability
- ✅ Events для audit trail
- ✅ Comprehensive operations documentation

Готово к production использованию! 🚀
