# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| main | Yes |
| < 0.2.0 | No |

## Reporting a Vulnerability

**Please do not open public issues for security vulnerabilities.**

Email: security@example.com (replace with actual contact)

Include in your report:
- Description of the vulnerability
- Steps to reproduce
- Affected version / commit
- Environment details
- Proof of concept (if available)

We aim to respond within 48 hours.

## Security Practices

### Credentials & Secrets

- All secrets via environment variables only
- No hardcoded credentials in code
- DATABASE_URL is masked in logs and `/ready` errors
- `scripts/doctor.py` never displays full DSN

### Database

- Uses **psycopg v3** (not psycopg2)
- Connection strings normalized server-side
- No credentials in error messages

### Logging

- Structured logging with level control
- Passwords/tokens never logged
- Error messages truncated to prevent credential leaks

### Pre-commit Hooks

- Block commits containing secrets patterns
- Block `.env`, `.key`, `.pem` files
- Verify Python syntax before commit

## Non-Goals

This project does NOT guarantee:
- Zero-downtime database migrations
- Automatic rollback on failed migrations

The `/ready` endpoint:
- Checks migration status only
- Does NOT execute migrations
- Does NOT perform destructive operations
