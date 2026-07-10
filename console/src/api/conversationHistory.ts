/** Conversation History API client — read-only checkpoint time-travel for
 *  REAL production conversations (DingTalk stream).
 *
 *  Unlike graphDebug's checkpoint endpoints (which only accept ``debug:``
 *  threads), these read any conversation by its ``conversation_id`` directly,
 *  using it as the LangGraph ``thread_id``. STRICTLY GET — no edit/fork/replay.
 */

import { apiGet } from './client';
import type { CheckpointListResponse, CheckpointStateResponse } from './types';

export interface ConversationHistoryItem {
  conversation_id: string;
  message: string;
  channel: string | null;
  task_type: string | null;
  status: string | null;
  updated_at: string | null;
}

export interface ConversationHistoryListResponse {
  conversations: ConversationHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

/** List recent real conversations for an agent (newest first). */
export function listConversationHistory(agentId: string, limit = 50, offset = 0) {
  return apiGet<ConversationHistoryListResponse>(
    `/agents/${agentId}/history/conversations`,
    { limit, offset },
  );
}

/** Read-only checkpoint timeline for a real conversation (node-boundary snapshots). */
export function getConversationCheckpoints(agentId: string, conversationId: string) {
  return apiGet<CheckpointListResponse>(
    `/agents/${agentId}/history/conversations/${encodeURIComponent(conversationId)}/checkpoints`,
  );
}

/** Read-only full state values for one checkpoint of a real conversation. */
export function getConversationCheckpointState(
  agentId: string,
  conversationId: string,
  checkpointId: string,
) {
  return apiGet<CheckpointStateResponse>(
    `/agents/${agentId}/history/conversations/${encodeURIComponent(conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}/state`,
  );
}
