# Release Notes: PostgreSQL psycopg v3 Compatibility Fix

## 🚀 Improvements

### Database URL Normalization for psycopg v3

Fixed compatibility issue where SQLAlchemy and Alembic attempted to use `psycopg2` driver when `DATABASE_URL` was specified as `postgresql://...` or `postgres://...`, while the project uses `psycopg v3` (`psycopg[binary]`).

**What changed:**
- Automatic normalization of `DATABASE_URL` for SQLAlchemy/Alembic:
  - `postgresql://...` → `postgresql+psycopg://...`
  - `postgres://...` → `postgresql+psycopg://...`
- Normalization happens once and is production-safe (idempotent)
- Original `DATABASE_URL` in environment variables remains unchanged
- You can continue using standard PostgreSQL URL formats (`postgresql://...` or `postgres://...`)

**Benefits:**
- `/ready` endpoint now correctly checks PostgreSQL connection
- Alembic migrations work without `psycopg2` dependency
- No need to manually specify `postgresql+psycopg://` in `DATABASE_URL`
- **psycopg2 is not used and not required** in the project

## 🧪 Tests

- Added 12 regression tests for URL normalization (`tests/test_database_url_normalization.py`)
- Tests cover all scenarios: None, empty strings, SQLite, PostgreSQL, complex URLs
- Tests do not require real PostgreSQL connection (pure string operations)
- All existing tests remain green (SQLite workflow unchanged)

## 📚 Documentation

- Updated `README.md` with "PostgreSQL + psycopg v3" section
- Documented automatic URL normalization behavior
- Added examples for `docker-compose` and `/ready` endpoint verification
- Clarified that `psycopg2` is not used and not needed

## 🛡️ Compatibility & Safety

### ✅ No Breaking Changes

- **SQLite workflow unchanged**: SQLite URLs (`sqlite://...`) are not normalized and work exactly as before
- **Environment variables unchanged**: Original `DATABASE_URL` in `.env` files is not modified
- **Backward compatible**: Existing PostgreSQL URLs continue to work without modification
- **Idempotent**: Normalization is safe to call multiple times

### ✅ Production-Safe

- Normalization replaces only the first occurrence of scheme prefix (prevents double replacement)
- Original URL preserved for `psycopg.connect()` (uses parsed parameters)
- Normalized URL used only for SQLAlchemy `create_engine()` and Alembic config
- No side effects on existing deployments

### ✅ Migration Notes

**For existing environments:**
- No changes required
- Continue using `DATABASE_URL=postgresql://...` or `DATABASE_URL=postgres://...`
- Code automatically normalizes for SQLAlchemy/Alembic

**For new environments:**
- Use standard PostgreSQL URL format (`postgresql://...` or `postgres://...`)
- Code automatically normalizes to use `psycopg v3`

## Technical Details

**Files changed:**
- `utils/db_url.py` (new): Centralized URL normalization helper
- `app/main.py`: Uses `normalize_database_url()` in `/ready` endpoint
- `alembic/env.py`: Uses `normalize_database_url()` for Alembic config
- `tests/test_database_url_normalization.py` (new): Regression tests
- `README.md`: Documentation updates

**Dependencies:**
- No new dependencies added
- `psycopg[binary]>=3.1.0` already in `requirements.txt`
- **psycopg2 is not required and not used**

## Verification

After deployment, verify PostgreSQL readiness:

```bash
curl http://localhost:8000/ready
# Expected: "database": "ok (postgresql)", "database_migration": "ok (revision: ...)"
```

Check that SQLAlchemy uses correct driver:

```bash
docker-compose exec app python -c "from sqlalchemy import create_engine; from utils.db_url import normalize_database_url; import os; db_url = normalize_database_url(os.getenv('DATABASE_URL', '')); engine = create_engine(db_url); print(f'Driver: {engine.driver}')"
# Expected: Driver: psycopg
```

---

**Full Changelog**: See commit history for detailed changes.
