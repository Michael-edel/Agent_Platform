'use client';

import { useState, useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import SuccessBanner from '@/components/SuccessBanner';
import { getTenantId } from '@/lib/storage';
import { getDocument, runOcr, type DocumentDetail } from '@/lib/api';

export default function DocumentDetailPage() {
  const router = useRouter();
  const params = useParams();
  const documentId = params.id as string;

  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [runningOcr, setRunningOcr] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const tenantId = getTenantId();
    if (!tenantId) {
      router.push('/app/settings');
      return;
    }

    loadDocument();
  }, [documentId, router]);

  const loadDocument = async () => {
    try {
      setLoading(true);
      setError(null);
      const doc = await getDocument(documentId);
      setDocument(doc);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load document';
      setError(message);
      console.error('Error loading document:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRunOcr = async () => {
    if (!document) return;

    try {
      setRunningOcr(true);
      setError(null);
      setSuccess(null);

      const result = await runOcr(documentId);
      
      if (result.success && result.artifact_id) {
        setSuccess('OCR completed successfully! Redirecting to invoice...');
        // Redirect to invoice detail
        setTimeout(() => {
          router.push(`/app/invoices/${result.artifact_id}`);
        }, 1500);
      } else {
        setError(result.error || 'OCR failed');
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to run OCR';
      setError(message);
      console.error('Error running OCR:', err);
    } finally {
      setRunningOcr(false);
    }
  };

  const getFileUrl = () => {
    if (!document?.file_id) return null;
    const baseUrl = typeof window !== 'undefined' 
      ? localStorage.getItem('cyberplat_base_url') || 'http://127.0.0.1:8000'
      : 'http://127.0.0.1:8000';
    return `${baseUrl}/files/${document.file_id}`;
  };

  if (loading) {
    return (
      <>
        <Nav />
        <div className="container">
          <div className="card">
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading" style={{ margin: '0 auto' }}></div>
              <p style={{ marginTop: '16px', color: '#666' }}>Loading document...</p>
            </div>
          </div>
        </div>
      </>
    );
  }

  if (!document) {
    return (
      <>
        <Nav />
        <div className="container">
          <ErrorBanner message="Document not found" />
        </div>
      </>
    );
  }

  const fileUrl = getFileUrl();
  const status = document.state?.ui_status || 'pending';

  return (
    <>
      <Nav />
      <div className="container">
        <div style={{ marginBottom: '24px' }}>
          <button onClick={() => router.back()} className="btn btn-secondary" style={{ marginBottom: '16px' }}>
            ← Back
          </button>
          <h1>Document Details</h1>
        </div>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        {success && <SuccessBanner message={success} onDismiss={() => setSuccess(null)} />}

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Information</h2>
          <table className="table">
            <tbody>
              <tr>
                <td style={{ fontWeight: 600, width: '200px' }}>ID</td>
                <td><code>{document.id}</code></td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Status</td>
                <td>
                  <span className={`badge ${
                    status === 'uploaded' ? 'badge-info' :
                    status === 'processing' ? 'badge-warning' :
                    status === 'extracted' || status === 'completed' ? 'badge-success' :
                    status === 'error' ? 'badge-danger' : 'badge-info'
                  }`}>
                    {status}
                  </span>
                </td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Created</td>
                <td>{document.created_at ? new Date(document.created_at).toLocaleString() : '—'}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Source</td>
                <td>{document.source || '—'}</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>File ID</td>
                <td>{document.file_id || '—'}</td>
              </tr>
            </tbody>
          </table>
        </div>

        {fileUrl && (
          <div className="card">
            <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>PDF Viewer</h2>
            <iframe
              src={fileUrl}
              style={{
                width: '100%',
                height: '600px',
                border: '1px solid #ddd',
                borderRadius: '4px',
              }}
              title="PDF Viewer"
            />
          </div>
        )}

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Actions</h2>
          <button
            onClick={handleRunOcr}
            disabled={runningOcr || status === 'extracted' || status === 'completed'}
            className="btn btn-primary"
          >
            {runningOcr ? (
              <>
                <span className="loading" style={{ display: 'inline-block', marginRight: '8px' }}></span>
                Running OCR...
              </>
            ) : (
              'Run OCR'
            )}
          </button>
          {status === 'extracted' || status === 'completed' ? (
            <p style={{ marginTop: '12px', color: '#666', fontSize: '14px' }}>
              OCR already completed. Check the Invoices page for the result.
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}
