'use client';

import { useState, useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import SuccessBanner from '@/components/SuccessBanner';
import { getTenantId } from '@/lib/storage';
import { getInvoice, confirmInvoice, exportInvoice, type InvoiceDetail } from '@/lib/api';

export default function InvoiceDetailPage() {
  const router = useRouter();
  const params = useParams();
  const invoiceId = params.id as string;

  const [invoice, setInvoice] = useState<InvoiceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const tenantId = getTenantId();
    if (!tenantId) {
      router.push('/app/settings');
      return;
    }

    loadInvoice();
  }, [invoiceId, router]);

  const loadInvoice = async () => {
    try {
      setLoading(true);
      setError(null);
      const inv = await getInvoice(invoiceId);
      setInvoice(inv);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load invoice';
      setError(message);
      console.error('Error loading invoice:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async () => {
    try {
      setConfirming(true);
      setError(null);
      setSuccess(null);

      await confirmInvoice(invoiceId);
      setSuccess('Invoice confirmed successfully!');
      
      // Update local state
      if (invoice) {
        setInvoice({
          ...invoice,
          state: {
            ...invoice.state,
            ui_status: 'confirmed',
          },
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to confirm invoice';
      setError(message);
      console.error('Error confirming invoice:', err);
    } finally {
      setConfirming(false);
    }
  };

  const handleExport = async (exportType: 'excel' | 'json') => {
    try {
      setExporting(true);
      setError(null);
      setSuccess(null);

      await exportInvoice(invoiceId, exportType);
      setSuccess(`Invoice exported as ${exportType.toUpperCase()} and downloaded!`);
      
      // Update local state
      if (invoice) {
        setInvoice({
          ...invoice,
          state: {
            ...invoice.state,
            ui_status: 'exported',
          },
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to export invoice';
      setError(message);
      console.error('Error exporting invoice:', err);
    } finally {
      setExporting(false);
    }
  };

  const renderFields = () => {
    if (!invoice) return null;

    // Try to get fields from various possible locations
    const fields = invoice.fields || invoice.data || {};
    
    if (Object.keys(fields).length === 0) {
      return <p style={{ color: '#666' }}>No fields available</p>;
    }

    return (
      <table className="table">
        <tbody>
          {Object.entries(fields).map(([key, value]) => (
            <tr key={key}>
              <td style={{ fontWeight: 600, width: '200px' }}>{key}</td>
              <td>
                {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  if (loading) {
    return (
      <>
        <Nav />
        <div className="container">
          <div className="card">
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading" style={{ margin: '0 auto' }}></div>
              <p style={{ marginTop: '16px', color: '#666' }}>Loading invoice...</p>
            </div>
          </div>
        </div>
      </>
    );
  }

  if (!invoice) {
    return (
      <>
        <Nav />
        <div className="container">
          <ErrorBanner message="Invoice not found" />
        </div>
      </>
    );
  }

  const status = invoice.state?.ui_status || 'pending';
  const isConfirmed = status === 'confirmed';
  const isExported = status === 'exported';

  return (
    <>
      <Nav />
      <div className="container">
        <div style={{ marginBottom: '24px' }}>
          <button onClick={() => router.back()} className="btn btn-secondary" style={{ marginBottom: '16px' }}>
            ← Back
          </button>
          <h1>Invoice Details</h1>
        </div>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        {success && <SuccessBanner message={success} onDismiss={() => setSuccess(null)} />}

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Information</h2>
          <table className="table">
            <tbody>
              <tr>
                <td style={{ fontWeight: 600, width: '200px' }}>ID</td>
                <td><code>{invoice.id}</code></td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Status</td>
                <td>
                  <span className={`badge ${
                    status === 'pending' ? 'badge-info' :
                    status === 'confirmed' ? 'badge-success' :
                    status === 'exported' ? 'badge-success' :
                    status === 'error' ? 'badge-danger' : 'badge-info'
                  }`}>
                    {status}
                  </span>
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Invoice #</td>
                <td>{invoice.invoice_number || '—'}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Supplier</td>
                <td>{invoice.supplier_name || '—'}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Total Amount</td>
                <td>
                  {invoice.total_amount
                    ? `$${invoice.total_amount.toFixed(2)}`
                    : '—'}
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Date</td>
                <td>{invoice.date || '—'}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Created</td>
                <td>{invoice.created_at ? new Date(invoice.created_at).toLocaleString() : '—'}</td>
              </tr>
            </tbody>
          </table>
        </div>

        {invoice.fields && Object.keys(invoice.fields).length > 0 && (
          <div className="card">
            <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Fields</h2>
            {renderFields()}
          </div>
        )}

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Actions</h2>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {!isConfirmed && (
              <button
                onClick={handleConfirm}
                disabled={confirming}
                className="btn btn-success"
              >
                {confirming ? (
                  <>
                    <span className="loading" style={{ display: 'inline-block', marginRight: '8px' }}></span>
                    Confirming...
                  </>
                ) : (
                  'Confirm Invoice'
                )}
              </button>
            )}
            {isConfirmed && !isExported && (
              <>
                <button
                  onClick={() => handleExport('excel')}
                  disabled={exporting}
                  className="btn btn-primary"
                >
                  {exporting ? (
                    <>
                      <span className="loading" style={{ display: 'inline-block', marginRight: '8px' }}></span>
                      Exporting...
                    </>
                  ) : (
                    'Export Excel'
                  )}
                </button>
                <button
                  onClick={() => handleExport('json')}
                  disabled={exporting}
                  className="btn btn-primary"
                >
                  {exporting ? (
                    <>
                      <span className="loading" style={{ display: 'inline-block', marginRight: '8px' }}></span>
                      Exporting...
                    </>
                  ) : (
                    'Export JSON'
                  )}
                </button>
              </>
            )}
            {isExported && (
              <p style={{ color: '#666', fontSize: '14px' }}>
                Invoice has been exported. You can export again if needed.
              </p>
            )}
          </div>
          {isConfirmed && (
            <p style={{ marginTop: '12px', color: '#28a745', fontSize: '14px' }}>
              ✓ Invoice confirmed
            </p>
          )}
        </div>
      </div>
    </>
  );
}
