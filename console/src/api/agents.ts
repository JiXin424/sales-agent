/** Agent Instance API wrappers — one-to-one Agent management + clone. */

import { apiGet, apiPost, apiPatch } from './client';
import type {
  AgentInstance,
  AgentCreateRequest,
  CloneOptions,
  CloneResult,
  CloneManifest,
  ReadinessReport,
  PaginatedResponse,
} from './types';

export interface AgentListFilters {
  tenant_id?: string;
  status?: string;
  agent_type?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export function listAgents(filters?: AgentListFilters) {
  return apiGet<PaginatedResponse<AgentInstance>>(
    '/agents',
    filters as Record<string, string | number | undefined>,
  );
}

/** 当前实例（dedicated）的唯一 Agent —— 单 Agent 模式的入口解析。 */
export function getInstanceAgent() {
  return apiGet<AgentInstance>('/instance/agent');
}

export function getAgent(agentId: string) {
  return apiGet<AgentInstance>(`/agents/${agentId}`);
}

export function createAgent(req: AgentCreateRequest) {
  return apiPost<AgentInstance>('/agents', req);
}

export function updateAgent(
  agentId: string,
  body: { name?: string; description?: string; feature_flags?: Record<string, unknown> },
) {
  return apiPatch<AgentInstance>(`/agents/${agentId}`, body);
}

export function cloneAgent(sourceAgentId: string, options: CloneOptions) {
  return apiPost<CloneResult>(`/agents/${sourceAgentId}/clone`, options);
}

export function getCloneManifest(agentId: string) {
  return apiGet<CloneManifest>(`/agents/${agentId}/clone-manifest`);
}

export function getReadiness(agentId: string) {
  return apiGet<ReadinessReport>(`/agents/${agentId}/readiness`);
}

export function activateAgent(agentId: string, waiverReasons?: Record<string, string>) {
  return apiPost<AgentInstance>(`/agents/${agentId}/activate`, {
    waiver_reasons: waiverReasons ?? null,
  });
}

export function pauseAgent(agentId: string) {
  return apiPost<AgentInstance>(`/agents/${agentId}/pause`, {});
}

export function archiveAgent(agentId: string) {
  return apiPost<AgentInstance>(`/agents/${agentId}/archive`, {});
}

// Agent-scoped data wrappers
export function listAgentConversations(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/conversations`, { limit, offset });
}

export function listAgentFeedback(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/feedback`, { limit, offset });
}

export function listAgentDocuments(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/knowledge/documents`, { limit, offset });
}

export interface AgentPromptsResponse extends PaginatedResponse<unknown> {
  prompt_set_mapping?: Record<string, string>;
}

export function listAgentPrompts(agentId: string, limit = 100, offset = 0) {
  return apiGet<AgentPromptsResponse>(`/agents/${agentId}/prompts`, { limit, offset });
}

export function listAgentChannels(agentId: string) {
  return apiGet<{ items: unknown[]; total: number }>(`/agents/${agentId}/channels`);
}

export function listAgentAlerts(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/alerts`, { limit, offset });
}

export function listAgentReports(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/reports`, { limit, offset });
}

export function listAgentEvalRuns(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/eval-runs`, { limit, offset });
}

export function listAgentReviewQueue(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/review-queue`, { limit, offset });
}

export function listAgentKnowledgeGaps(agentId: string, limit = 50, offset = 0) {
  return apiGet<PaginatedResponse<unknown>>(`/agents/${agentId}/knowledge-gaps`, { limit, offset });
}
