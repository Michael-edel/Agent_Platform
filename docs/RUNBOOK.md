# Runbook: Agent Platform / CyberPlat

Практическое руководство по эксплуатации.

## Быстрый старт (Docker)

### Минимальные переменные окружения

```bash
# PostgreSQL (production/staging)
DATABASE_URL=postgresql://postgres:password@postgres:5432/agent_platform

# Heroku-style URL также поддерживается
DATABASE_URL=postgres://postgres:password@postgres:5432/agent_platform

# SQLite (dev, по умолчанию)
PLATFORM_DB_PATH=platform.db
```

**Важно:** SQLAlchemy автоматически нормализует `postgresql://` и `postgres://` в `postgresql+psycopg://` для использования psycopg v3. Ничего дополнительно настраивать не нужно.

### Запуск

```bash
# Поднять всё
docker-compose up -d

# Проверить готовность
curl http://localhost:8000/ready

# Применить миграции (PostgreSQL)
docker exec -it agent-platform-app alembic upgrade head
```

## Диагностика

### Health Endpoints

```bash
# Liveness (процесс жив)
curl http://localhost:8000/health

# Readiness (зависимости готовы)
curl http://localhost:8000/ready

# Метрики Prometheus
curl http://localhost:8000/metrics
```

### Интерпретация /ready

**status: ok** — всё работает, миграции актуальны.

**status: degraded** — приложение работает, но есть проблемы:
- `database_migration: "warning: pending migrations..."` — нужно применить миграции
- `database_migration: "warning: database has no alembic revision"` — БД не инициализирована миграциями

**Пример ответа:**
```json
{
  "status": "ok",
  "checks": {
    "database": "ok (postgresql, driver: psycopg)",
    "database_driver": "psycopg",
    "database_migration": "ok (revision: abc123)",
    "billing_service": "ok"
  }
}
```

### Скрипт диагностики

```bash
# Локально
make doctor

# В контейнере
docker exec -it agent-platform-app python scripts/doctor.py

# JSON вывод (для CI)
python scripts/doctor.py --json
```

## Миграции

### Команды

```bash
# Текущая версия в БД
alembic current

# Доступные версии (heads)
alembic heads

# Применить все миграции
alembic upgrade head

# Откатить последнюю
alembic downgrade -1

# Проверка через Makefile
make db-check
```

### Важно

- `/ready` **НЕ выполняет** миграции, только проверяет соответствие версий
- При `pending migrations` приложение работает, но `/ready` возвращает `degraded`
- Миграции нужно применять явно перед/после деплоя

## Безопасность

### Что НЕ выводится в логи/ответы

- Полный DATABASE_URL с паролем
- Credentials пользователей
- API ключи (Stripe, Kaspi)

### Что выводится безопасно

- Схема БД (`postgresql`, `sqlite`)
- Драйвер (`psycopg`)
- Хост (без user:pass)
- Статус миграций

### Где смотреть логи

```bash
# Docker
docker-compose logs -f app

# Kubernetes
kubectl logs -f deployment/agent-platform

# Формат логов
LOG_FORMAT=json   # production (структурированные)
LOG_FORMAT=pretty # development
```

## Troubleshooting

### "No module named 'psycopg2'"

**Причина:** Старая проблема, исправлена нормализацией URL.

**Почему больше не случится:** 
- `postgresql://` автоматически преобразуется в `postgresql+psycopg://`
- Проект использует `psycopg v3`, не `psycopg2`
- Проверить драйвер: `curl /ready | jq .checks.database_driver`

### "database has no alembic revision"

**Причина:** БД существует, но таблица `alembic_version` пустая или отсутствует.

**Решение:**
```bash
# Инициализировать миграции
alembic upgrade head

# Или stamp текущую версию (если схема уже актуальна вручную)
alembic stamp head
```

### "pending migrations"

**Причина:** Версия схемы в БД отличается от head в коде.

**Решение:**
```bash
# Посмотреть разницу
alembic current
alembic heads

# Применить
alembic upgrade head
```

### /ready возвращает degraded

**Действия:**
1. Проверить `database_migration` в ответе
2. Если pending — применить миграции
3. Если connection error — проверить DATABASE_URL и доступность БД
4. После исправления: `curl /ready` должен вернуть `status: ok`

## Admin Panel (SQLAdmin)

### Включение

```bash
# В .env или docker-compose.yml
ADMIN_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password
ADMIN_SECRET_KEY=random-secret-for-sessions
```

### Доступ

URL: `http://localhost:8000/admin`

Войти с логином/паролем из ENV.

### Роли

| Роль | Описание |
|------|----------|
| `platform_admin` | Видит все tenant'ы (по умолчанию) |
| `tenant_admin` | Видит только свой tenant |

Для `tenant_admin` добавить:
```bash
ADMIN_ROLE=tenant_admin
ADMIN_TENANT_ID=your-tenant-id
```

### Возможности

- **Plans / Billing Plans** — CRUD тарифных планов
- **Tenant Subscriptions** — просмотр подписок (read-only)
- **Billing Orders / Events** — просмотр платежей и webhook'ов (read-only)
- **Webhooks / Deliveries** — просмотр конфигурации webhooks (read-only)

### Безопасность

- Admin panel отключена по умолчанию (`ADMIN_ENABLED=false`)
- Без `ADMIN_PASSWORD` логин невозможен
- Секреты (webhook secrets, tokens) скрыты в UI
- Платёжные данные доступны только для просмотра
