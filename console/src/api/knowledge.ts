/** Knowledge upload and ingestion API wrappers. */

import { apiGet, apiPost, apiUpload, apiUploadFiles } from './client';
import type { PaginatedResponse, UploadResponse, IngestionJobItem, IngestionJobFilters, OntologyStatus, OntologyJob, IngestStartResponse, JobProgressEvent } from './types';

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

// --- Ontology (Neo4j knowledge engine) ---

export function getOntologyStatus(agentId: string) {
  return apiGet<OntologyStatus>(`/agents/${agentId}/ontology/status`);
}

export function startOntologyIngest(agentId: string, files: File[]) {
  return apiUploadFiles<IngestStartResponse[]>(`/agents/${agentId}/ontology/ingest`, files);
}

export function listOntologyJobs(agentId: string, limit = 20, offset = 0) {
  return apiGet<PaginatedResponse<OntologyJob>>(`/agents/${agentId}/ontology/jobs`, { limit, offset });
}

export function subscribeJobEvents(agentId: string, jobId: string): EventSource {
  return new EventSource(`/api/agents/${agentId}/ontology/jobs/${jobId}/events`);
}
