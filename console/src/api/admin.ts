/** Admin API wrappers — all tenant-scoped. */

import { apiGet } from './client';
import type {
  PaginatedResponse,
  ConversationItem,
  ConversationDetail,
  ConversationFilters,
  AgentRunDetail,
  RunStepsResponse,
  DocumentItem,
  SourceFileItem,
  FeedbackSummary,
  ModelCallItem,
  ModelCallSummary,
  WorkflowMetrics,
} from './types';

const BASE = (tid: string) => `/tenants/${tid}/admin`;

// --- Conversations ---

export function listConversations(tenantId: string, filters?: ConversationFilters) {
  return apiGet<PaginatedResponse<ConversationItem>>(`${BASE(tenantId)}/conversations`, filters as Record<string, string | number | undefined>);
}

export function getConversationDetail(tenantId: string, conversationId: string) {
  return apiGet<ConversationDetail>(`${BASE(tenantId)}/conversations/${conversationId}`);
}

// --- Run Traces ---

export function getRunDetail(tenantId: string, runId: string) {
  return apiGet<AgentRunDetail>(`${BASE(tenantId)}/runs/${runId}`);
}

export function getRunSteps(tenantId: string, runId: string) {
  return apiGet<RunStepsResponse>(`${BASE(tenantId)}/runs/${runId}/steps`);
}

// --- Documents ---

export function listDocuments(tenantId: string, filters?: { status?: string; limit?: number; offset?: number }) {
  return apiGet<PaginatedResponse<DocumentItem>>(`${BASE(tenantId)}/documents`, filters as Record<string, string | number | undefined>);
}

export function getDocumentDetail(tenantId: string, documentId: string) {
  return apiGet<DocumentItem>(`${BASE(tenantId)}/documents/${documentId}`);
}

export function listSourceFiles(tenantId: string, filters?: { limit?: number; offset?: number }) {
  return apiGet<PaginatedResponse<SourceFileItem>>(`${BASE(tenantId)}/source-files`, filters as Record<string, string | number | undefined>);
}

// --- Feedback Summary ---

export function getFeedbackSummary(tenantId: string) {
  return apiGet<FeedbackSummary>(`${BASE(tenantId)}/feedback/summary`);
}

// --- Latency Stats ---

export function getTenantLatencyStats(tenantId: string) {
  return apiGet<Record<string, unknown>>(`${BASE(tenantId)}/latency-stats`);
}

// --- Model Calls ---

export function listModelCalls(tenantId: string, filters?: { request_type?: string; status?: string; limit?: number; offset?: number }) {
  return apiGet<PaginatedResponse<ModelCallItem>>(`${BASE(tenantId)}/model-calls`, filters as Record<string, string | number | undefined>);
}

export function getModelCallsSummary(tenantId: string) {
  return apiGet<ModelCallSummary>(`${BASE(tenantId)}/model-calls/summary`);
}

// --- Workflow Metrics ---

export function getWorkflowMetrics(tenantId: string, dateRange?: { start_date?: string; end_date?: string }) {
  return apiGet<WorkflowMetrics>(`${BASE(tenantId)}/workflow-metrics`, dateRange as Record<string, string | undefined>);
}
