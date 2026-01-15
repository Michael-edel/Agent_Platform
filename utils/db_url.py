"""Утилиты для нормализации DATABASE_URL для SQLAlchemy и psycopg v3."""

import os
from typing import Optional


def normalize_sqlalchemy_database_url(url: Optional[str]) -> Optional[str]:
    """
    Normalizes DATABASE_URL for SQLAlchemy to use psycopg v3.
    
    SQLAlchemy по умолчанию использует psycopg2 при DATABASE_URL вида postgresql://...
    Но в проекте установлен psycopg v3 (psycopg[binary]), поэтому нужно явно указать драйвер.
    
    Правила нормализации:
    - None -> None
    - "" или whitespace -> None
    - postgresql+*://... -> без изменений (явный драйвер уже указан: asyncpg, psycopg, pg8000, etc.)
    - postgresql://... -> postgresql+psycopg://... (замена только первого вхождения)
    - postgres://...   -> postgresql+psycopg://... (замена только первого вхождения)
    - sqlite://... и другие схемы -> без изменений
    
    Args:
        url: DATABASE_URL строка или None
        
    Returns:
        Нормализованный URL или None.
        
    Examples:
        >>> normalize_sqlalchemy_database_url("postgresql://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_sqlalchemy_database_url("postgres://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_sqlalchemy_database_url("postgresql+psycopg://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_sqlalchemy_database_url("postgresql+asyncpg://user:pass@host:5432/db")
        'postgresql+asyncpg://user:pass@host:5432/db'
        >>> normalize_sqlalchemy_database_url("sqlite:///./test.db")
        'sqlite:///./test.db'
        >>> normalize_sqlalchemy_database_url(None)
        >>> normalize_sqlalchemy_database_url("")
    """
    if url is None:
        return None
    
    url = url.strip()
    if not url:
        return None
    
    # Если уже указан явный драйвер (postgresql+asyncpg://, postgresql+psycopg://, etc.)
    # НЕ модифицировать — пользователь явно выбрал драйвер
    if url.startswith("postgresql+"):
        return url
    
    # Нормализация postgresql:// -> postgresql+psycopg://
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    
    # Нормализация postgres:// -> postgresql+psycopg://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    
    # Для всех остальных схем (sqlite://, mysql://, и т.д.) вернуть как есть
    return url


def get_original_database_url() -> Optional[str]:
    """
    Получить оригинальный DATABASE_URL из окружения.
    
    Возвращает URL как есть, без нормализации.
    Используется для psycopg.connect() который не понимает postgresql+psycopg://.
    
    Returns:
        DATABASE_URL из os.environ или None если не задан/пустой.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    return url if url else None


def get_sqlalchemy_database_url() -> Optional[str]:
    """
    Получить нормализованный DATABASE_URL для SQLAlchemy.
    
    Читает DATABASE_URL из окружения и нормализует для psycopg v3.
    Используется для SQLAlchemy create_engine() и Alembic.
    
    Returns:
        Нормализованный DATABASE_URL или None если не задан.
    """
    return normalize_sqlalchemy_database_url(get_original_database_url())


# Backward compatibility alias
normalize_database_url = normalize_sqlalchemy_database_url
