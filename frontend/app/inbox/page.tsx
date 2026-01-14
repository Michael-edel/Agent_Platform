'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { listInboxEmails, InboxEmailItem } from '@/lib/api';
import Link from 'next/link';

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    queued: 'bg-gray-100 text-gray-800',
    processing: 'bg-blue-100 text-blue-800',
    done: 'bg-green-100 text-green-800',
    failed: 'bg-yellow-100 text-yellow-800',
    dead: 'bg-red-100 text-red-800',
    none: 'bg-gray-100 text-gray-800',
  };

  return (
    <span
      className={`px-2 py-1 rounded text-xs font-medium ${
        colors[status] || colors.none
      }`}
    >
      {status}
    </span>
  );
}

function formatDate(dateString: string): string {
  try {
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateString;
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function InboxPage() {
  const router = useRouter();
  const [emails, setEmails] = useState<InboxEmailItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>(undefined);

  const loadEmails = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await listInboxEmails({ limit: 50, cursor });
      setEmails(response.items);
      setCursor(response.cursor);
    } catch (err: any) {
      setError(err.message || 'Failed to load inbox emails');
      console.error('Error loading inbox:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadEmails();
  }, []);

  if (loading && emails.length === 0) {
    return (
      <div className="container mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold mb-6">Inbox</h1>
        <div className="text-center py-8">Loading...</div>
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Inbox</h1>
        <button
          onClick={loadEmails}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          disabled={loading}
        >
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-100 text-red-800 rounded">
          {error}
        </div>
      )}

      {emails.length === 0 ? (
        <div className="text-center py-8 text-gray-500">
          No emails in inbox yet. Send an email with PDF attachment to{' '}
          <code className="bg-gray-100 px-2 py-1 rounded">
            invoices+your-tenant-id@yourapp.ai
          </code>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full bg-white border border-gray-200">
            <thead>
              <tr className="bg-gray-50">
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Received
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  From
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Subject
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Attachment
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Attempts
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Links
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {emails.map((email) => (
                <tr key={email.job_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                    {formatDate(email.received_at)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                    {email.from_email || '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-900">
                    {email.subject || '—'}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                    <div>
                      <div className="font-medium">{email.attachment_filename || '—'}</div>
                      {email.attachment_size > 0 && (
                        <div className="text-xs text-gray-500">
                          {formatFileSize(email.attachment_size)}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <StatusBadge status={email.job_status} />
                    {email.error && (
                      <div className="text-xs text-red-600 mt-1 max-w-xs truncate" title={email.error}>
                        {email.error}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900">
                    {email.attempts > 0 ? `${email.attempts}` : '—'}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm">
                    <div className="flex gap-2">
                      {email.document_artifact_id && (
                        <Link
                          href={`/app/documents/${email.document_artifact_id}`}
                          className="text-blue-600 hover:underline"
                        >
                          Document
                        </Link>
                      )}
                      {email.invoice_artifact_id && (
                        <Link
                          href={`/app/invoices/${email.invoice_artifact_id}`}
                          className="text-green-600 hover:underline"
                        >
                          Invoice
                        </Link>
                      )}
                      {!email.document_artifact_id && !email.invoice_artifact_id && (
                        <span className="text-gray-400">—</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
