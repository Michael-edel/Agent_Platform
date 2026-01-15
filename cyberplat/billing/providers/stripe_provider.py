"""Stripe billing provider for usage invoice billing jobs."""

from __future__ import annotations

import os
from typing import Any, Dict

from cyberplat.billing.providers.base import ProviderCreateResult


class StripeBillingProvider:
    name = "stripe"

    def _get_api_key(self) -> str:
        # Prefer new name, keep backward compatible with existing config.
        key = os.getenv("STRIPE_API_KEY", "").strip()
        if not key:
            key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        return key

    def create_payment(self, invoice: Any) -> ProviderCreateResult:
        api_key = self._get_api_key()
        if not api_key:
            raise RuntimeError("Stripe misconfigured: STRIPE_API_KEY is missing")

        try:
            import stripe  # type: ignore
        except Exception as e:
            raise RuntimeError("Stripe SDK not installed") from e

        stripe.api_key = api_key

        currency = str(getattr(invoice, "currency", "kzt") or "kzt").lower()
        amount = int(getattr(invoice, "amount_cents", 0) or 0)
        if amount <= 0:
            raise ValueError("invoice amount_cents must be > 0")

        metadata: Dict[str, str] = {
            "tenant_id": str(getattr(invoice, "tenant_id", "")),
            "period": f"{int(getattr(invoice, 'period_year')):04d}-{int(getattr(invoice, 'period_month')):02d}",
            "usage_invoice_id": str(getattr(invoice, "id")),
        }

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            metadata=metadata,
        )

        intent_id = str(getattr(intent, "id", "") or "")
        if not intent_id:
            raise RuntimeError("Stripe PaymentIntent.create returned empty id")

        return ProviderCreateResult(
            provider_ref=intent_id,
            status="processing",
            raw={"id": intent_id, "object": "payment_intent"},
        )

