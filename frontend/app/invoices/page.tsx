'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import { getTenantId } from '@/lib/storage';
import { listInvoices, type InvoiceListItem } from '@/lib/api';

export default function InvoicesPage() {
  const router = useRouter();
  const [invoices, setInvoices] = useState<InvoiceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tenantId = getTenantId();
    if (!tenantId) {
      router.push('/app/settings');
      return;
    }

    loadInvoices();
  }, [router]);

  const loadInvoices = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await listInvoices({ limit: 100 });
      setInvoices(response.items || []);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load invoices';
      setError(message);
      console.error('Error loading invoices:', err);
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = (status?: string) => {
    if (!status) return <span className="badge badge-info">pending</span>;
    
    const statusMap: Record<string, { class: string; label: string }> = {
      pending: { class: 'badge-info', label: 'Pending' },
      confirmed: { class: 'badge-success', label: 'Confirmed' },
      exported: { class: 'badge-success', label: 'Exported' },
      error: { class: 'badge-danger', label: 'Error' },
    };

    const statusInfo = statusMap[status] || { class: 'badge-info', label: status };
    return <span className={`badge ${statusInfo.class}`}>{statusInfo.label}</span>;
  };

  return (
    <>
      <Nav />
      <div className="container">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
          <h1>Invoices</h1>
          <button onClick={loadInvoices} className="btn btn-secondary">
            Refresh
          </button>
        </div>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        {loading ? (
          <div className="card">
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading" style={{ margin: '0 auto' }}></div>
              <p style={{ marginTop: '16px', color: '#666' }}>Loading invoices...</p>
            </div>
          </div>
        ) : invoices.length === 0 ? (
          <div className="card">
            <p style={{ textAlign: 'center', color: '#666', padding: '40px' }}>
              No invoices found. Run OCR on a document to create an invoice.
            </p>
          </div>
        ) : (
          <div className="card" style={{ padding: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Invoice #</th>
                  <th>Supplier</th>
                  <th>Amount</th>
                  <th>Date</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((invoice) => (
                  <tr key={invoice.id}>
                    <td>
                      <code style={{ fontSize: '12px' }}>{invoice.id.slice(0, 8)}...</code>
                    </td>
                    <td>{invoice.invoice_number || '—'}</td>
                    <td>{invoice.supplier_name || '—'}</td>
                    <td>
                      {invoice.total_amount
                        ? `$${invoice.total_amount.toFixed(2)}`
                        : '—'}
                    </td>
                    <td>{invoice.date || '—'}</td>
                    <td>{getStatusBadge(invoice.state?.ui_status)}</td>
                    <td>
                      {invoice.created_at
                        ? new Date(invoice.created_at).toLocaleString()
                        : '—'}
                    </td>
                    <td>
                      <Link href={`/app/invoices/${invoice.id}`} className="btn btn-primary" style={{ fontSize: '12px', padding: '4px 8px' }}>
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
