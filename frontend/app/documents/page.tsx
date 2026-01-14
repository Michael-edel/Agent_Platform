'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import SuccessBanner from '@/components/SuccessBanner';
import { getTenantId } from '@/lib/storage';
import { listDocuments, uploadDocument, type DocumentListItem } from '@/lib/api';

export default function DocumentsPage() {
  const router = useRouter();
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    // Check tenant ID
    const tenantId = getTenantId();
    if (!tenantId) {
      router.push('/app/settings');
      return;
    }

    loadDocuments();
  }, [router]);

  const loadDocuments = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await listDocuments({ limit: 100 });
      setDocuments(response.items || []);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load documents';
      setError(message);
      console.error('Error loading documents:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      setUploading(true);
      setError(null);
      setSuccess(null);

      await uploadDocument(file);
      setSuccess(`Document "${file.name}" uploaded successfully!`);
      
      // Refresh documents list
      await loadDocuments();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to upload document';
      setError(message);
      console.error('Error uploading document:', err);
    } finally {
      setUploading(false);
      // Reset input
      e.target.value = '';
    }
  };

  const getStatusBadge = (status?: string) => {
    if (!status) return <span className="badge badge-info">pending</span>;
    
    const statusMap: Record<string, { class: string; label: string }> = {
      uploaded: { class: 'badge-info', label: 'Uploaded' },
      processing: { class: 'badge-warning', label: 'Processing' },
      extracted: { class: 'badge-success', label: 'Extracted' },
      completed: { class: 'badge-success', label: 'Completed' },
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
          <h1>Documents</h1>
          <div>
            <label htmlFor="file-upload" className="btn btn-primary" style={{ cursor: 'pointer' }}>
              {uploading ? 'Uploading...' : 'Upload Document'}
            </label>
            <input
              id="file-upload"
              type="file"
              accept=".pdf,.jpg,.jpeg,.png"
              onChange={handleFileUpload}
              disabled={uploading}
              style={{ display: 'none' }}
            />
            <button onClick={loadDocuments} className="btn btn-secondary" style={{ marginLeft: '8px' }}>
              Refresh
            </button>
          </div>
        </div>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        {success && <SuccessBanner message={success} onDismiss={() => setSuccess(null)} />}

        {loading ? (
          <div className="card">
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading" style={{ margin: '0 auto' }}></div>
              <p style={{ marginTop: '16px', color: '#666' }}>Loading documents...</p>
            </div>
          </div>
        ) : documents.length === 0 ? (
          <div className="card">
            <p style={{ textAlign: 'center', color: '#666', padding: '40px' }}>
              No documents found. Upload your first document to get started.
            </p>
          </div>
        ) : (
          <div className="card" style={{ padding: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Created</th>
                  <th>Status</th>
                  <th>Source</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id}>
                    <td>
                      <code style={{ fontSize: '12px' }}>{doc.id.slice(0, 8)}...</code>
                    </td>
                    <td>
                      {doc.created_at
                        ? new Date(doc.created_at).toLocaleString()
                        : '—'}
                    </td>
                    <td>{getStatusBadge(doc.state?.ui_status)}</td>
                    <td>{doc.source || '—'}</td>
                    <td>
                      <Link href={`/app/documents/${doc.id}`} className="btn btn-primary" style={{ fontSize: '12px', padding: '4px 8px' }}>
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
