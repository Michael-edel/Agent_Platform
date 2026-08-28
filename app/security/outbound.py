"""Validation for outbound webhook destinations."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException


def validate_public_webhook_url(url: str) -> None:
    """Reject webhook destinations that directly target local or private networks.

    Host names remain permitted; deployments that require an allowlist should enforce
    it at the egress firewall or proxy as well.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Webhook URL must use http or https")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Webhook URL must not include credentials")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="Webhook URL must not target localhost")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return

    if any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    )):
        raise HTTPException(status_code=400, detail="Webhook URL must not target a private address")
