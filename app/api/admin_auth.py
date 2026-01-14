"""Admin authentication dependency for internal/admin endpoints."""

import os
import logging
from fastapi import HTTPException, Header
from typing import Optional

logger = logging.getLogger(__name__)


async def admin_auth(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")
) -> bool:
    """
    Dependency для проверки admin API key.
    
    Если ADMIN_API_KEY не задан (dev режим), endpoint доступен без ключа.
    Если ADMIN_API_KEY задан, требуется заголовок X-Admin-Key.
    
    Args:
        x_admin_key: Значение заголовка X-Admin-Key
        
    Returns:
        True если авторизация успешна
        
    Raises:
        HTTPException 403 если ключ неверный или отсутствует (в prod режиме)
    """
    admin_api_key = os.getenv("ADMIN_API_KEY", "").strip()
    
    # Dev режим: если ADMIN_API_KEY не задан, разрешаем доступ без ключа
    if not admin_api_key:
        logger.debug("ADMIN_API_KEY not set, allowing access without key (dev mode)")
        return True
    
    # Prod режим: требуется валидный ключ
    if not x_admin_key:
        logger.warning("X-Admin-Key header missing (ADMIN_API_KEY is set)")
        raise HTTPException(
            status_code=403,
            detail="X-Admin-Key header is required"
        )
    
    if x_admin_key != admin_api_key:
        logger.warning(f"Invalid X-Admin-Key provided (expected length: {len(admin_api_key)})")
        raise HTTPException(
            status_code=403,
            detail="Invalid X-Admin-Key"
        )
    
    logger.debug("Admin authentication successful")
    return True
