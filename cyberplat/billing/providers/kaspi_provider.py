"""Kaspi billing provider for usage invoice billing jobs.

NOTE: Real create-invoice API is not wired yet (14A.2 allows placeholder).
"""

from __future__ import annotations

from typing import Any

from cyberplat.billing.providers.base import ProviderCreateResult


class KaspiBillingProvider:
    name = "kaspi"

    def create_payment(self, invoice: Any) -> ProviderCreateResult:
        # This repo currently has Kaspi checkout/integration focused on plan upgrades.
        # Usage-invoice payment creation will be implemented in the next step.
        raise NotImplementedError("Kaspi usage invoice payment creation not implemented yet")

