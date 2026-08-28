"""Bearer authentication with server-side roles and tenant scopes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Set

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


ALLOWED_ROLES: Set[str] = {"accountant", "approver", "director", "system"}
PUBLIC_PATHS = {"/health", "/ready", "/metrics"}


@dataclass(frozen=True)
class TokenIdentity:
    subject: str
    role: str
    tenant_ids: frozenset[str]


@dataclass(frozen=True)
class Actor:
    role: str
    subject: str


def _is_auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _parse_token_identities() -> dict[str, TokenIdentity]:
    """Parse API_TOKEN_CONFIG without trusting request-provided roles or tenants.

    Expected JSON format:
    {
      "secret-token": {
        "subject": "pilot-accountant",
        "role": "accountant",
        "tenants": ["tenant-1"]
      }
    }
    """
    raw = os.getenv("API_TOKEN_CONFIG", "").strip()
    if not raw:
        raise ValueError("AUTH_ENABLED=true требует API_TOKEN_CONFIG")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("API_TOKEN_CONFIG должен быть корректным JSON object") from exc

    if not isinstance(payload, dict):
        raise ValueError("API_TOKEN_CONFIG должен быть JSON object")

    identities: dict[str, TokenIdentity] = {}
    for token, value in payload.items():
        if not isinstance(token, str) or not token.strip() or not isinstance(value, dict):
            raise ValueError("API_TOKEN_CONFIG содержит некорректную запись")

        subject = value.get("subject")
        role = value.get("role")
        tenants = value.get("tenants")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Для каждого токена требуется непустой subject")
        if not isinstance(role, str) or role.strip().lower() not in ALLOWED_ROLES:
            raise ValueError("Для каждого токена требуется допустимая role")
        if not isinstance(tenants, list) or not tenants or not all(
            isinstance(tenant, str) and tenant.strip() for tenant in tenants
        ):
            raise ValueError("Для каждого токена требуется непустой список tenants")

        identities[token] = TokenIdentity(
            subject=subject.strip(),
            role=role.strip().lower(),
            tenant_ids=frozenset(tenant.strip() for tenant in tenants),
        )
    return identities


def get_actor(request: Request) -> Actor:
    """Return the middleware-verified identity for audit events."""
    return Actor(
        role=getattr(request.state, "actor_role", None) or "accountant",
        subject=getattr(request.state, "actor_subject", None) or "anonymous",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_enabled = _is_auth_enabled()
        request.state.auth_enabled = auth_enabled
        request.state.actor_subject = "anonymous"
        request.state.actor_role = "accountant"

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if not auth_enabled:
            return await call_next(request)

        try:
            identities = _parse_token_identities()
        except ValueError as exc:
            return JSONResponse(status_code=500, content={"detail": str(exc)})

        token = _extract_bearer_token(request.headers.get("Authorization"))
        identity = identities.get(token or "")
        if not identity:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        requested_tenants = [request.headers.get("X-Tenant-ID"), *request.query_params.getlist("tenant_id")]
        for requested_tenant in requested_tenants:
            if not requested_tenant:
                continue
            requested_tenant = requested_tenant.strip()
            if requested_tenant not in identity.tenant_ids and "*" not in identity.tenant_ids:
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        request.state.actor_subject = identity.subject
        request.state.actor_role = identity.role
        request.state.actor_tenant_ids = identity.tenant_ids
        return await call_next(request)


def require_roles(*allowed: str):
    allowed_set = {role.lower() for role in allowed}

    async def _dep(request: Request) -> None:
        if not _is_auth_enabled():
            return
        role = getattr(request.state, "actor_role", None)
        if not role or role.lower() not in allowed_set:
            raise HTTPException(status_code=403, detail="Forbidden")

    return _dep
