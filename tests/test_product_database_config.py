"""Tests for selecting the product runtime database."""

import pytest

from cyberplat.product.infrastructure import database


def _configure_compose_postgres(monkeypatch, *, password="p@ss:word"):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "agent_platform")
    monkeypatch.setenv("POSTGRES_USER", "app_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    monkeypatch.setattr(database, "_is_testing_mode", lambda: False)


def test_compose_settings_use_postgres_when_database_url_is_empty(monkeypatch):
    _configure_compose_postgres(monkeypatch)

    assert database.get_database_url() == (
        "postgresql+psycopg://app_user:p%40ss%3Aword@postgres:5432/agent_platform"
    )


def test_compose_settings_require_postgres_password(monkeypatch):
    _configure_compose_postgres(monkeypatch, password="")

    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        database.get_database_url()
