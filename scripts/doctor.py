#!/usr/bin/env python3
"""
Doctor: Diagnostic script for Agent Platform database configuration.

Checks:
- DATABASE_URL presence and normalization
- SQLAlchemy driver detection
- Database connection (if PostgreSQL)
- Alembic migration status

Usage:
    python scripts/doctor.py
    python scripts/doctor.py --json
    python scripts/doctor.py --alembic-ini custom/alembic.ini

Exit codes:
    0 - All checks passed
    1 - Hard error (cannot connect, configuration error)
    2 - Warning (pending migrations, degraded state)
"""

import argparse
import json
import sys
from typing import Optional, Dict, Any
from urllib.parse import urlparse


def mask_dsn(url: Optional[str]) -> str:
    """Mask credentials in database URL for safe display."""
    if not url:
        return "(not set)"
    
    try:
        parsed = urlparse(url)
        # Show scheme + host + dbname only
        host = parsed.hostname or "localhost"
        port = f":{parsed.port}" if parsed.port else ""
        dbname = parsed.path.lstrip("/") if parsed.path else "(default)"
        return f"{parsed.scheme}://{host}{port}/{dbname}"
    except Exception:
        # Fallback: show just scheme
        if "://" in url:
            return url.split("://")[0] + "://***"
        return "(invalid url)"


def run_doctor(alembic_ini: str = "alembic.ini", output_json: bool = False) -> int:
    """
    Run diagnostic checks.
    
    Returns:
        0 - OK
        1 - Error
        2 - Warning
    """
    results: Dict[str, Any] = {
        "status": "ok",
        "checks": {},
        "warnings": [],
        "errors": []
    }
    
    # Import utilities
    try:
        from utils.db_url import get_original_database_url, get_sqlalchemy_database_url
    except ImportError as e:
        results["status"] = "error"
        results["errors"].append(f"Cannot import utils.db_url: {e}")
        _output(results, output_json)
        return 1
    
    # Check DATABASE_URL
    original_url = get_original_database_url()
    sqlalchemy_url = get_sqlalchemy_database_url()
    
    results["checks"]["original_url_set"] = original_url is not None
    results["checks"]["original_url_masked"] = mask_dsn(original_url)
    results["checks"]["sqlalchemy_url_masked"] = mask_dsn(sqlalchemy_url)
    
    if not original_url:
        results["checks"]["mode"] = "sqlite/dev"
        results["warnings"].append("DATABASE_URL not set; assuming sqlite/dev mode")
        if not output_json:
            print("=" * 60)
            print("DOCTOR: Agent Platform Database Diagnostics")
            print("=" * 60)
            print()
            print("DATABASE_URL: (not set)")
            print("Mode: sqlite/dev (no PostgreSQL checks)")
            print()
            print("=" * 60)
            print("SUMMARY: OK (dev mode)")
            print("=" * 60)
        else:
            _output(results, output_json)
        return 0
    
    # Determine DB type
    is_postgres = (
        original_url.startswith("postgresql://") or
        original_url.startswith("postgres://") or
        original_url.startswith("postgresql+")
    )
    
    results["checks"]["db_type"] = "postgresql" if is_postgres else "other"
    
    # Create SQLAlchemy engine
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
        driver_name = engine.dialect.driver
        
        results["checks"]["sqlalchemy_driver"] = driver_name
        results["checks"]["engine_created"] = True
    except Exception as e:
        results["status"] = "error"
        results["errors"].append(f"Cannot create SQLAlchemy engine: {str(e)[:100]}")
        results["checks"]["engine_created"] = False
        _output(results, output_json)
        return 1
    
    # Test connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        results["checks"]["connection"] = "ok"
    except Exception as e:
        results["status"] = "error"
        results["errors"].append(f"Database connection failed: {str(e)[:100]}")
        results["checks"]["connection"] = "failed"
        engine.dispose()
        _output(results, output_json)
        return 1
    
    # Check migrations (PostgreSQL only)
    if is_postgres:
        try:
            from utils.db_migrations import check_database_migration
            
            migration_ok, migration_message = check_database_migration(engine, alembic_ini)
            results["checks"]["migration_ok"] = migration_ok
            results["checks"]["migration_message"] = migration_message
            
            if not migration_ok:
                results["status"] = "warning"
                results["warnings"].append(migration_message)
        except Exception as e:
            results["warnings"].append(f"Migration check failed: {str(e)[:100]}")
            results["checks"]["migration_ok"] = None
            results["checks"]["migration_message"] = f"check failed: {str(e)[:50]}"
    
    engine.dispose()
    
    # Output
    if not output_json:
        _print_human_readable(results)
    else:
        _output(results, output_json)
    
    # Exit code
    if results["status"] == "error":
        return 1
    elif results["status"] == "warning":
        return 2
    return 0


def _print_human_readable(results: Dict[str, Any]):
    """Print human-readable output."""
    print("=" * 60)
    print("DOCTOR: Agent Platform Database Diagnostics")
    print("=" * 60)
    print()
    
    checks = results["checks"]
    
    print(f"DATABASE_URL: {checks.get('original_url_masked', '(not set)')}")
    print(f"SQLAlchemy URL: {checks.get('sqlalchemy_url_masked', '(not set)')}")
    print(f"DB Type: {checks.get('db_type', 'unknown')}")
    print(f"SQLAlchemy Driver: {checks.get('sqlalchemy_driver', 'unknown')}")
    print(f"Connection: {checks.get('connection', 'not tested')}")
    
    if "migration_message" in checks:
        print(f"Migrations: {checks['migration_message']}")
    
    print()
    
    if results["warnings"]:
        print("WARNINGS:")
        for w in results["warnings"]:
            print(f"  - {w}")
        print()
    
    if results["errors"]:
        print("ERRORS:")
        for e in results["errors"]:
            print(f"  - {e}")
        print()
    
    status = results["status"].upper()
    print("=" * 60)
    print(f"SUMMARY: {status}")
    print("=" * 60)


def _output(results: Dict[str, Any], as_json: bool):
    """Output results."""
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        _print_human_readable(results)


def main():
    parser = argparse.ArgumentParser(
        description="Doctor: Diagnostic script for Agent Platform database configuration."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--alembic-ini",
        default="alembic.ini",
        help="Path to alembic.ini (default: alembic.ini)"
    )
    
    args = parser.parse_args()
    
    exit_code = run_doctor(
        alembic_ini=args.alembic_ini,
        output_json=args.json
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
