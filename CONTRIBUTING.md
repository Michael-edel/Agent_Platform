# Contributing to Agent Platform

## Getting Started

```bash
# Python 3.13 required
python --version

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest -v

# Run locally
uvicorn app.main:app --reload

# Or with Docker
docker-compose up -d
```

## Project Principles

- **Clean Architecture**: domain / application / infrastructure separation
- **Event-driven**: async processing, event subscribers
- **Multi-tenant**: tenant isolation at data layer
- **Production-first**: logging, metrics, health checks from day one

## Database & Migrations

| Environment | Database |
|-------------|----------|
| Tests/Dev | SQLite |
| Staging/Production | PostgreSQL |

**DATABASE_URL formats supported:**
- `postgresql://user:pass@host/db` — auto-normalized
- `postgres://user:pass@host/db` — Heroku-style, auto-normalized
- `postgresql+psycopg://...` — explicit driver, used as-is

SQLAlchemy uses **psycopg v3** via automatic URL normalization. No psycopg2.

**Alembic commands:**
```bash
alembic upgrade head    # Apply migrations
alembic current         # Show current revision
alembic heads           # Show available heads
```

**Important:** `/ready` checks migration status but does NOT run migrations.

## Testing

```bash
pytest -v
```

- Tests run on SQLite — no PostgreSQL required
- New tests must be deterministic (no random, no time-dependent logic)
- Mock external services

## Code Style

- **PEP8** — keep it simple
- Use structured logging (`logging` module)
- **Never log secrets or full DSN**
- Prefer explicit over implicit

## Commits & PRs

- Small, focused commits
- One PR = one logical change
- Update `CHANGELOG.md` for user-visible changes
- Use the PR template (`.github/pull_request_template.md`)
- Run `pytest` before pushing

## Questions?

Open an issue or check `docs/RUNBOOK.md` for operations details.
