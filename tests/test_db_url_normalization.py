"""Regression tests for DATABASE_URL normalization for psycopg v3."""

import os
import pytest
from utils.db_url import (
    normalize_sqlalchemy_database_url,
    get_original_database_url,
    get_sqlalchemy_database_url,
)


class TestNormalizeSqlalchemyDatabaseUrl:
    """Tests for normalize_sqlalchemy_database_url function."""

    def test_none_returns_none(self):
        """None input returns None."""
        assert normalize_sqlalchemy_database_url(None) is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert normalize_sqlalchemy_database_url("") is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only string returns None."""
        assert normalize_sqlalchemy_database_url("   ") is None
        assert normalize_sqlalchemy_database_url("\t\n") is None

    def test_postgresql_prefix_normalized(self):
        """postgresql:// is normalized to postgresql+psycopg://."""
        url = "postgresql://user:pass@host:5432/db"
        expected = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == expected

    def test_postgres_prefix_normalized(self):
        """postgres:// is normalized to postgresql+psycopg://."""
        url = "postgres://user:pass@host:5432/db"
        expected = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == expected

    def test_already_normalized_unchanged(self):
        """postgresql+psycopg:// URLs are returned unchanged."""
        url = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == url

    def test_explicit_asyncpg_driver_unchanged(self):
        """postgresql+asyncpg:// URLs are returned unchanged (explicit driver)."""
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == url

    def test_explicit_pg8000_driver_unchanged(self):
        """postgresql+pg8000:// URLs are returned unchanged (explicit driver)."""
        url = "postgresql+pg8000://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == url

    def test_explicit_psycopg2_driver_unchanged(self):
        """postgresql+psycopg2:// URLs are returned unchanged (explicit driver)."""
        url = "postgresql+psycopg2://user:pass@host:5432/db"
        assert normalize_sqlalchemy_database_url(url) == url

    def test_explicit_drivers_not_modified(self):
        """Any postgresql+<driver>:// URL is returned unchanged."""
        urls = [
            "postgresql+asyncpg://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
            "postgresql+psycopg2://user:pass@host/db",
            "postgresql+pg8000://user:pass@host/db",
            "postgresql+aiopg://user:pass@host/db",
        ]
        for url in urls:
            assert normalize_sqlalchemy_database_url(url) == url, f"URL was modified: {url}"

    def test_sqlite_unchanged(self):
        """SQLite URLs are returned unchanged."""
        url = "sqlite:///./test.db"
        assert normalize_sqlalchemy_database_url(url) == url
        
        url_memory = "sqlite:///:memory:"
        assert normalize_sqlalchemy_database_url(url_memory) == url_memory

    def test_only_first_occurrence_replaced(self):
        """Only the first occurrence of postgresql:// is replaced."""
        # URL with postgresql:// in the query string (edge case)
        url = "postgresql://user:pass@host/db?options=postgresql://other"
        result = normalize_sqlalchemy_database_url(url)
        # First postgresql:// should be replaced, second should remain
        assert result == "postgresql+psycopg://user:pass@host/db?options=postgresql://other"
        assert result.count("postgresql+psycopg://") == 1
        assert result.count("postgresql://") == 1

    def test_only_first_occurrence_replaced_postgres(self):
        """Only the first occurrence of postgres:// is replaced."""
        url = "postgres://user:pass@host/db?fallback=postgres://backup"
        result = normalize_sqlalchemy_database_url(url)
        assert result == "postgresql+psycopg://user:pass@host/db?fallback=postgres://backup"
        assert result.count("postgresql+psycopg://") == 1
        assert result.count("postgres://") == 1

    def test_strips_whitespace(self):
        """Leading/trailing whitespace is stripped."""
        url = "  postgresql://user:pass@host/db  "
        expected = "postgresql+psycopg://user:pass@host/db"
        assert normalize_sqlalchemy_database_url(url) == expected

    def test_complex_url_with_special_chars(self):
        """Complex URLs with special characters are handled correctly."""
        url = "postgresql://user:p%40ssw0rd@host.example.com:5432/my_db?sslmode=require"
        expected = "postgresql+psycopg://user:p%40ssw0rd@host.example.com:5432/my_db?sslmode=require"
        assert normalize_sqlalchemy_database_url(url) == expected

    def test_other_schemes_unchanged(self):
        """Other database schemes are returned unchanged."""
        urls = [
            "mysql://user:pass@host/db",
            "mysql+pymysql://user:pass@host/db",
            "oracle://user:pass@host/db",
            "mssql+pyodbc://user:pass@host/db",
        ]
        for url in urls:
            assert normalize_sqlalchemy_database_url(url) == url


class TestGetOriginalDatabaseUrl:
    """Tests for get_original_database_url function."""

    def test_returns_env_value(self, monkeypatch):
        """Returns DATABASE_URL from environment."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        assert get_original_database_url() == "postgresql://user:pass@host/db"

    def test_strips_whitespace(self, monkeypatch):
        """Strips whitespace from DATABASE_URL."""
        monkeypatch.setenv("DATABASE_URL", "  postgresql://host/db  ")
        assert get_original_database_url() == "postgresql://host/db"

    def test_empty_returns_none(self, monkeypatch):
        """Empty DATABASE_URL returns None."""
        monkeypatch.setenv("DATABASE_URL", "")
        assert get_original_database_url() is None

    def test_whitespace_only_returns_none(self, monkeypatch):
        """Whitespace-only DATABASE_URL returns None."""
        monkeypatch.setenv("DATABASE_URL", "   ")
        assert get_original_database_url() is None

    def test_unset_returns_none(self, monkeypatch):
        """Unset DATABASE_URL returns None."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert get_original_database_url() is None


class TestGetSqlalchemyDatabaseUrl:
    """Tests for get_sqlalchemy_database_url function."""

    def test_returns_normalized_url(self, monkeypatch):
        """Returns normalized DATABASE_URL."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        assert get_sqlalchemy_database_url() == "postgresql+psycopg://user:pass@host/db"

    def test_postgres_prefix_normalized(self, monkeypatch):
        """postgres:// prefix is normalized."""
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host/db")
        assert get_sqlalchemy_database_url() == "postgresql+psycopg://user:pass@host/db"

    def test_already_normalized_unchanged(self, monkeypatch):
        """Already normalized URLs are unchanged."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@host/db")
        assert get_sqlalchemy_database_url() == "postgresql+psycopg://user:pass@host/db"

    def test_unset_returns_none(self, monkeypatch):
        """Unset DATABASE_URL returns None."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert get_sqlalchemy_database_url() is None

    def test_sqlite_unchanged(self, monkeypatch):
        """SQLite URLs are unchanged."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
        assert get_sqlalchemy_database_url() == "sqlite:///./test.db"

    def test_explicit_asyncpg_driver_unchanged(self, monkeypatch):
        """Explicit asyncpg driver is not modified."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host/db")
        assert get_sqlalchemy_database_url() == "postgresql+asyncpg://user:pass@host/db"

    def test_explicit_pg8000_driver_unchanged(self, monkeypatch):
        """Explicit pg8000 driver is not modified."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://user:pass@host/db")
        assert get_sqlalchemy_database_url() == "postgresql+pg8000://user:pass@host/db"
