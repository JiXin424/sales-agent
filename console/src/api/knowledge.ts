/** Knowledge upload and ingestion API wrappers. */

import { apiGet, apiPost, apiUpload } from './client';
import type { PaginatedResponse, UploadResponse, IngestionJobItem, IngestionJobFilters } from './types';

function base(tid: string) {
  return `/tenants/${tid}/knowledge`;
}

export function uploadKnowledgeFile(tenantId: string, file: File) {
  return apiUpload<UploadResponse>(`${base(tenantId)}/upload`, file);
}

export function listIngestionJobs(tenantId: string, filters?: IngestionJobFilters) {
  return apiGet<PaginatedResponse<IngestionJobItem>>(
    `${base(tenantId)}/jobs`,
    filters as Record<string, string | number | undefined>,
  );
}

export function getIngestionJob(tenantId: string, jobId: string) {
  return apiGet<IngestionJobItem>(`${base(tenantId)}/jobs/${jobId}`);
}

export function retryIngestionJob(tenantId: string, jobId: string) {
  return apiPost<IngestionJobItem>(`${base(tenantId)}/jobs/${jobId}/retry`);
}

export function reindexDocument(tenantId: string, documentId: string) {
  return apiPost<Record<string, unknown>>(`/tenants/${tenantId}/knowledge/documents/${documentId}/reindex`);
}
