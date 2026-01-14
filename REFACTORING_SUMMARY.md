# Billing System Refactoring - Summary

## What Has Been Created

### ✅ Domain Layer (Pure Business Logic)

**Location:** `cyberplat/billing/domain/`

1. **Domain Events** (`events.py`)
   - `SubscriptionActivated`
   - `SubscriptionRenewed`
   - `SubscriptionCanceled`
   - `PlanApplied`

2. **Domain Interfaces** (`interfaces.py`)
   - `PaymentProvider`: Abstract interface for payment providers
   - `SubscriptionRepository`: Interface for subscription storage operations
   - `WebhookEventRepository`: Interface for webhook event idempotency

### ✅ Application Layer (Use Cases)

**Location:** `cyberplat/billing/application/`

1. **ApplyPlanUseCase** (`apply_plan_use_case.py`)
   - Encapsulates logic for applying plans to tenants
   - Handles period calculation (defaults to 30 days)
   - Delegates to `SubscriptionRepository`

2. **ProcessWebhookUseCase** (`process_webhook_use_case.py`)
   - Handles webhook event processing
   - Verifies signatures via `PaymentProvider`
   - Ensures idempotency via `WebhookEventRepository`
   - Routes to event-specific handlers

### ✅ Infrastructure Layer (Adapters)

**Location:** `cyberplat/billing/infrastructure/`

#### Payment Provider Adapters

1. **StripePaymentProvider** (`stripe_provider.py`)
   - Implements `PaymentProvider` interface
   - Wraps `stripe_client.py` functions
   - Handles Stripe-specific signature verification
   - Converts Stripe API calls to domain interface

2. **KaspiPaymentProvider** (`kaspi_provider.py`)
   - Implements `PaymentProvider` interface
   - Wraps `kaspi_client.py` functions
   - Handles Kaspi-specific operations (checkout, token charging)
   - Converts Kaspi API calls to domain interface

#### Repository Adapters

3. **EntitlementSubscriptionRepository** (`repositories.py`)
   - Implements `SubscriptionRepository` interface
   - Wraps `EntitlementService` methods
   - Provides clean interface for subscription operations
   - Handles `get_active_subscriptions_for_renewal()` for recurring billing

4. **EntitlementWebhookEventRepository** (`repositories.py`)
   - Implements `WebhookEventRepository` interface
   - Wraps `EntitlementService` webhook event ledger
   - Ensures idempotency for webhook processing
   - Maintains event state (received, processed)

#### Event Handlers (Extracted Business Logic)

5. **Stripe Event Handlers** (`stripe_webhook_handlers.py`)
   - `handle_stripe_checkout_completed()`
   - `handle_stripe_invoice_paid()`
   - `handle_stripe_subscription_updated()`
   - `create_stripe_event_handlers()`: Factory function

6. **Kaspi Event Handlers** (`kaspi_webhook_handlers.py`)
   - `handle_kaspi_payment_paid()`
   - `handle_kaspi_payment_failed()`
   - `handle_kaspi_payment_canceled()`
   - `create_kaspi_event_handlers()`: Factory function with order lookup

## Architecture Benefits

### 1. **Reduced Coupling**
- Payment providers isolated behind `PaymentProvider` interface
- Webhook handlers don't directly depend on Stripe/Kaspi SDKs
- Business logic separated from infrastructure concerns

### 2. **Improved Testability**
- Interfaces can be easily mocked
- Use cases testable independently
- Event handlers testable without webhook infrastructure

### 3. **Clear Boundaries**
- **Domain**: Pure business logic, no dependencies
- **Application**: Use cases orchestrate domain logic
- **Infrastructure**: Adapters implement domain interfaces

### 4. **Easier Extension**
- New payment providers: implement `PaymentProvider` interface
- New event types: add handlers to event handler dict
- Repository changes: implement interface, no use case changes needed

## Backward Compatibility

✅ **All existing code continues to work:**
- `StripeWebhookHandler` unchanged (can be gradually refactored)
- `KaspiWebhookHandler` unchanged (can be gradually refactored)
- `BillingService` unchanged
- `EntitlementService` unchanged
- All existing tests should pass

## Next Steps (Gradual Migration)

### Phase 1: Refactor Webhook Handlers (Recommended Next)

Refactor `StripeWebhookHandler.handle_event()` to use `ProcessWebhookUseCase`:

```python
# In stripe_webhook_handler.py
from cyberplat.billing.infrastructure.stripe_provider import StripePaymentProvider
from cyberplat.billing.infrastructure.repositories import (
    EntitlementSubscriptionRepository,
    EntitlementWebhookEventRepository
)
from cyberplat.billing.application.process_webhook_use_case import ProcessWebhookUseCase
from cyberplat.billing.infrastructure.stripe_webhook_handlers import create_stripe_event_handlers

class StripeWebhookHandler:
    def __init__(self, entitlement_service, webhook_secret=None):
        self.entitlement_service = entitlement_service
        self.webhook_secret = webhook_secret
        
        # Initialize new structure
        provider = StripePaymentProvider()
        event_repo = EntitlementWebhookEventRepository(entitlement_service)
        subscription_repo = EntitlementSubscriptionRepository(entitlement_service)
        handlers = create_stripe_event_handlers()
        self.use_case = ProcessWebhookUseCase(
            provider, event_repo, subscription_repo, handlers
        )
    
    def handle_event(self, event: Dict[str, Any], raw_payload: bytes = None, signature: str = None):
        # Use new use case (maintains backward compatibility)
        return self.use_case.execute(
            event, raw_payload or b"", signature or "", self.webhook_secret or ""
        )
```

### Phase 2: Extract Recurring Billing

Move `charge_kaspi_subscriptions()` logic to use case:
- `RenewSubscriptionUseCase`
- Use `SubscriptionRepository.get_active_subscriptions_for_renewal()`
- Use `PaymentProvider.charge_token()`

### Phase 3: Consolidate Payment Profile Storage

Move provider-specific payment profile logic to adapters:
- Stripe: `StripePaymentProvider` handles customer_id storage
- Kaspi: `KaspiPaymentProvider` handles token storage
- Abstract through repository interface

### Phase 4: Remove Duplication

Identify shared patterns between Stripe/Kaspi handlers:
- Period calculation (30 days default)
- Metadata extraction
- Error handling patterns
- Create shared utilities

## Testing Strategy

1. **Unit Tests**: Test use cases with mocked repositories/providers
2. **Integration Tests**: Test adapters with real services
3. **Contract Tests**: Verify interface implementations
4. **End-to-End**: Test webhook handlers with new structure

## Files Modified

- ✅ Created: `cyberplat/billing/domain/` (new directory)
- ✅ Created: `cyberplat/billing/application/` (new directory)
- ✅ Created: `cyberplat/billing/infrastructure/` (new directory)
- ✅ No existing files modified (backward compatible)

## Files That Can Be Refactored (Future)

- `cyberplat/stripe_webhook_handler.py` - Can use `ProcessWebhookUseCase`
- `cyberplat/kaspi_webhook_handler.py` - Can use `ProcessWebhookUseCase`
- `cyberplat/kaspi_recurring.py` - Can use `RenewSubscriptionUseCase`
- `app/main.py` - Can use new abstractions in endpoints

## Invariants Maintained

✅ All invariants preserved:
1. Usage derived from events (unchanged)
2. Webhook idempotency (via `WebhookEventRepository`)
3. Subscription periods consistent (via `SubscriptionRepository`)
4. Multi-tenant isolation (all methods tenant-scoped)
5. External API unchanged (backward compatible)

## Example: Using New Structure

```python
# Initialize adapters
provider = StripePaymentProvider()
event_repo = EntitlementWebhookEventRepository(entitlement_service)
subscription_repo = EntitlementSubscriptionRepository(entitlement_service)

# Create use case
from cyberplat.billing.application.apply_plan_use_case import ApplyPlanUseCase
apply_plan = ApplyPlanUseCase(subscription_repo)

# Use case
apply_plan.execute(
    tenant_id="tenant-123",
    plan_id="plan_pro",
    provider="stripe",
    provider_customer_id="cus_xxx",
    status="active"
)
```

---

**Status:** Foundation complete. Ready for gradual migration.
**Tests:** All existing tests should pass (no breaking changes).
**Next:** Refactor webhook handlers one at a time, test after each change.
