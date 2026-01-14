/**
 * Storage utilities for baseUrl and tenantId.
 * Uses localStorage for persistence.
 */

const STORAGE_KEY_BASE_URL = 'cyberplat_base_url';
const STORAGE_KEY_TENANT_ID = 'cyberplat_tenant_id';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';

/**
 * Get base URL from storage or return default.
 */
export function getBaseUrl(): string {
  if (typeof window === 'undefined') {
    return DEFAULT_BASE_URL;
  }
  const stored = localStorage.getItem(STORAGE_KEY_BASE_URL);
  return stored || DEFAULT_BASE_URL;
}

/**
 * Set base URL in storage.
 */
export function setBaseUrl(url: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  localStorage.setItem(STORAGE_KEY_BASE_URL, url);
}

/**
 * Get tenant ID from storage.
 */
export function getTenantId(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  const stored = localStorage.getItem(STORAGE_KEY_TENANT_ID);
  return stored || '';
}

/**
 * Set tenant ID in storage.
 */
export function setTenantId(tenantId: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  localStorage.setItem(STORAGE_KEY_TENANT_ID, tenantId);
}

/**
 * Clear all stored settings.
 */
export function clearSettings(): void {
  if (typeof window === 'undefined') {
    return;
  }
  localStorage.removeItem(STORAGE_KEY_BASE_URL);
  localStorage.removeItem(STORAGE_KEY_TENANT_ID);
}
