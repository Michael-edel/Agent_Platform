"""Billing provider registry for billing jobs."""

from __future__ import annotations

from typing import Optional

from cyberplat.billing.providers.base import BillingProvider
from cyberplat.billing.providers.kaspi_provider import KaspiBillingProvider
from cyberplat.billing.providers.stripe_provider import StripeBillingProvider


_PROVIDERS: dict[str, BillingProvider] = {
    "stripe": StripeBillingProvider(),
    "kaspi": KaspiBillingProvider(),
}


def get_provider(provider_name: str) -> Optional[BillingProvider]:
    return _PROVIDERS.get((provider_name or "").strip().lower())

