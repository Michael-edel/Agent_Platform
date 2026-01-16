"""Database session management for product layer."""

import os
import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from utils.db_url import normalize_sqlalchemy_database_url

logger = logging.getLogger(__name__)

# Global engine and sessionmaker (initialized on first use)
_engine = None
_engine_url = None
_SessionLocal = None


def _is_testing_mode() -> bool:
    """Проверка, запущен ли код в тестовом режиме."""
    return os.getenv("CYBERPLAT_TESTING") == "1" or os.getenv("PYTEST_CURRENT_TEST") is not None


def get_database_url() -> str:
    """Получить нормализованный DATABASE_URL для SQLAlchemy."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    
    # В тестовом режиме принудительно используем SQLite (игнорируем non-sqlite DATABASE_URL)
    if _is_testing_mode():
        if not database_url or not database_url.startswith("sqlite"):
            # Игнорируем non-sqlite DATABASE_URL в тестах, используем SQLite
            db_path = os.getenv("PLATFORM_DB_PATH", "platform.db")
            # Преобразуем относительный путь в абсолютный для SQLite
            if not os.path.isabs(db_path):
                db_path = os.path.abspath(db_path)
            database_url = f"sqlite:///{db_path}"
        # Если уже sqlite:// - используем как есть
    elif not database_url:
        # Fallback для dev (SQLite) - используем тот же путь, что и legacy сервисы
        db_path = os.getenv("PLATFORM_DB_PATH", "platform.db")
        # Преобразуем относительный путь в абсолютный для SQLite
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
        database_url = f"sqlite:///{db_path}"
    
    # Нормализация для PostgreSQL (psycopg v3) - только в non-test режиме
    if not _is_testing_mode():
        normalized = normalize_sqlalchemy_database_url(database_url)
        return normalized or database_url
    
    # В тестах возвращаем как есть (уже sqlite://)
    return database_url


def get_engine():
    """Получить или создать SQLAlchemy engine."""
    global _engine, _engine_url, _SessionLocal
    database_url = get_database_url()
    # Recreate engine if DATABASE_URL changes (tests / runtime config changes).
    if _engine is None or _engine_url != database_url:
        if _engine is not None:
            try:
                _engine.dispose()
                logger.info("Disposed previous SQLAlchemy engine due to URL change")
            except Exception:
                logger.warning("Failed to dispose previous SQLAlchemy engine", exc_info=True)
        # Reset sessionmaker to bind to the new engine
        _SessionLocal = None
        _engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {}
        )
        _engine_url = database_url
        # Логируем только схему и хост, без credentials
        from urllib.parse import urlparse
        parsed = urlparse(database_url)
        safe_url = f"{parsed.scheme}://{parsed.hostname or 'localhost'}/..."
        logger.info(f"SQLAlchemy engine created: {safe_url}")

        # В тестовом режиме для SQLite автоматически создаём schema product layer (artifact_states и др.).
        # В production поведение не меняем (никакого auto-create).
        if _is_testing_mode() and database_url.startswith("sqlite"):
            try:
                # Import inside to avoid circular imports
                from cyberplat.product.infrastructure.models import Base

                Base.metadata.create_all(bind=_engine)
                logger.info("В тестовом режиме схема product layer для SQLite создана автоматически (create_all).")
            except Exception:
                logger.warning("Не удалось автоматически создать schema product layer для SQLite в тестовом режиме")
    return _engine


def get_sessionmaker():
    """Получить или создать sessionmaker."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        logger.info("Sessionmaker created")
    return _SessionLocal


def get_db_session() -> Generator[Session, None, None]:
    """
    Dependency для получения DB session (FastAPI dependency).
    
    Usage:
        @app.get("/endpoint")
        def endpoint(db: Session = Depends(get_db_session)):
            ...
    """
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
