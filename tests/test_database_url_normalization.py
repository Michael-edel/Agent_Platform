"""Тесты для нормализации DATABASE_URL для psycopg v3."""

import pytest
from utils.db_url import normalize_database_url


class TestNormalizeDatabaseURL:
    """Тесты функции normalize_database_url."""
    
    def test_none_returns_none(self):
        """None должен вернуться как есть."""
        assert normalize_database_url(None) is None
    
    def test_empty_string_returns_empty(self):
        """Пустая строка должна вернуться как есть."""
        assert normalize_database_url("") == ""
        assert normalize_database_url("   ") == ""
    
    def test_sqlite_unchanged(self):
        """SQLite URL не должен изменяться."""
        url = "sqlite:///./test.db"
        assert normalize_database_url(url) == url
        
        url = "sqlite:///:memory:"
        assert normalize_database_url(url) == url
    
    def test_postgresql_normalized(self):
        """postgresql:// должен быть нормализован в postgresql+psycopg://."""
        original = "postgresql://user:pass@host:5432/db"
        expected = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_database_url(original) == expected
    
    def test_postgres_normalized(self):
        """postgres:// должен быть нормализован в postgresql+psycopg://."""
        original = "postgres://user:pass@host:5432/db"
        expected = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_database_url(original) == expected
    
    def test_already_normalized_unchanged(self):
        """postgresql+psycopg:// не должен изменяться."""
        url = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_database_url(url) == url
    
    def test_replace_only_once(self):
        """Замена должна происходить только один раз (первое вхождение)."""
        # URL с postgresql:// в пути не должен заменяться дважды
        original = "postgresql://user:pass@host:5432/postgresql://path"
        expected = "postgresql+psycopg://user:pass@host:5432/postgresql://path"
        result = normalize_database_url(original)
        assert result == expected
        # Проверяем, что второй postgresql:// не заменён
        assert "postgresql://path" in result
    
    def test_postgres_replace_only_once(self):
        """Замена postgres:// должна происходить только один раз."""
        original = "postgres://user:pass@host:5432/postgres://path"
        expected = "postgresql+psycopg://user:pass@host:5432/postgres://path"
        result = normalize_database_url(original)
        assert result == expected
        # Проверяем, что второй postgres:// не заменён
        assert "/postgres://path" in result
    
    def test_other_schemes_unchanged(self):
        """Другие схемы не должны изменяться."""
        # MySQL
        url = "mysql://user:pass@host:3306/db"
        assert normalize_database_url(url) == url
        
        # MongoDB
        url = "mongodb://user:pass@host:27017/db"
        assert normalize_database_url(url) == url
        
        # Любая другая схема
        url = "custom://user:pass@host:1234/db"
        assert normalize_database_url(url) == url
    
    def test_whitespace_handling(self):
        """Пробелы должны обрезаться."""
        url = "  postgresql://user:pass@host:5432/db  "
        expected = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_database_url(url) == expected
    
    def test_complex_postgresql_url(self):
        """Сложный PostgreSQL URL с параметрами должен нормализоваться корректно."""
        original = "postgresql://user:pass@host:5432/db?sslmode=require&connect_timeout=10"
        expected = "postgresql+psycopg://user:pass@host:5432/db?sslmode=require&connect_timeout=10"
        assert normalize_database_url(original) == expected
    
    def test_complex_postgres_url(self):
        """Сложный postgres:// URL с параметрами должен нормализоваться корректно."""
        original = "postgres://user:pass@host:5432/db?sslmode=require"
        expected = "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"
        assert normalize_database_url(original) == expected
