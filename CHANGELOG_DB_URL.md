## DATABASE_URL Normalization for psycopg v3

### Summary

Centralized DATABASE_URL normalization to ensure SQLAlchemy uses `psycopg v3` driver correctly.

### Changes

1. **`utils/db_url.py`** — Centralized normalization functions:
   - `normalize_sqlalchemy_database_url()` — normalizes `postgresql://` and `postgres://` to `postgresql+psycopg://`
   - `get_original_database_url()` — returns raw DATABASE_URL from environment
   - `get_sqlalchemy_database_url()` — returns normalized URL for SQLAlchemy/Alembic
   - Explicit drivers (`postgresql+asyncpg://`, `postgresql+psycopg://`, etc.) are NOT modified

2. **`utils/db_migrations.py`** — Unified migration check:
   - `check_database_migration(engine)` — checks Alembic migrations using the provided SQLAlchemy engine
   - Lazy imports for Alembic to optimize cold start
   - Supports multiple heads

3. **`/ready` endpoint**:
   - Shows `database_driver` in response (e.g., `"psycopg"`)
   - Uses single SQLAlchemy engine for both connection and migration check
   - `psycopg.connect()` uses original URL (without `+psycopg`)

4. **`alembic/env.py`**:
   - Uses `get_sqlalchemy_database_url()` for normalized URL

5. **`Makefile db-check`**:
   - Uses `get_sqlalchemy_database_url()` with `PYTHONPATH=.`

### Behavior

| Input DATABASE_URL | SQLAlchemy URL | psycopg.connect |
|-------------------|----------------|-----------------|
| `postgresql://user:pass@host/db` | `postgresql+psycopg://user:pass@host/db` | parsed params from original |
| `postgres://user:pass@host/db` | `postgresql+psycopg://user:pass@host/db` | parsed params from original |
| `postgresql+psycopg://...` | unchanged | N/A |
| `postgresql+asyncpg://...` | unchanged | N/A |
| `sqlite:///file.db` | unchanged | N/A |

### Security

- Database URL with password is NOT logged in full
- Error messages are truncated to 100 chars to avoid credential leaks

### Tests

- `tests/test_db_url_normalization.py` — 28 tests for normalization
- `tests/test_migration_check.py` — 9 tests for migration check
- All tests run on SQLite, no PostgreSQL required
