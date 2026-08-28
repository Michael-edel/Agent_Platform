"""Admin authentication dependency for internal/admin endpoints."""

import os
import logging
import secrets
from fastapi import HTTPException, Header
from typing import Optional

logger = logging.getLogger(__name__)


async def admin_auth(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")
) -> bool:
    """
    Dependency для проверки admin API key.
    
    ADMIN_API_KEY обязателен во всех окружениях.
    Требуется заголовок X-Admin-Key с совпадающим ключом.
    
    Args:
        x_admin_key: Значение заголовка X-Admin-Key
        
    Returns:
        True если авторизация успешна
        
    Raises:
        HTTPException если ключ неверный, отсутствует или не настроен
    """
    admin_api_key = os.getenv("ADMIN_API_KEY", "").strip()
    
    if not admin_api_key:
        logger.error("ADMIN_API_KEY not set; denying administrative API access")
        raise HTTPException(
            status_code=503,
            detail="Administrative API is not configured",
        )

    if not x_admin_key:
        logger.warning("X-Admin-Key header missing (ADMIN_API_KEY is set)")
        raise HTTPException(
            status_code=403,
            detail="X-Admin-Key header is required"
        )
    
    if not secrets.compare_digest(x_admin_key, admin_api_key):
        logger.warning(f"Invalid X-Admin-Key provided (expected length: {len(admin_api_key)})")
        raise HTTPException(
            status_code=403,
            detail="Invalid X-Admin-Key"
        )
    
    logger.debug("Admin authentication successful")
    return True
