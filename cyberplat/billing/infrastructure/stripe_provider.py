"""Stripe payment provider adapter."""

import logging
from typing import Optional, Dict, Any
import hmac
import hashlib

from cyberplat.billing.domain.interfaces import PaymentProvider
from cyberplat.stripe_client import (
    get_stripe_client,
    create_checkout_session as stripe_create_checkout,
    create_portal_session as stripe_create_portal
)

logger = logging.getLogger(__name__)


class StripePaymentProvider(PaymentProvider):
    """Адаптер для Stripe payment provider."""
    
    @property
    def provider_name(self) -> str:
        return "stripe"
    
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
        """Создать Stripe Checkout Session."""
        # Stripe использует price_id, а не amount_minor
        # Для совместимости с текущим API, metadata должен содержать price_id
        if metadata and "price_id" in metadata:
            price_id = metadata["price_id"]
        else:
            # Fallback: пытаемся получить price_id из плана
            from cyberplat.stripe_client import get_price_id_for_plan
            price_id = get_price_id_for_plan(plan_id)
        
        if not price_id:
            logger.error(f"No Stripe price_id found for plan {plan_id}")
            return None
        
        result = stripe_create_checkout(
            price_id=price_id,
            tenant_id=tenant_id,
            target_plan_id=plan_id,
            success_url=success_url,
            cancel_url=cancel_url
        )
        
        if result:
            return {
                "checkout_url": result["url"],
                "session_id": result["id"],
                "external_order_id": result["id"]  # Для совместимости
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
        """
        Stripe не использует token-based charging для subscriptions.
        Подписки управляются через Stripe Subscriptions API.
        """
        logger.warning("Stripe does not support token-based charging for subscriptions")
        return None
    
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        secret: str
    ) -> bool:
        """Проверить подпись Stripe webhook."""
        if not secret:
            logger.warning("Stripe webhook secret not set, skipping signature verification")
            return False
        
        try:
            # Stripe signature format: timestamp,signature1 signature2 ...
            sig_parts = signature.split(",")
            timestamp = None
            signatures = []
            
            for part in sig_parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    if key == "t":
                        timestamp = value
                    elif key == "v1":
                        signatures.append(value)
            
            if not timestamp or not signatures:
                logger.error("Invalid Stripe signature format")
                return False
            
            # Create expected signature
            signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
            expected_signature = hmac.new(
                secret.encode('utf-8'),
                signed_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures
            for sig in signatures:
                if hmac.compare_digest(expected_signature, sig):
                    return True
            
            logger.error("Stripe signature mismatch")
            return False
            
        except Exception as e:
            logger.error(f"Error verifying Stripe signature: {e}", exc_info=True)
            return False
    
    def create_portal_session(
        self,
        customer_id: str,
        return_url: str
    ) -> Optional[Dict[str, Any]]:
        """Создать Stripe Customer Portal Session."""
        result = stripe_create_portal(customer_id, return_url)
        if result:
            return {
                "portal_url": result["url"],
                "session_id": result["id"]
            }
        return None
