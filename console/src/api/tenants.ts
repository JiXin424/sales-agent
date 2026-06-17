/** Tenant API wrappers. */

import { apiGet, apiPost } from './client';
import type { TenantResponse, CreateTenantRequest, PaginatedResponse } from './types';

export interface TenantListItem {
  tenant_id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export function listTenants(filters?: { status?: string; limit?: number; offset?: number }) {
  return apiGet<PaginatedResponse<TenantListItem>>('/tenants', filters as Record<string, string | number | undefined>);
}

export function getTenant(tenantId: string) {
  return apiGet<TenantResponse>(`/tenants/${tenantId}`);
}

export function createTenant(req: CreateTenantRequest) {
  return apiPost<TenantResponse>('/tenants', req);
}
