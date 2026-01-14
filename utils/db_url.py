"""Утилиты для нормализации DATABASE_URL для SQLAlchemy и psycopg v3."""

from typing import Optional


def normalize_database_url(url: Optional[str]) -> Optional[str]:
    """
    Normalizes DATABASE_URL for SQLAlchemy to use psycopg v3.
    
    SQLAlchemy по умолчанию использует psycopg2 при DATABASE_URL вида postgresql://...
    Но в проекте установлен psycopg v3 (psycopg[binary]), поэтому нужно явно указать драйвер.
    
    Правила нормализации:
    - postgresql://... -> postgresql+psycopg://... (замена только первого вхождения)
    - postgres://...   -> postgresql+psycopg://... (замена только первого вхождения)
    - postgresql+psycopg://... -> без изменений (уже нормализован)
    - sqlite://... -> без изменений (SQLite не требует нормализации)
    - None -> None (без изменений)
    - Пустая строка или строка из пробелов -> "" (после .strip())
    - Остальные строки -> обрезка пробелов (.strip()) + нормализация схемы
    
    Args:
        url: DATABASE_URL строка или None
        
    Returns:
        Нормализованный URL или None/пустая строка.
        Пробелы в начале и конце строки обрезаются (.strip()).
        
    Examples:
        >>> normalize_database_url("postgresql://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_database_url("postgres://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_database_url("postgresql+psycopg://user:pass@host:5432/db")
        'postgresql+psycopg://user:pass@host:5432/db'
        >>> normalize_database_url("sqlite:///./test.db")
        'sqlite:///./test.db'
        >>> normalize_database_url(None)
        >>> normalize_database_url("")
        ''
    """
    if not url:
        return url
    
    url = url.strip()
    
    # Если уже нормализован, вернуть как есть
    if url.startswith("postgresql+psycopg://"):
        return url
    
    # Нормализация postgresql:// -> postgresql+psycopg://
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    
    # Нормализация postgres:// -> postgresql+psycopg://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    
    # Для всех остальных схем (sqlite://, и т.д.) вернуть как есть
    return url
