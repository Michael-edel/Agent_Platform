"""Admin audit logging helpers (no secrets)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from cyberplat.product.infrastructure.models import AdminAuditLog


SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "key"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_metadata(value: Any) -> Any:
    """Best-effort masking for sensitive keys in nested dict/list structures."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(s in lk for s in SENSITIVE_KEYS):
                out[str(k)] = "***"
            else:
                out[str(k)] = mask_metadata(v)
        return out
    if isinstance(value, list):
        return [mask_metadata(v) for v in value]
    return value


def write_audit_log(
    session: Session,
    *,
    actor_username: str,
    actor_role: str,
    tenant_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    session.add(
        AdminAuditLog(
            id=str(uuid.uuid4()),
            created_at=_now_iso(),
            actor_username=actor_username or "unknown",
            actor_role=actor_role or "unknown",
            tenant_id=tenant_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            metadata_json=mask_metadata(metadata) if metadata else None,
        )
    )

