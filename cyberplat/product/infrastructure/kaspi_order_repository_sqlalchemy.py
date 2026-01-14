"""SQLAlchemy implementation для KaspiOrderRepository."""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from cyberplat.product.domain.interfaces import KaspiOrderRepository
from cyberplat.product.infrastructure.models import KaspiOrder

logger = logging.getLogger(__name__)


def _safe_payload(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Persist only safe, non-secret fields.
    We intentionally do NOT store signatures/secrets/tokens.
    """
    if not payload:
        return None

    safe_keys = {
        "id",
        "event_id",
        "type",
        "event_type",
        "order_id",
        "external_order_id",
        "status",
        "created_at",
        "paid_at",
        "amount_minor",
        "currency",
    }
    safe = {k: v for k, v in payload.items() if k in safe_keys}
    return json.dumps(safe, ensure_ascii=False)


class KaspiOrderRepositoryImpl(KaspiOrderRepository):
    def __init__(self, session: Session):
        self.session = session

    def create_order(self, tenant_id: str, plan_id: str, kaspi_order_id: str) -> str:
        order_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        order = KaspiOrder(
            id=order_id,
            tenant_id=tenant_id,
            plan_id=plan_id,
            kaspi_order_id=kaspi_order_id,
            status="created",
            created_at=now,
            updated_at=now,
            paid_at=None,
            last_error=None,
            raw_payload=None,
        )
        self.session.add(order)
        self.session.commit()
        return order_id

    def get_by_kaspi_order_id(self, kaspi_order_id: str) -> Optional[Dict[str, Any]]:
        order = self.session.query(KaspiOrder).filter(KaspiOrder.kaspi_order_id == kaspi_order_id).first()
        if not order:
            return None

        return {
            "id": order.id,
            "tenant_id": order.tenant_id,
            "plan_id": order.plan_id,
            "kaspi_order_id": order.kaspi_order_id,
            "status": order.status,
            "created_at": order.created_at,
            "updated_at": order.updated_at,
            "paid_at": order.paid_at,
            "last_error": order.last_error,
            "raw_payload": json.loads(order.raw_payload) if order.raw_payload else None,
        }

    def mark_paid(self, kaspi_order_id: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        order = self.session.query(KaspiOrder).filter(KaspiOrder.kaspi_order_id == kaspi_order_id).first()
        if not order:
            return False

        # Idempotency: if already paid, no-op
        if order.status == "paid":
            return True

        now = datetime.now().isoformat()
        order.status = "paid"
        order.paid_at = now
        order.updated_at = now
        order.last_error = None
        order.raw_payload = _safe_payload(payload)

        self.session.commit()
        return True

    def mark_failed(
        self,
        kaspi_order_id: str,
        error: str,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "failed",
    ) -> bool:
        order = self.session.query(KaspiOrder).filter(KaspiOrder.kaspi_order_id == kaspi_order_id).first()
        if not order:
            return False

        # Idempotency: if already paid, do not override
        if order.status == "paid":
            return True

        now = datetime.now().isoformat()
        order.status = status
        order.updated_at = now
        order.last_error = (error or "")[:500]
        order.raw_payload = _safe_payload(payload)

        self.session.commit()
        return True

