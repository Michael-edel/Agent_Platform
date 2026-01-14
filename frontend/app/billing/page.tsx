'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import { getTenantId } from '@/lib/storage';
import { getBillingInvoice, getBillingPortal, kaspiCheckout, type BillingInvoice, type BillingPortalResponse } from '@/lib/api';

export default function BillingPage() {
  const router = useRouter();
  const [billing, setBilling] = useState<BillingInvoice | null>(null);
  const [portal, setPortal] = useState<BillingPortalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingUpgrade, setLoadingUpgrade] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState(() => {
    // Default to current month (YYYY-MM)
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  });

  useEffect(() => {
    const tenantId = getTenantId();
    if (!tenantId) {
      router.push('/app/settings');
      return;
    }
  }, [router]);

  const loadBilling = async () => {
    if (!period.match(/^\d{4}-\d{2}$/)) {
      setError('Invalid period format. Use YYYY-MM (e.g., 2026-01)');
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const invoice = await getBillingInvoice(period);
      setBilling(invoice);

      // Portal info (plan / trial / upgrade)
      try {
        const portalResp = await getBillingPortal(period);
        setPortal(portalResp);
      } catch (portalErr) {
        console.warn('Failed to load billing portal:', portalErr);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load billing invoice';
      setError(message);
      console.error('Error loading billing:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleUpgrade = async () => {
    try {
      setLoadingUpgrade(true);
      setError(null);

      // Decide target plan
      const currentPlan = portal?.plan?.id;
      const target: 'pro' | 'enterprise' = currentPlan === 'pro' ? 'enterprise' : 'pro';

      const res = await kaspiCheckout(target);
      if (!res.checkout_url) throw new Error('Missing checkout_url');

      // Redirect user to Kaspi Checkout
      window.location.href = res.checkout_url;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start upgrade';
      setError(message);
      console.error('Upgrade error:', err);
    } finally {
      setLoadingUpgrade(false);
    }
  };

  useEffect(() => {
    if (period.match(/^\d{4}-\d{2}$/)) {
      loadBilling();
    }
  }, [period]);

  const formatAmount = (amountMinor: number, currency: string = 'USD') => {
    const amount = amountMinor / 100;
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency,
    }).format(amount);
  };

  return (
    <>
      <Nav />
      <div className="container">
        <h1 style={{ marginBottom: '24px' }}>Billing</h1>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Period</h2>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <input
              type="text"
              className="form-input"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              placeholder="YYYY-MM"
              pattern="\d{4}-\d{2}"
              style={{ width: '150px' }}
            />
            <button onClick={loadBilling} className="btn btn-primary" disabled={loading}>
              {loading ? (
                <>
                  <span className="loading" style={{ display: 'inline-block', marginRight: '8px' }}></span>
                  Loading...
                </>
              ) : (
                'Load'
              )}
            </button>
          </div>
          <small style={{ color: '#666', fontSize: '12px', marginTop: '4px', display: 'block' }}>
            Format: YYYY-MM (e.g., 2026-01)
          </small>
        </div>

        {loading ? (
          <div className="card">
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading" style={{ margin: '0 auto' }}></div>
              <p style={{ marginTop: '16px', color: '#666' }}>Loading billing invoice...</p>
            </div>
          </div>
        ) : billing ? (
          <>
            <div className="card">
              <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Plan</h2>
              <table className="table">
                <tbody>
                  <tr>
                    <td style={{ fontWeight: 600, width: '200px' }}>Current plan</td>
                    <td>{portal?.plan?.id || '—'}</td>
                  </tr>
                  <tr>
                    <td style={{ fontWeight: 600 }}>Status</td>
                    <td>{portal?.subscription?.status || '—'}</td>
                  </tr>
                  <tr>
                    <td style={{ fontWeight: 600 }}>Trial</td>
                    <td>
                      {portal?.trial ? (
                        <>
                          days_left={portal.trial.days_left ?? '—'}; expires_at={portal.trial.expires_at ?? '—'}
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                </tbody>
              </table>

              {portal?.upgrade_available !== false && portal?.plan?.id !== 'enterprise' && (
                <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <button className="btn btn-primary" onClick={handleUpgrade} disabled={loadingUpgrade}>
                    {loadingUpgrade ? 'Opening Kaspi…' : portal?.plan?.id === 'pro' ? 'Upgrade to Enterprise' : 'Upgrade to Pro'}
                  </button>
                  <small style={{ color: '#666' }}>
                    You will be redirected to Kaspi Checkout.
                  </small>
                </div>
              )}
            </div>

            <div className="card">
              <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Summary</h2>
              <table className="table">
                <tbody>
                  <tr>
                    <td style={{ fontWeight: 600, width: '200px' }}>Tenant ID</td>
                    <td>{billing.tenant_id}</td>
                  </tr>
                  <tr>
                    <td style={{ fontWeight: 600 }}>Period</td>
                    <td>{billing.period}</td>
                  </tr>
                  <tr>
                    <td style={{ fontWeight: 600 }}>Currency</td>
                    <td>{billing.currency || 'USD'}</td>
                  </tr>
                  <tr>
                    <td style={{ fontWeight: 600 }}>Total Amount</td>
                    <td style={{ fontSize: '18px', fontWeight: 600 }}>
                      {formatAmount(billing.total_amount_minor, billing.currency)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {billing.totals_by_metric && Object.keys(billing.totals_by_metric).length > 0 && (
              <div className="card">
                <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Totals by Metric</h2>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Units</th>
                      <th>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(billing.totals_by_metric).map(([metric, data]) => (
                      <tr key={metric}>
                        <td style={{ fontWeight: 600 }}>{metric}</td>
                        <td>{data.units || 0}</td>
                        <td>{formatAmount(data.amount_minor || 0, billing.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <div className="card">
            <p style={{ textAlign: 'center', color: '#666', padding: '40px' }}>
              No billing data available for this period.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
