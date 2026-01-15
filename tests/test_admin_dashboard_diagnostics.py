"""Tests for admin dashboard system diagnostics."""

import pytest
from unittest.mock import MagicMock, patch


class TestBuildSystemDiagnostics:
    """Tests for build_system_diagnostics function."""

    def test_returns_required_keys(self):
        """Diagnostics contains all required keys."""
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            with patch("app.admin.dashboard.check_database_migration") as mock_check:
                mock_check.return_value = (True, "ok (revision: abc123)")
                
                from app.admin.dashboard import build_system_diagnostics
                result = build_system_diagnostics()
        
        assert "readiness_status" in result
        assert "database_driver" in result
        assert "migrations_status" in result
        assert "migrations_message" in result
        assert "error" in result

    def test_ok_status_when_migrations_current(self):
        """readiness_status is 'ok' when migrations are up to date."""
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            with patch("app.admin.dashboard.check_database_migration") as mock_check:
                mock_check.return_value = (True, "ok (revision: abc123)")
                
                from app.admin.dashboard import build_system_diagnostics
                result = build_system_diagnostics()
        
        assert result["readiness_status"] == "ok"
        assert result["migrations_status"] == "up_to_date"
        assert result["database_driver"] == "psycopg"

    def test_degraded_status_when_migrations_pending(self):
        """readiness_status is 'degraded' when migrations are pending."""
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            with patch("app.admin.dashboard.check_database_migration") as mock_check:
                mock_check.return_value = (False, "warning: pending migrations")
                
                from app.admin.dashboard import build_system_diagnostics
                result = build_system_diagnostics()
        
        assert result["readiness_status"] == "degraded"
        assert result["migrations_status"] == "pending"
        assert "pending" in result["migrations_message"]

    def test_unknown_status_on_exception(self):
        """readiness_status is 'unknown' when an exception occurs."""
        with patch("app.admin.dashboard.get_engine") as mock_get_engine:
            mock_get_engine.side_effect = Exception("Connection failed")
            
            from app.admin.dashboard import build_system_diagnostics
            result = build_system_diagnostics()
        
        assert result["readiness_status"] == "unknown"
        assert result["database_driver"] == "unknown"
        assert result["error"] is not None
        assert "Connection failed" in result["error"]

    def test_error_truncated_for_security(self):
        """Long error messages are truncated to avoid leaking secrets."""
        long_error = "postgresql://user:supersecretpassword@host/db " + "x" * 200
        
        with patch("app.admin.dashboard.get_engine") as mock_get_engine:
            mock_get_engine.side_effect = Exception(long_error)
            
            from app.admin.dashboard import build_system_diagnostics
            result = build_system_diagnostics()
        
        assert result["error"] is not None
        assert len(result["error"]) <= 100

    def test_does_not_raise_on_migration_check_failure(self):
        """Dashboard doesn't crash if migration check fails."""
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "sqlite"
        
        with patch("app.admin.dashboard.get_engine", return_value=mock_engine):
            with patch("app.admin.dashboard.check_database_migration") as mock_check:
                mock_check.side_effect = Exception("Alembic error")
                
                from app.admin.dashboard import build_system_diagnostics
                # Should not raise
                result = build_system_diagnostics()
        
        assert result["readiness_status"] == "unknown"
        assert result["error"] is not None
