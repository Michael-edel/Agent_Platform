"""Tests for doctor.py diagnostic script."""

import pytest
from scripts.doctor import mask_dsn, run_doctor


class TestMaskDsn:
    """Tests for DSN masking function."""

    def test_masks_password_postgresql(self):
        """Password is not visible in masked URL."""
        url = "postgresql://myuser:secretpassword123@db.example.com:5432/mydb"
        masked = mask_dsn(url)
        
        assert "secretpassword123" not in masked
        assert "myuser" not in masked
        assert "db.example.com" in masked
        assert "mydb" in masked
        assert "postgresql" in masked

    def test_masks_password_postgres(self):
        """Password is not visible in postgres:// URL."""
        url = "postgres://admin:super_secret@localhost:5432/app"
        masked = mask_dsn(url)
        
        assert "super_secret" not in masked
        assert "admin" not in masked
        assert "localhost" in masked
        assert "app" in masked

    def test_none_returns_not_set(self):
        """None URL returns '(not set)'."""
        assert mask_dsn(None) == "(not set)"

    def test_empty_returns_not_set(self):
        """Empty URL returns '(not set)'."""
        assert mask_dsn("") == "(not set)"

    def test_sqlite_url_safe(self):
        """SQLite URL is displayed safely."""
        url = "sqlite:///./test.db"
        masked = mask_dsn(url)
        
        assert "sqlite" in masked
        assert "test.db" in masked or "." in masked

    def test_complex_password_masked(self):
        """Complex passwords with special chars are masked."""
        url = "postgresql://user:p%40ss%3Dw0rd!@host/db"
        masked = mask_dsn(url)
        
        assert "p%40ss" not in masked
        assert "w0rd!" not in masked
        assert "host" in masked


class TestRunDoctor:
    """Tests for run_doctor function."""

    def test_no_database_url_returns_zero(self, monkeypatch):
        """Without DATABASE_URL, doctor returns 0 (dev mode)."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        
        exit_code = run_doctor(output_json=True)
        
        assert exit_code == 0

    def test_warning_on_pending_migrations(self, monkeypatch, tmp_path):
        """Doctor returns exit code 2 when migrations are pending."""
        from unittest.mock import MagicMock, patch
        
        # Set up a PostgreSQL URL
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        
        # Mock SQLAlchemy engine
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        mock_engine.connect.return_value.__enter__ = MagicMock()
        mock_engine.connect.return_value.__exit__ = MagicMock()
        
        # Patch at sqlalchemy module level (lazy import in run_doctor)
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            with patch("utils.db_migrations.check_database_migration") as mock_check:
                mock_check.return_value = (False, "warning: pending migrations (db: old, head: new)")
                
                exit_code = run_doctor(output_json=True)
                
                assert exit_code == 2

    def test_ok_when_migrations_current(self, monkeypatch, tmp_path):
        """Doctor returns 0 when everything is OK."""
        from unittest.mock import MagicMock, patch
        
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        mock_engine.connect.return_value.__enter__ = MagicMock()
        mock_engine.connect.return_value.__exit__ = MagicMock()
        
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            with patch("utils.db_migrations.check_database_migration") as mock_check:
                mock_check.return_value = (True, "ok (revision: abc123)")
                
                exit_code = run_doctor(output_json=True)
                
                assert exit_code == 0

    def test_error_on_connection_failure(self, monkeypatch):
        """Doctor returns 1 when database connection fails."""
        from unittest.mock import MagicMock, patch
        
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        
        mock_engine = MagicMock()
        mock_engine.dialect.driver = "psycopg"
        mock_engine.connect.side_effect = Exception("Connection refused")
        
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            exit_code = run_doctor(output_json=True)
            
            assert exit_code == 1
