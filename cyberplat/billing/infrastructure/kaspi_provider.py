"""Kaspi payment provider adapter."""

import logging
from typing import Optional, Dict, Any
import hmac
import hashlib

from cyberplat.billing.domain.interfaces import PaymentProvider
from cyberplat.kaspi_client import (
    get_kaspi_client,
    create_checkout_session as kaspi_create_checkout,
    charge_token as kaspi_charge_token,
    verify_webhook_signature as kaspi_verify_signature
)

logger = logging.getLogger(__name__)


class KaspiPaymentProvider(PaymentProvider):
    """Адаптер для Kaspi payment provider."""
    
    @property
    def provider_name(self) -> str:
        return "kaspi"
    
    def create_checkout_session(
        self,
        amount_minor: int,
        currency: str,
        tenant_id: str,
        plan_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Создать Kaspi Checkout Session."""
        # Kaspi требует order_id в metadata
        order_id = None
        if metadata:
            order_id = metadata.get("order_id")
        
        if not order_id:
            logger.error("Kaspi checkout requires order_id in metadata")
            return None
        
        result = kaspi_create_checkout(
            amount_minor=amount_minor,
            currency=currency,
            tenant_id=tenant_id,
            plan_id=plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
            order_id=order_id
        )
        
        if result:
            return {
                "checkout_url": result["checkout_url"],
                "external_order_id": result["external_order_id"],
                "session_id": result["external_order_id"]  # Для совместимости
            }
        return None
    
    def charge_token(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        tenant_id: str,
        plan_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Списать средства с Kaspi токена."""
        result = kaspi_charge_token(
            token=token,
            amount_minor=amount_minor,
            currency=currency,
            tenant_id=tenant_id,
            plan_id=plan_id
        )
        
        if result:
            return {
                "external_order_id": result["external_order_id"],
                "status": result["status"]
            }
        return None
    
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        secret: str
    ) -> bool:
        """Проверить подпись Kaspi webhook."""
        return kaspi_verify_signature(payload, signature, secret)
