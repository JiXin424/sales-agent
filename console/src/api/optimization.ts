/** Optimization (knowledge iteration) API client. */

import { apiGet, apiPost } from './client';

// ── Types ──────────────────────────────────────────────────────────────

export interface IterationResponse {
  id: string;
  tenant_id: string;
  agent_id: string;
  iteration_no: number;
  status: string;
  baseline_release_id?: string | null;
  created_at?: string | null;
}

export interface StartIterationRequest {
  fixed_suite_id: string;
  exploration_suite_id?: string | null;
  max_candidates?: number;
  max_consecutive_failures?: number;
  allowed_change_types?: string[];
}

export interface DiagnosisResponse {
  id: string;
  primary_cause: string;
  confidence: number;
  recommended_action: string;
  cluster_key: string;
  affected_case_ids: string[];
}

export interface CandidateResponse {
  id: string;
  change_type: string;
  status: string;
  attempt_number: number;
  hypothesis?: string | null;
  patch_hash?: string | null;
}

export interface ReleaseCompareResponse {
  release_id: string;
  previous_release_id?: string | null;
  changes: Record<string, unknown>[];
}

export interface EvalComparisonResponse {
  metric_name: string;
  baseline_score?: number | null;
  candidate_score?: number | null;
  delta?: number | null;
  is_regression: boolean;
}

// ── API calls ───────────────────────────────────────────────────────────

export function startIteration(
  agentId: string,
  req: StartIterationRequest,
): Promise<IterationResponse> {
  return apiPost(`/agents/${agentId}/optimization/iterations`, {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function listIterations(agentId: string): Promise<IterationResponse[]> {
  return apiGet(`/agents/${agentId}/optimization/iterations`);
}

export function getIteration(
  agentId: string,
  iterationId: string,
): Promise<IterationResponse> {
  return apiGet(`/agents/${agentId}/optimization/iterations/${iterationId}`);
}

export function cancelIteration(
  agentId: string,
  iterationId: string,
): Promise<{ status: string }> {
  return apiPost(`/agents/${agentId}/optimization/iterations/${iterationId}/cancel`, {
    method: 'POST',
  });
}

export function approveIteration(
  agentId: string,
  iterationId: string,
  actorId: string = 'operator',
): Promise<{ status: string }> {
  return apiPost(`/agents/${agentId}/optimization/iterations/${iterationId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ actor_id: actorId }),
  });
}

export function rejectIteration(
  agentId: string,
  iterationId: string,
  reason: string = '',
  actorId: string = 'operator',
): Promise<{ status: string }> {
  return apiPost(`/agents/${agentId}/optimization/iterations/${iterationId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ actor_id: actorId, reason }),
  });
}

export function listCandidates(
  agentId: string,
  iterationId: string,
): Promise<CandidateResponse[]> {
  return apiGet(`/agents/${agentId}/optimization/iterations/${iterationId}/candidates`);
}

export function publishCandidate(
  agentId: string,
  candidateId: string,
  actorId: string = 'operator',
): Promise<{ status: string; release_id: string }> {
  return apiPost(`/agents/${agentId}/optimization/candidates/${candidateId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ actor_id: actorId }),
  });
}

export function rollbackRelease(
  agentId: string,
  targetReleaseId: string,
  actorId: string = 'operator',
): Promise<{ status: string; release_id: string; rolled_back_to: string }> {
  return apiPost(`/agents/${agentId}/optimization/releases/rollback`, {
    method: 'POST',
    body: JSON.stringify({ target_release_id: targetReleaseId, actor_id: actorId }),
  });
}

export function listCheckpoints(
  agentId: string,
  iterationId: string,
): Promise<{ id: string; stage: string; thread_id: string; checkpoint_id: string | null }[]> {
  return apiGet(`/agents/${agentId}/optimization/checkpoints?iteration_id=${iterationId}`);
}

export function forkCheckpoint(
  agentId: string,
  checkpointId: string,
  candidateId: string,
): Promise<{ status: string; forked_checkpoint_id: string; thread_id: string }> {
  return apiPost(`/agents/${agentId}/optimization/checkpoints/${checkpointId}/fork`, {
    method: 'POST',
    body: JSON.stringify({ candidate_id: candidateId }),
  });
}

// ── Event types ──────────────────────────────────────────────────────────

export interface EventResponse {
  id: string;
  sequence_no: number;
  event_type: string;
  stage?: string | null;
  status?: string | null;
  progress_current?: number | null;
  progress_total?: number | null;
  message: string;
  payload: Record<string, unknown>;
  actor_type: string;
  actor_id?: string | null;
  created_at?: string | null;
}

export interface EventPageResponse {
  events: EventResponse[];
  next_sequence: number;
  terminal: boolean;
}

// ── Report types ─────────────────────────────────────────────────────────

export interface ReportMetricResponse {
  metric_name: string;
  group_name: string;
  direction: string;
  weight: number;
  before_value?: number | null;
  after_value?: number | null;
  before_normalized?: number | null;
  after_normalized?: number | null;
  delta?: number | null;
  applicable: boolean;
  gate_result?: string | null;
}

export interface ReportCaseResponse {
  case_id: string;
  classification: string;
  cause?: string | null;
  before_pass?: boolean | null;
  after_pass?: boolean | null;
  score_delta?: number | null;
  rank_delta?: number | null;
  latency_delta_ms?: number | null;
  token_delta?: number | null;
}

export interface ReportSummaryResponse {
  id: string;
  tenant_id: string;
  agent_id: string;
  iteration_id: string;
  report_type: string;
  candidate_id?: string | null;
  candidate_key: string;
  release_id?: string | null;
  report_version: number;
  formula_version: string;
  status: string;
  recommendation?: string | null;
  effect_index_before?: number | null;
  effect_index_after?: number | null;
  effect_index_delta?: number | null;
  hard_gates: Record<string, unknown>;
  data_snapshot_hash?: string | null;
  created_at?: string | null;
}

export interface ReportDetailResponse extends ReportSummaryResponse {
  groups: Record<string, unknown>[];
  cases: ReportCaseResponse[];
}

export interface TrendPointResponse {
  report_id: string;
  iteration_id: string;
  recommendation?: string | null;
  effect_index_before?: number | null;
  effect_index_after?: number | null;
  effect_index_delta?: number | null;
  hard_gates: Record<string, unknown>;
  created_at?: string | null;
}

export interface TrendResponse {
  agent_id: string;
  trends: TrendPointResponse[];
}

// ── Event API calls ──────────────────────────────────────────────────────

export function listEvents(
  agentId: string,
  iterationId: string,
  afterSequence: number = 0,
  limit: number = 50,
): Promise<EventPageResponse> {
  return apiGet(
    `/agents/${agentId}/optimization/iterations/${iterationId}/events?after_sequence=${afterSequence}&limit=${limit}`,
  );
}

export function waitEvents(
  agentId: string,
  iterationId: string,
  afterSequence: number = 0,
  timeoutSeconds: number = 30,
): Promise<EventPageResponse> {
  return apiGet(
    `/agents/${agentId}/optimization/iterations/${iterationId}/events/wait?after_sequence=${afterSequence}&timeout_seconds=${timeoutSeconds}`,
  );
}

export function streamEventsUrl(
  agentId: string,
  iterationId: string,
  lastEventId: string = '',
): string {
  const base = `/agents/${agentId}/optimization/iterations/${iterationId}/events/stream`;
  return lastEventId ? `${base}?Last-Event-ID=${lastEventId}` : base;
}

// ── Report API calls ─────────────────────────────────────────────────────

export function listReports(
  agentId: string,
  iterationId: string,
): Promise<ReportSummaryResponse[]> {
  return apiGet(`/agents/${agentId}/optimization/iterations/${iterationId}/reports`);
}

export function getReport(
  agentId: string,
  iterationId: string,
  reportId: string,
): Promise<ReportDetailResponse> {
  return apiGet(
    `/agents/${agentId}/optimization/iterations/${iterationId}/reports/${reportId}`,
  );
}

export function getReportArtifactUrl(
  agentId: string,
  iterationId: string,
  reportId: string,
  format: string,
): string {
  return `/agents/${agentId}/optimization/iterations/${iterationId}/reports/${reportId}/artifacts/${format}`;
}

// ── Trend API calls ──────────────────────────────────────────────────────

export function getTrends(
  agentId: string,
  limit: number = 10,
): Promise<TrendResponse> {
  return apiGet(`/agents/${agentId}/optimization/optimization/trends?limit=${limit}`);
}
