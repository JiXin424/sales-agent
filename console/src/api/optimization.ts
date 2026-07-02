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
