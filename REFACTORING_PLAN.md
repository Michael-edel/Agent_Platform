# Billing System Refactoring Plan

## Overview

Refactoring goal: Reduce coupling between payment providers (Stripe/Kaspi) and core billing domain, introduce clean architecture boundaries while maintaining all existing functionality and invariants.

## Architecture Layers Created

### 1. Domain Layer (`cyberplat/billing/domain/`)

**Pure business logic - no dependencies on infrastructure**

- `events.py`: Domain events (`SubscriptionActivated`, `SubscriptionRenewed`, `SubscriptionCanceled`, `PlanApplied`)
- `interfaces.py`: Domain interfaces (ports) that define contracts:
  - `PaymentProvider`: Interface for payment providers
  - `SubscriptionRepository`: Interface for subscription storage
  - `WebhookEventRepository`: Interface for webhook event idempotency

### 2. Application Layer (`cyberplat/billing/application/`)

**Use cases - orchestrate domain logic**

- `apply_plan_use_case.py`: `ApplyPlanUseCase` - encapsulates logic for applying plans to tenants
- `process_webhook_use_case.py`: `ProcessWebhookUseCase` - handles webhook event processing with idempotency

### 3. Infrastructure Layer (`cyberplat/billing/infrastructure/`)

**Adapters - implement domain interfaces, wrap external systems**

- `stripe_provider.py`: `StripePaymentProvider` - implements `PaymentProvider` interface
- `kaspi_provider.py`: `KaspiPaymentProvider` - implements `PaymentProvider` interface  
- `repositories.py`: Repository adapters wrapping existing services:
  - `EntitlementSubscriptionRepository` - wraps `EntitlementService`
  - `EntitlementWebhookEventRepository` - wraps `EntitlementService` webhook ledger
- `stripe_webhook_handlers.py`: Extracted Stripe-specific event handlers (business logic separated from infrastructure)

## Current State

✅ **Completed:**
- Domain interfaces and events created
- Payment provider interfaces implemented (Stripe, Kaspi)
- Repository adapters created (wrapping existing services)
- Application use cases created
- Stripe event handlers extracted to separate file

⏳ **In Progress:**
- Existing webhook handlers still use old structure (backward compatible)
- Gradually refactoring handlers to use new abstractions

## Refactoring Strategy

### Phase 1: Create Abstractions ✅
- Create domain interfaces
- Implement provider adapters
- Create repository adapters

### Phase 2: Extract Business Logic (Current)
- Extract event handlers from webhook handlers
- Create use cases
- Keep webhook handlers backward compatible

### Phase 3: Refactor Webhook Handlers (Next)
- Refactor `StripeWebhookHandler` to use `ProcessWebhookUseCase`
- Refactor `KaspiWebhookHandler` to use `ProcessWebhookUseCase`
- Maintain backward compatibility with existing API

### Phase 4: Consolidate Duplication
- Extract shared webhook processing logic
- Create shared event handler patterns
- Reduce code duplication between Stripe/Kaspi

### Phase 5: Move Provider-Specific Logic
- Move all provider-specific code to infrastructure adapters
- Ensure domain layer has zero dependencies on Stripe/Kaspi

## Invariants Preserved

1. ✅ **Usage derived from events**: `BillingSubscriber` unchanged, continues to process events
2. ✅ **Idempotency**: `WebhookEventRepository` interface maintains idempotency checks
3. ✅ **Subscription consistency**: `SubscriptionRepository` interface preserves period/renewal logic
4. ✅ **Multi-tenant isolation**: All repository methods tenant-scoped
5. ✅ **External API unchanged**: Webhook handlers maintain same public API

## Testing Strategy

- All existing tests should continue to pass
- Repository adapters wrap existing services (no behavior change)
- New abstractions can be tested independently
- Integration tests validate end-to-end flow

## Next Steps

1. Create Kaspi webhook handlers (similar to Stripe)
2. Refactor `StripeWebhookHandler.handle_event()` to use `ProcessWebhookUseCase`
3. Refactor `KaspiWebhookHandler.handle_event()` to use `ProcessWebhookUseCase`
4. Test each refactoring step
5. Extract recurring billing logic to use case
6. Move provider-specific payment profile storage to adapters

## Example: Using New Structure

```python
# Old way (still works):
handler = StripeWebhookHandler(entitlement_service, webhook_secret)
success, tenant_id, error = handler.handle_event(event)

# New way (gradual adoption):
from cyberplat.billing.infrastructure.stripe_provider import StripePaymentProvider
from cyberplat.billing.infrastructure.repositories import (
    EntitlementSubscriptionRepository,
    EntitlementWebhookEventRepository
)
from cyberplat.billing.application.process_webhook_use_case import ProcessWebhookUseCase
from cyberplat.billing.infrastructure.stripe_webhook_handlers import create_stripe_event_handlers

provider = StripePaymentProvider()
event_repo = EntitlementWebhookEventRepository(entitlement_service)
subscription_repo = EntitlementSubscriptionRepository(entitlement_service)
handlers = create_stripe_event_handlers()
use_case = ProcessWebhookUseCase(provider, event_repo, subscription_repo, handlers)
success, tenant_id, error = use_case.execute(event, raw_payload, signature, webhook_secret)
```

## Benefits

1. **Reduced Coupling**: Payment providers isolated behind interfaces
2. **Testability**: Interfaces can be mocked, use cases tested independently
3. **Flexibility**: Easy to add new payment providers
4. **Clear Boundaries**: Domain, application, infrastructure clearly separated
5. **Maintainability**: Business logic separated from infrastructure concerns
