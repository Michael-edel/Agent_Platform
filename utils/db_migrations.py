"""Утилиты для проверки миграций базы данных через Alembic."""

import logging
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)


def check_database_migration(
    engine,
    alembic_ini_path: str = "alembic.ini"
) -> Tuple[bool, str]:
    """
    Проверяет состояние миграций базы данных.
    
    Использует переданный SQLAlchemy engine для подключения к БД,
    гарантируя использование того же драйвера что и остальное приложение.
    
    Args:
        engine: SQLAlchemy Engine (уже сконфигурированный с нужным драйвером)
        alembic_ini_path: Путь к alembic.ini (по умолчанию "alembic.ini")
        
    Returns:
        Tuple[ok: bool, message: str]:
        - ok=True, message="ok (revision: abc123)" если миграции актуальны
        - ok=False, message="warning: ..." если есть проблемы
        
    Note:
        Функция НЕ запускает миграции, только проверяет состояние.
        Alembic импортируется lazy для оптимизации cold start.
    """
    try:
        # Lazy imports для оптимизации cold start
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import text
        
        current_rev: Optional[str] = None
        head_revs: List[str] = []
        
        # Получаем текущую revision из БД
        with engine.connect() as connection:
            try:
                context = MigrationContext.configure(connection)
                current_rev = context.get_current_revision()
            except Exception as ctx_error:
                # Fallback: прямой запрос к alembic_version
                logger.debug(f"MigrationContext failed, trying direct query: {ctx_error}")
                try:
                    result = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                    row = result.fetchone()
                    if row:
                        current_rev = row[0]
                except Exception:
                    # Таблица alembic_version не существует
                    current_rev = None
        
        # Получаем head revision(s) из скриптов миграций
        try:
            alembic_cfg = Config(alembic_ini_path)
            script = ScriptDirectory.from_config(alembic_cfg)
            
            # Поддержка multiple heads
            heads = script.get_heads()
            if heads:
                head_revs = list(heads)
            else:
                # Fallback на get_current_head() для single-head
                single_head = script.get_current_head()
                if single_head:
                    head_revs = [single_head]
        except Exception as script_error:
            # Не удалось загрузить скрипты миграций
            error_msg = str(script_error)[:100]
            logger.warning(f"Failed to load Alembic scripts: {error_msg}")
            return False, f"warning: cannot load migration scripts ({error_msg})"
        
        # Проверяем состояние
        if not head_revs:
            return True, "ok (no migrations defined)"
        
        if current_rev is None:
            # БД не инициализирована миграциями
            head_display = head_revs[0] if len(head_revs) == 1 else str(head_revs)
            return False, f"warning: database has no alembic revision (head: {head_display})"
        
        # Проверяем соответствие (поддержка multiple heads)
        if current_rev in head_revs:
            return True, f"ok (revision: {current_rev})"
        elif len(head_revs) == 1:
            return False, f"warning: pending migrations (db: {current_rev}, head: {head_revs[0]})"
        else:
            return False, f"warning: pending migrations (db: {current_rev}, heads: {head_revs})"
            
    except ImportError as ie:
        logger.warning(f"Alembic not available: {ie}")
        return False, "warning: alembic not installed"
    except Exception as e:
        # Безопасное форматирование ошибки (без паролей/DSN)
        error_msg = str(e)[:100]
        logger.warning(f"Migration check failed: {error_msg}")
        return False, f"warning: migration check error ({error_msg})"
