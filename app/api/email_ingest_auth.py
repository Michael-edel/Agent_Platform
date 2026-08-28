"""Authentication for the public email-ingestion webhook."""

import logging
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


async def email_ingest_auth(
    x_email_ingest_key: Optional[str] = Header(None, alias="X-Email-Ingest-Key"),
) -> bool:
    """Require a separately configured shared secret for inbound email webhooks."""
    configured_key = os.getenv("EMAIL_INGEST_API_KEY", "").strip()

    if not configured_key:
        logger.error("EMAIL_INGEST_API_KEY not set; denying email ingestion")
        raise HTTPException(
            status_code=503,
            detail="Email ingestion is not configured",
        )

    if not x_email_ingest_key or not secrets.compare_digest(
        x_email_ingest_key, configured_key
    ):
        logger.warning("Invalid or missing email ingestion webhook key")
        raise HTTPException(
            status_code=403,
            detail="Valid X-Email-Ingest-Key header is required",
        )

    return True
