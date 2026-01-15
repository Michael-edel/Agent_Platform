# Release Checklist

Pre-release verification for Agent Platform.

## Before Release

### 1. Tests
```bash
pytest -v
```
All tests must pass. No skipped security-related tests.

### 2. Docker Build
```bash
docker-compose build
docker-compose up -d
```
Verify containers start without errors.

### 3. Health Checks
```bash
# Liveness
curl http://localhost:8000/health

# Readiness (check driver and migrations)
curl http://localhost:8000/ready | jq .
```
Expected: `status: ok`, `database_driver: psycopg`, `database_migration: ok`.

### 4. Doctor Diagnostic
```bash
make doctor
# or in container:
docker exec -it agent-platform-app python scripts/doctor.py
```
Exit code must be 0.

### 5. Migration Status (PostgreSQL)
```bash
alembic current
alembic heads
make db-check
```
Current revision must match head.

### 6. Security Verification
- [ ] No secrets in git history (check recent commits)
- [ ] DATABASE_URL not logged in full (check logs)
- [ ] `/ready` does not expose credentials

### 7. Documentation
- [ ] README.md is up-to-date
- [ ] CHANGELOG.md updated with new changes
- [ ] docs/RUNBOOK.md reflects current behavior

### 8. Version Bump
- [ ] `app/__init__.py` `__version__` updated
- [ ] CHANGELOG.md has entry for new version

## Release

### 9. Tag & Push
```bash
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

### 10. Post-Release
- [ ] Verify CI/CD pipeline completed
- [ ] Check staging/production `/ready` endpoint
- [ ] Monitor logs for errors

## Rollback

If issues discovered:
```bash
# Revert to previous version
git revert HEAD
# or
docker-compose down
docker-compose pull  # previous image
docker-compose up -d
```

For database rollback:
```bash
alembic downgrade -1
```
