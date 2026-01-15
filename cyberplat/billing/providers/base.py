"""Provider abstraction for billing jobs (usage invoices)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Protocol


ProviderPaymentStatus = Literal["processing", "paid", "failed"]


@dataclass(frozen=True)
class ProviderCreateResult:
    provider_ref: str
    status: ProviderPaymentStatus
    raw: Optional[Dict[str, Any]] = None


class BillingProvider(Protocol):
    name: str

    def create_payment(self, invoice: Any) -> ProviderCreateResult:
        """
        Create payment / invoice / charge for a given UsageInvoice row.
        Must not return secrets in `raw`.
        """
        ...

