/**
 * API client for CyberPlat backend.
 * Automatically adds X-Tenant-ID header from storage.
 */

import { getBaseUrl, getTenantId } from './storage';

export interface ApiError {
  detail: string;
  message?: string;
}

export interface DocumentListItem {
  id: string;
  created_at: string;
  state?: {
    ui_status?: string;
  };
  kind?: string;
  source?: string;
  [key: string]: any;
}

export interface DocumentDetail extends DocumentListItem {
  file_id?: string;
  data?: Record<string, any>;
}

export interface InvoiceListItem {
  id: string;
  created_at: string;
  state?: {
    ui_status?: string;
  };
  invoice_number?: string;
  total_amount?: number;
  supplier_name?: string;
  date?: string;
  [key: string]: any;
}

export interface InvoiceDetail extends InvoiceListItem {
  fields?: Record<string, any>;
  raw?: any;
}

export interface BillingInvoice {
  tenant_id: string;
  period: string;
  currency: string;
  totals_by_metric: Record<string, {
    units: number;
    amount_minor: number;
  }>;
  total_amount_minor: number;
  [key: string]: any;
}

export interface BillingPortalResponse {
  tenant_id: string;
  period: string;
  plan: { id: string; name: string };
  subscription: { status: string; subscription_id?: string | null };
  links?: { upgrade_url?: string | null; manage_url?: string | null };
  trial?: { days_left?: number | null; expires_at?: string | null } | null;
  upgrade_available?: boolean;
  [key: string]: any;
}

/**
 * Make API request with automatic X-Tenant-ID header.
 */
async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const baseUrl = getBaseUrl();
  const tenantId = getTenantId();

  if (!tenantId) {
    throw new Error('Tenant ID is not set. Please configure it in settings.');
  }

  const url = `${baseUrl}${path}`;
  const headers = new Headers(options.headers);
  headers.set('X-Tenant-ID', tenantId);

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData: ApiError = await response.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // Ignore JSON parse errors
    }
    throw new Error(errorMessage);
  }

  // Handle empty responses
  const contentType = response.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return response.json();
  }

  return response as unknown as T;
}

/**
 * Upload document (multipart form-data).
 */
export async function uploadDocument(file: File): Promise<{ artifact_id: string; file_id: string }> {
  const baseUrl = getBaseUrl();
  const tenantId = getTenantId();

  if (!tenantId) {
    throw new Error('Tenant ID is not set. Please configure it in settings.');
  }

  const formData = new FormData();
  formData.append('file', file);

  const url = `${baseUrl}/documents/upload`;
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'X-Tenant-ID': tenantId,
    },
    body: formData,
  });

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData: ApiError = await response.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // Ignore JSON parse errors
    }
    throw new Error(errorMessage);
  }

  return response.json();
}

/**
 * List documents.
 */
export async function listDocuments(params?: {
  ui_status?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: DocumentListItem[]; total: number; limit: number; offset: number }> {
  const queryParams = new URLSearchParams();
  if (params?.ui_status) queryParams.set('ui_status', params.ui_status);
  if (params?.limit) queryParams.set('limit', params.limit.toString());
  if (params?.offset) queryParams.set('offset', params.offset.toString());

  const path = `/api/v1/documents${queryParams.toString() ? `?${queryParams}` : ''}`;
  return request(path);
}

/**
 * Get document detail.
 */
export async function getDocument(id: string): Promise<DocumentDetail> {
  return request(`/api/v1/documents/${id}`);
}

/**
 * Run OCR on document.
 */
export async function runOcr(documentId: string): Promise<{
  success: boolean;
  artifact_id?: string;
  tenant_id?: string;
  error?: string;
}> {
  return request(`/api/v1/documents/${documentId}/run-ocr`, {
    method: 'POST',
  });
}

/**
 * List invoices.
 */
export async function listInvoices(params?: {
  ui_status?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: InvoiceListItem[]; total: number; limit: number; offset: number }> {
  const queryParams = new URLSearchParams();
  if (params?.ui_status) queryParams.set('ui_status', params.ui_status);
  if (params?.limit) queryParams.set('limit', params.limit.toString());
  if (params?.offset) queryParams.set('offset', params.offset.toString());

  const path = `/api/v1/invoices${queryParams.toString() ? `?${queryParams}` : ''}`;
  return request(path);
}

/**
 * Get invoice detail.
 */
export async function getInvoice(id: string): Promise<InvoiceDetail> {
  return request(`/api/v1/invoices/${id}`);
}

/**
 * Confirm invoice.
 */
export async function confirmInvoice(id: string): Promise<{
  success: boolean;
  artifact_id: string;
  message?: string;
}> {
  return request(`/api/v1/invoices/${id}/confirm`, {
    method: 'POST',
  });
}

/**
 * Export invoice and download file.
 */
export async function exportInvoice(
  id: string,
  exportType: 'excel' | 'json'
): Promise<void> {
  const baseUrl = getBaseUrl();
  const tenantId = getTenantId();

  if (!tenantId) {
    throw new Error('Tenant ID is not set. Please configure it in settings.');
  }

  const url = `${baseUrl}/api/v1/invoices/${id}/export`;
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Tenant-ID': tenantId,
    },
    body: JSON.stringify({ export_type: exportType }),
  });

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData: ApiError = await response.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // Ignore JSON parse errors
    }
    throw new Error(errorMessage);
  }

  // Get file_id from export response or download directly
  const exportData = await response.json();
  const exportId = exportData.export_id;

  // For MVP, we'll try to download from /files/{file_id} if available
  // In production, this would be a proper file download endpoint
  if (exportData.file_id) {
    const fileUrl = `${baseUrl}/files/${exportData.file_id}`;
    const fileResponse = await fetch(fileUrl, {
      headers: {
        'X-Tenant-ID': tenantId,
      },
    });

    if (fileResponse.ok) {
      const blob = await fileResponse.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = `invoice_${id}_${exportType}.${exportType === 'excel' ? 'xlsx' : 'json'}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(downloadUrl);
    }
  }
}

/**
 * Get billing invoice for period.
 */
export async function getBillingInvoice(period: string): Promise<BillingInvoice> {
  return request(`/api/v1/billing/invoice?period=${period}`);
}

export async function getBillingPortal(period?: string): Promise<BillingPortalResponse> {
  const qs = period ? `?period=${encodeURIComponent(period)}` : '';
  return request(`/api/v1/billing/portal${qs}`);
}

export async function kaspiCheckout(plan_id: 'pro' | 'enterprise'): Promise<{
  checkout_url: string;
  order_id: string;
  kaspi_order_id?: string | null;
}> {
  return request(`/api/v1/billing/checkout/kaspi`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id }),
  });
}

export interface InboxEmailItem {
  received_at: string;
  from_email: string;
  to_email: string;
  subject: string;
  attachment_filename: string;
  attachment_size: number;
  document_artifact_id: string;
  job_id: string;
  job_status: 'queued' | 'processing' | 'done' | 'failed' | 'dead' | 'none';
  attempts: number;
  next_run_at?: string;
  invoice_artifact_id?: string;
  error?: string;
}

export interface InboxEmailListResponse {
  items: InboxEmailItem[];
  cursor?: string;
}

/**
 * List inbox emails (email ingestion status).
 */
export async function listInboxEmails(params?: {
  limit?: number;
  cursor?: string;
}): Promise<InboxEmailListResponse> {
  const queryParams = new URLSearchParams();
  if (params?.limit) queryParams.set('limit', params.limit.toString());
  if (params?.cursor) queryParams.set('cursor', params.cursor);

  const path = `/api/v1/inbox/emails${queryParams.toString() ? `?${queryParams}` : ''}`;
  return request(path);
}
