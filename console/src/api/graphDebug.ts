/** Graph Debug API client. */

import { apiGet } from './client';
import type { GraphListResponse } from './types';

export function getGraphDebugGraphs(agentId: string) {
  return apiGet<GraphListResponse>(`/agents/${agentId}/graph-debug/graphs`);
}

export async function runGraphDebug(
  agentId: string,
  body: { graph_id: string; message: string; tenant_id?: string },
): Promise<Response> {
  return fetch(`/api/agents/${agentId}/graph-debug/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
