/** Graph Debug API client. */

import { apiGet, apiPost } from './client';
import type {
  GraphListResponse,
  CheckpointListResponse,
  CheckpointStateResponse,
  UpdateStateRequest,
  UpdateStateResponse,
  ReplayRequest,
} from './types';

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

/** List checkpoints (node-boundary snapshots) for a debug thread — read-only timeline. */
export function getCheckpoints(agentId: string, threadId: string) {
  return apiGet<CheckpointListResponse>(
    `/agents/${agentId}/graph-debug/threads/${encodeURIComponent(threadId)}/checkpoints`,
  );
}

/** Get full state values for one checkpoint on the timeline. */
export function getCheckpointState(
  agentId: string,
  threadId: string,
  checkpointId: string,
) {
  return apiGet<CheckpointStateResponse>(
    `/agents/${agentId}/graph-debug/threads/${encodeURIComponent(threadId)}/checkpoints/${encodeURIComponent(checkpointId)}/state`,
  );
}

/** Fork: update a checkpoint's state values. Returns the new checkpoint_id.
 *  Body contract (strict): `{ values: Record<string, unknown>, graph_id?: string }`. */
export function updateCheckpointState(
  agentId: string,
  threadId: string,
  checkpointId: string,
  body: UpdateStateRequest,
) {
  return apiPost<UpdateStateResponse>(
    `/agents/${agentId}/graph-debug/threads/${encodeURIComponent(threadId)}/checkpoints/${encodeURIComponent(checkpointId)}/state`,
    body,
  );
}

/** Fork: replay the tail of the graph from a checkpoint (input=None resumes
 *  from the checkpoint's `next` nodes). Streams the same SSE event vocabulary
 *  as `/run` (node_start / node_output / node_end / done / error). */
export async function replayCheckpoint(
  agentId: string,
  threadId: string,
  checkpointId: string,
  body?: ReplayRequest,
): Promise<Response> {
  return fetch(
    `/api/agents/${agentId}/graph-debug/threads/${encodeURIComponent(threadId)}/checkpoints/${encodeURIComponent(checkpointId)}/replay`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    },
  );
}
