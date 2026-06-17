/** Prompt management API wrappers. */

import { apiGet, apiPost, apiPut } from './client';
import type {
  PaginatedResponse,
  PromptVersion,
  PromptFilters,
  PromptPreviewRequest,
  PromptPreviewResponse,
} from './types';

function base(tid: string) {
  return `/tenants/${tid}/prompts`;
}

export function createPromptVersion(
  tenantId: string,
  data: { task_type: string; template_text: string; description?: string; version?: string },
) {
  return apiPost<PromptVersion>(base(tenantId), data);
}

export function listPromptVersions(tenantId: string, filters?: PromptFilters) {
  return apiGet<PaginatedResponse<PromptVersion>>(
    base(tenantId),
    filters as Record<string, string | number | undefined>,
  );
}

export function getPromptVersion(tenantId: string, versionId: string) {
  return apiGet<PromptVersion>(`${base(tenantId)}/${versionId}`);
}

export function updatePromptVersion(
  tenantId: string,
  versionId: string,
  data: { template_text?: string; description?: string },
) {
  return apiPut<PromptVersion>(`${base(tenantId)}/${versionId}`, data);
}

export function activatePromptVersion(tenantId: string, versionId: string) {
  return apiPost<PromptVersion>(`${base(tenantId)}/${versionId}/activate`);
}

export function archivePromptVersion(tenantId: string, versionId: string) {
  return apiPost<PromptVersion>(`${base(tenantId)}/${versionId}/archive`);
}

export function previewPrompt(tenantId: string, req: PromptPreviewRequest) {
  return apiPost<PromptPreviewResponse>(`${base(tenantId)}/preview`, req);
}
