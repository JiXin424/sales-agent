/** 本体探索器（Ontology Explorer）API：stats / query / query/stream。 */

import { apiGet, apiPost } from './client';
import type {
  OntologyStats,
  OntologyQueryRequest,
  OntologyQueryResponse,
} from './types';

/** 头部徽章：按实体类型计数。 */
export function getOntologyStats(agentId: string) {
  return apiGet<OntologyStats>(`/agents/${agentId}/ontology/stats`);
}

/** 同步本体查询：返回答案 + 检索过程 + 完整上下文。 */
export function ontologyQuery(agentId: string, body: OntologyQueryRequest) {
  return apiPost<OntologyQueryResponse>(`/agents/${agentId}/ontology/query`, body);
}

/**
 * 流式本体查询（SSE）。POST 不能用 EventSource，故用 fetch + ReadableStream 手解
 * （与外部项目同款）。返回原始 Response，由调用方逐行解析 `data: <json>` 事件。
 *
 * 同源 /api 前缀由 vite 代理转发（见 .env.development 与 vite.config.ts）。
 */
export function ontologyQueryStream(agentId: string, body: OntologyQueryRequest): Promise<Response> {
  return fetch(`/api/agents/${agentId}/ontology/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
