'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Nav from '@/components/Nav';
import ErrorBanner from '@/components/ErrorBanner';
import SuccessBanner from '@/components/SuccessBanner';
import { getBaseUrl, setBaseUrl, getTenantId, setTenantId } from '@/lib/storage';

export default function SettingsPage() {
  const router = useRouter();
  const [baseUrl, setBaseUrlState] = useState('');
  const [tenantId, setTenantIdState] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    setBaseUrlState(getBaseUrl());
    setTenantIdState(getTenantId());
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!baseUrl.trim()) {
      setError('Base URL is required');
      return;
    }

    if (!tenantId.trim()) {
      setError('Tenant ID is required');
      return;
    }

    try {
      // Validate URL
      new URL(baseUrl);
    } catch {
      setError('Invalid URL format');
      return;
    }

    setBaseUrl(baseUrl.trim());
    setTenantId(tenantId.trim());
    setSuccess('Settings saved successfully!');
    
    // Redirect to documents after a short delay
    setTimeout(() => {
      router.push('/app/documents');
    }, 1000);
  };

  return (
    <>
      <Nav />
      <div className="container">
        <h1 style={{ marginBottom: '24px' }}>Settings</h1>
        
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        {success && <SuccessBanner message={success} onDismiss={() => setSuccess(null)} />}

        <div className="card">
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label" htmlFor="baseUrl">
                Base URL
              </label>
              <input
                id="baseUrl"
                type="text"
                className="form-input"
                value={baseUrl}
                onChange={(e) => setBaseUrlState(e.target.value)}
                placeholder="http://127.0.0.1:8000"
                required
              />
              <small style={{ color: '#666', fontSize: '12px', marginTop: '4px', display: 'block' }}>
                Backend API base URL (e.g., http://127.0.0.1:8000)
              </small>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="tenantId">
                Tenant ID
              </label>
              <input
                id="tenantId"
                type="text"
                className="form-input"
                value={tenantId}
                onChange={(e) => setTenantIdState(e.target.value)}
                placeholder="tenant-1"
                required
              />
              <small style={{ color: '#666', fontSize: '12px', marginTop: '4px', display: 'block' }}>
                Your tenant identifier (e.g., tenant-1)
              </small>
            </div>

            <button type="submit" className="btn btn-primary">
              Save Settings
            </button>
          </form>
        </div>

        <div className="card">
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Quick Start</h2>
          <p style={{ marginBottom: '12px', color: '#666' }}>
            For local development, use:
          </p>
          <ul style={{ paddingLeft: '20px', color: '#666' }}>
            <li>Base URL: <code>http://127.0.0.1:8000</code></li>
            <li>Tenant ID: <code>tenant-1</code> (or any identifier)</li>
          </ul>
        </div>
      </div>
    </>
  );
}
