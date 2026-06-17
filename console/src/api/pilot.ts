/** Pilot Validation API wrappers — all tenant-scoped. */

import { apiGet, apiPost, apiPatch } from './client';
import type {
  PaginatedResponse,
  PilotMetrics,
  ReviewItem,
  ReviewItemFilters,
  FeedbackCategorySummary,
  FeedbackItem,
  KnowledgeGap,
  KnowledgeGapSummary,
  EvalSuite,
  EvalRun,
  EvalRunResult,
  ComparisonResult,
  ReviewOutcomeComparison,
  AlertRule,
  AlertItem,
  PilotReport,
  PilotStatus,
} from './types';

const BASE = (tid: string) => `/tenants/${tid}/admin/pilot`;

// --- R2: Pilot Metrics ---

export function getPilotMetrics(tenantId: string, startDate: string, endDate: string) {
  return apiGet<PilotMetrics>(`${BASE(tenantId)}/pilot-metrics`, { start_date: startDate, end_date: endDate });
}

// --- R3: Review Queue ---

export function scanReviewQueue(tenantId: string) {
  return apiPost<{ created: number }>(`${BASE(tenantId)}/review-queue/scan`);
}

export function listReviewQueue(tenantId: string, filters?: ReviewItemFilters) {
  return apiGet<PaginatedResponse<ReviewItem>>(`${BASE(tenantId)}/review-queue`, filters as Record<string, string | number | undefined>);
}

export function createReviewItem(tenantId: string, body: { conversation_id: string; reason?: string; priority?: string; notes?: Record<string, unknown>; assignee?: string }) {
  return apiPost<ReviewItem>(`${BASE(tenantId)}/review-queue`, body);
}

export function getReviewItem(tenantId: string, itemId: string) {
  return apiGet<ReviewItem>(`${BASE(tenantId)}/review-queue/${itemId}`);
}

export function updateReviewItem(tenantId: string, itemId: string, body: { status: string; assignee?: string; notes?: Record<string, unknown> }) {
  return apiPatch<ReviewItem>(`${BASE(tenantId)}/review-queue/${itemId}`, body);
}

// --- R4: Feedback Classification ---

export function classifyFeedback(tenantId: string, feedbackId: string, categories: string[]) {
  return apiPatch<{ status: string; feedback_id: string; categories: string[] }>(`${BASE(tenantId)}/feedback/${feedbackId}/classify`, { categories });
}

export function getFeedbackCategorySummary(tenantId: string) {
  return apiGet<FeedbackCategorySummary>(`${BASE(tenantId)}/feedback-categories/summary`);
}

export function listFeedbackByCategory(tenantId: string, category: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<FeedbackItem>>(`${BASE(tenantId)}/feedback-categories/${category}`, { limit, offset });
}

// --- R5: Knowledge Gaps ---

export function listKnowledgeGaps(tenantId: string, status?: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<KnowledgeGap>>(`${BASE(tenantId)}/knowledge-gaps`, { status, limit, offset });
}

export function createKnowledgeGap(tenantId: string, body: { title: string; description?: string; source_conversation_id?: string; source_feedback_id?: string; priority?: string; keywords?: string[] }) {
  return apiPost<KnowledgeGap>(`${BASE(tenantId)}/knowledge-gaps`, body);
}

export function getKnowledgeGap(tenantId: string, gapId: string) {
  return apiGet<KnowledgeGap>(`${BASE(tenantId)}/knowledge-gaps/${gapId}`);
}

export function transitionKnowledgeGap(tenantId: string, gapId: string, status: string) {
  return apiPatch<KnowledgeGap>(`${BASE(tenantId)}/knowledge-gaps/${gapId}`, { status });
}

export function linkDocumentToGap(tenantId: string, gapId: string, documentId: string) {
  return apiPost<KnowledgeGap>(`${BASE(tenantId)}/knowledge-gaps/${gapId}/link-document`, { document_id: documentId });
}

export function getKnowledgeGapsSummary(tenantId: string) {
  return apiGet<KnowledgeGapSummary>(`${BASE(tenantId)}/knowledge-gaps-summary`);
}

// --- R6: Eval Suites & Runs ---

export function listEvalSuites(tenantId: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<EvalSuite>>(`${BASE(tenantId)}/eval-suites`, { limit, offset });
}

export function createEvalSuite(tenantId: string, body: { name: string; fixture_path: string; description?: string }) {
  return apiPost<EvalSuite>(`${BASE(tenantId)}/eval-suites`, body);
}

export function runEvalSuite(tenantId: string, suiteId: string, promptVersionId?: string) {
  return apiPost<EvalRun>(`${BASE(tenantId)}/eval-suites/${suiteId}/run`, { prompt_version_id: promptVersionId });
}

export function listEvalRuns(tenantId: string, evalSuiteId?: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<EvalRun>>(`${BASE(tenantId)}/eval-runs`, { eval_suite_id: evalSuiteId, limit, offset });
}

export function getEvalRun(tenantId: string, runId: string) {
  return apiGet<EvalRun>(`${BASE(tenantId)}/eval-runs/${runId}`);
}

export function getEvalRunResults(tenantId: string, runId: string) {
  return apiGet<{ items: EvalRunResult[]; total: number }>(`${BASE(tenantId)}/eval-runs/${runId}/results`);
}

// --- R7: Comparison ---

export function compareEvalRuns(tenantId: string, beforeRunId: string, afterRunId: string) {
  return apiPost<ComparisonResult>(`${BASE(tenantId)}/compare`, { before_run_id: beforeRunId, after_run_id: afterRunId });
}

export function compareReviewOutcomes(tenantId: string, beforeDate: string, afterDate: string) {
  return apiPost<ReviewOutcomeComparison>(`${BASE(tenantId)}/compare-review-outcomes`, { before_date: beforeDate, after_date: afterDate });
}

export function compareDocumentChange(tenantId: string, beforeDate: string, afterDate: string) {
  return apiPost<{ before_date: string; after_date: string; error_count: { before: number; after: number; change: number }; negative_feedback: { before: number; after: number; change: number } }>(`${BASE(tenantId)}/compare-document-change`, { before_date: beforeDate, after_date: afterDate });
}

// --- R8: Alerts ---

export function listAlertRules(tenantId: string, enabledOnly?: boolean, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<AlertRule>>(`${BASE(tenantId)}/alert-rules`, { enabled_only: enabledOnly !== undefined ? String(enabledOnly) : undefined, limit, offset });
}

export function createAlertRule(tenantId: string, body: { name: string; metric: string; threshold: number; condition?: string; window_minutes?: number; severity?: string }) {
  return apiPost<AlertRule>(`${BASE(tenantId)}/alert-rules`, body);
}

export function updateAlertRule(tenantId: string, ruleId: string, body: Record<string, unknown>) {
  return apiPatch<AlertRule>(`${BASE(tenantId)}/alert-rules/${ruleId}`, body);
}

export function seedDefaultAlertRules(tenantId: string) {
  return apiPost<{ created: number }>(`${BASE(tenantId)}/alert-rules/seed-defaults`);
}

export function listAlerts(tenantId: string, status?: string, severity?: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<AlertItem>>(`${BASE(tenantId)}/alerts`, { status, severity, limit, offset });
}

export function evaluateAlerts(tenantId: string) {
  return apiPost<{ triggered: number; alerts: AlertItem[] }>(`${BASE(tenantId)}/alerts/evaluate`);
}

export function acknowledgeAlert(tenantId: string, alertId: string) {
  return apiPost<AlertItem>(`${BASE(tenantId)}/alerts/${alertId}/acknowledge`);
}

export function resolveAlert(tenantId: string, alertId: string) {
  return apiPost<AlertItem>(`${BASE(tenantId)}/alerts/${alertId}/resolve`);
}

// --- R9: Pilot Reports ---

export function generateReport(tenantId: string, body: { start_date: string; end_date: string; report_type?: string }) {
  return apiPost<PilotReport>(`${BASE(tenantId)}/reports/generate`, body);
}

export function listReports(tenantId: string, reportType?: string, limit?: number, offset?: number) {
  return apiGet<PaginatedResponse<PilotReport>>(`${BASE(tenantId)}/reports`, { report_type: reportType, limit, offset });
}

export function getReport(tenantId: string, reportId: string) {
  return apiGet<PilotReport>(`${BASE(tenantId)}/reports/${reportId}`);
}

// --- R10: Pilot Status ---

export function getPilotStatus(tenantId: string) {
  return apiGet<PilotStatus>(`${BASE(tenantId)}/status`);
}
