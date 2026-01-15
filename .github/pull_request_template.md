## What

Brief description of changes.

## Why

Motivation and context.

## How to Test

```bash
# Run tests
pytest -v

# Check readiness
curl http://localhost:8000/ready

# Run doctor
make doctor
```

## Checklist

- [ ] Tests pass (`pytest -v`)
- [ ] No new linter warnings
- [ ] Documentation updated (README, RUNBOOK, CHANGELOG)
- [ ] No secrets or credentials in code
- [ ] Database migrations work (`alembic upgrade head`)

## Rollout / Backout

**Rollout:** Standard deploy via CI/CD.

**Backout:** `git revert` or redeploy previous version. If DB migration involved: `alembic downgrade -1`.
