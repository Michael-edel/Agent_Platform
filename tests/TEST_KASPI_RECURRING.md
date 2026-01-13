# Kaspi Recurring Subscriptions - Test Documentation

## Overview

This test suite validates that Kaspi recurring subscriptions (autocharge) work correctly in a production SaaS environment.

## Test Scenarios

### Test 1: Initial Paid Subscription (Month 1)
**Simulates:** Kaspi webhook → payment.paid

**Verifies:**
- ✅ `billing_orders.status = "paid"`
- ✅ `tenant_subscriptions.status = "active"`
- ✅ `plan_id = "plan_pro"`
- ✅ `billing_plan_limits` applied
- ✅ `kaspi_token` saved in `billing_tenant_payment_profiles`

### Test 2: Recurring Charge Success (Month 2)
**Simulates:** Successful autocharge via `charge_kaspi_subscriptions()`

**Verifies:**
- ✅ New `billing_orders` row created with `status = "paid"`
- ✅ `tenant_subscriptions` remains `active`
- ✅ No downgrade to free plan
- ✅ Subscription period extended by 30 days
- ✅ `billing_webhook_events` contains renewal record

### Test 3: Recurring Charge Failure (Month 3)
**Simulates:** Failed autocharge → automatic downgrade

**Verifies:**
- ✅ `billing_orders.status = "failed"` (audit record created)
- ✅ `tenant_subscriptions.status = "canceled"`
- ✅ Tenant downgraded to `plan_free`
- ✅ `billing_plan_limits` updated to free plan quotas
- ✅ Quotas restricted to free plan limits

### Test 4: Idempotency
**Verifies:**
- ✅ Running `charge_kaspi_subscriptions()` twice doesn't double-charge
- ✅ Period extension prevents duplicate charges

### Test 5: Tenant Isolation
**Verifies:**
- ✅ Charging one tenant doesn't affect another
- ✅ Each tenant has isolated orders and subscriptions
- ✅ Plans are correctly applied per tenant

### Test 6: Webhook Events Consistency
**Verifies:**
- ✅ `billing_webhook_events` and `billing_orders` are consistent
- ✅ Webhook processing is idempotent

### Test 7: Full Lifecycle (Month 1 → Month 2 → Month 3)
**Simulates:** Complete subscription lifecycle

**Verifies:**
- ✅ Month 1: Initial payment → `plan_pro` active
- ✅ Month 2: Successful autocharge → `plan_pro` remains active
- ✅ Month 3: Failed autocharge → downgrade to `plan_free`, status `canceled`
- ✅ Audit trail: 2 paid orders + 1 failed order

## Running Tests

```bash
# Install pytest if not already installed
pip install pytest

# Run all Kaspi recurring tests
pytest tests/test_kaspi_recurring.py -v

# Run specific test
pytest tests/test_kaspi_recurring.py::TestKaspiRecurringSubscriptions::test_1_initial_paid_subscription -v
```

## Expected Results

All tests should pass, proving that:
1. ✅ Kaspi recurring subscriptions work correctly
2. ✅ Idempotency is preserved
3. ✅ Tenant isolation is maintained
4. ✅ Audit trail is complete
5. ✅ Failed charges properly downgrade tenants
6. ✅ Successful charges properly renew subscriptions

## Production Readiness

These tests validate that Kaspi recurring subscriptions are **SaaS-grade**:
- ✅ **Idempotent**: Safe to retry
- ✅ **Tenant-isolated**: No cross-tenant access
- ✅ **Auditable**: Complete order and event history
- ✅ **Resilient**: Handles failures gracefully
- ✅ **Consistent**: Database state always valid

## Integration with S3 Export

The test suite ensures that:
- All `billing_orders` records are exportable to S3
- All `billing_webhook_events` are exportable to S3
- Export maintains tenant isolation
- Export preserves audit trail
