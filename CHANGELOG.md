# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **1С интеграция (MVP)** (`cyberplat/integrations/`):
  - Tenant settings для 1С (per-tenant настройки: base_url, auth, enabled)
  - OneC client с retry/backoff, idempotency headers, timeout
  - Mapping layer: Artifact → 1C payload (counterparty, contract, invoice)
  - Idempotency service для предотвращения дубликатов
  - Integration jobs pipeline с retry/backoff
  - Integration worker для обработки jobs
  - Error queue → Case tasks (при финальных ошибках)
  - Метрики: `onec_job_outcomes_total`, `onec_job_latency_seconds`, `onec_failures_total`
  - API endpoints: settings, test-connection, jobs list
  - Тесты: settings, client, mapper, idempotency, job service, worker

- **Case/Workflow MVP** (`cyberplat/case_service.py`, `app/api/cases.py`):
  - Cases API для работы с кейсами и workflow процессами
  - SQLite storage через `PLATFORM_DB_PATH` или `platform.db`
  - Audit trail через `case_events` таблицу
  - Endpoints: create case, get case, list cases, add task, complete task, transition step, close case
  - Тесты: `tests/test_case_service.py` (pytest, SQLite in-memory)
  - Smoke test: `scripts/smoke_cases.sh` и `make smoke-cases`

- **DATABASE_URL normalization for psycopg v3** (`utils/db_url.py`):
  - `normalize_sqlalchemy_database_url()` — normalizes `postgresql://` and `postgres://` to `postgresql+psycopg://`
  - `get_original_database_url()` — returns raw DATABASE_URL from environment
  - `get_sqlalchemy_database_url()` — returns normalized URL for SQLAlchemy/Alembic
  - Explicit drivers (`postgresql+asyncpg://`, etc.) are preserved unchanged

- **Unified migration check** (`utils/db_migrations.py`):
  - `check_database_migration(engine)` — checks Alembic migrations using SQLAlchemy engine
  - Lazy imports for Alembic to optimize cold start
  - Supports multiple heads

- **Enhanced `/ready` endpoint**:
  - Shows `database_driver` in response (e.g., `"psycopg"`)
  - Uses single SQLAlchemy engine for both connection and migration check
  - `psycopg.connect()` uses original URL (without `+psycopg`)

- **Doctor diagnostic script** (`scripts/doctor.py`):
  - Checks DATABASE_URL, SQLAlchemy driver, connection, migrations
  - Masks credentials in output
  - `--json` flag for CI integration
  - `make doctor` target

- **Documentation**:
  - `docs/RUNBOOK.md` — operations runbook
  - `docs/RELEASE_CHECKLIST.md` — release checklist

- **Regression tests**:
  - `tests/test_db_url_normalization.py` — 28 tests for URL normalization
  - `tests/test_migration_check.py` — 9 tests for migration check
  - `tests/test_doctor.py` — 10 tests for doctor script

### Changed

- `alembic/env.py` uses `get_sqlalchemy_database_url()` for normalized URL
- `Makefile db-check` uses centralized normalization with `PYTHONPATH=.`

### Security

- Database URL with password is NOT logged in full
- Error messages are truncated to avoid credential leaks
- Doctor script masks DSN in all output

---

## [0.1.0] - 2025-01-14

### Added

- **Git hooks v2.0** — security-focused pre-commit and post-commit hooks:
  - Pre-commit blocks secrets, DB files, large files, Python syntax errors
  - Post-commit auto-push with safety checks (rebase/merge detection)
  - Can be disabled via `GIT_AUTO_PUSH=0` or git config

- **Product/UI sync** — legacy `/documents/upload` now syncs with Product API:
  - `ArtifactService.create_artifact()` accepts optional `artifact_id` for idempotency
  - Export endpoint returns `file_id` and `download_url`

### Documentation

- `GIT_HOOKS_SECURITY.md` — git hooks security documentation
