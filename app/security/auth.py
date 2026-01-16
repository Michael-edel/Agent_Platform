"""Opt-in bearer auth + RBAC for pilot deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional, Set

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


ALLOWED_ROLES: Set[str] = {"accountant", "approver", "system"}


@dataclass(frozen=True)
class Actor:
    role: str
    subject: str


def _is_auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def _parse_tokens() -> list[str]:
    raw_many = os.getenv("API_TOKENS", "")
    raw_one = os.getenv("API_TOKEN", "")
    tokens: list[str] = []
    if raw_many.strip():
        tokens.extend([t.strip() for t in raw_many.split(",") if t.strip()])
    if raw_one.strip():
        tokens.append(raw_one.strip())
    # De-dup while keeping order
    out: list[str] = []
    for t in tokens:
        if t not in out:
            out.append(t)
    return out


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _token_subject(token: str, valid_tokens: list[str]) -> str:
    try:
        idx = valid_tokens.index(token)
        return f"token-{idx + 1}"
    except Exception:
        return "token"


def _extract_role(request: Request) -> Optional[str]:
    role = request.headers.get("X-Role") or request.headers.get("X-User-Role")
    if not role:
        return None
    role = role.strip().lower()
    return role or None


def get_actor(request: Request) -> Actor:
    """Get actor for events. When auth is off, uses best-effort defaults."""
    role = _extract_role(request) or "accountant"
    subject = getattr(request.state, "actor_subject", None) or "anonymous"
    role = role.lower()
    if role not in ALLOWED_ROLES:
        role = "accountant"
    return Actor(role=role, subject=subject)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_enabled = _is_auth_enabled()
        request.state.auth_enabled = auth_enabled

        # Default actor subject (even when auth is off)
        request.state.actor_subject = "anonymous"

        # Skip auth for basic health/metrics endpoints
        if request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)

        if not auth_enabled:
            return await call_next(request)

        tokens = _parse_tokens()
        if not tokens:
            return JSONResponse(
                status_code=500,
                content={"detail": "AUTH_ENABLED=true, но не задан API_TOKEN/API_TOKENS"},
            )

        token = _extract_bearer_token(request.headers.get("Authorization"))
        if not token or token not in tokens:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        request.state.actor_subject = _token_subject(token, tokens)

        role = _extract_role(request)
        if not role or role not in ALLOWED_ROLES:
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        request.state.actor_role = role
        return await call_next(request)


def require_roles(*allowed: str):
    allowed_set = {r.lower() for r in allowed}

    async def _dep(request: Request) -> None:
        if not _is_auth_enabled():
            return

        role = getattr(request.state, "actor_role", None) or _extract_role(request)
        if not role:
            raise HTTPException(status_code=403, detail="Forbidden")
        role = role.lower()
        if role not in allowed_set:
            raise HTTPException(status_code=403, detail="Forbidden")

    return _dep

