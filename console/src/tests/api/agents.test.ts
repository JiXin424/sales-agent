/** Agent API layer tests — verify path/method/body mapping (no real network). */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// Provide a window + location so the client's new URL(..., window.location.origin) works.
// @ts-expect-error - stubbing browser globals in node
globalThis.window = { location: { origin: 'http://localhost' } };

import {
  listAgents, getAgent, createAgent, updateAgent, cloneAgent,
  getCloneManifest, getReadiness, activateAgent, pauseAgent, archiveAgent,
} from '@/api/agents';
import type { CloneOptions } from '@/api/types';

let lastCall: { method: string; url: string; body: unknown } | null = null;

beforeEach(() => {
  lastCall = null;
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === 'string' ? input : input.toString();
    lastCall = {
      method: init?.method ?? 'GET',
      url: u,
      body: init?.body ? JSON.parse(init.body as string) : null,
    };
    return new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
});

describe('Agent API wrappers', () => {
  it('listAgents hits GET /agents with filters', async () => {
    await listAgents({ status: 'active', q: 'x' });
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents');
    expect(lastCall!.url).toContain('status=active');
    expect(lastCall!.url).toContain('q=x');
  });

  it('getAgent hits GET /agents/{id}', async () => {
    await getAgent('a1');
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1');
  });

  it('createAgent hits POST /agents with body', async () => {
    await createAgent({ tenant_id: 't1', name: 'A' });
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents');
    expect(lastCall!.body).toEqual({ tenant_id: 't1', name: 'A' });
  });

  it('updateAgent hits PATCH /agents/{id}', async () => {
    await updateAgent('a1', { name: 'A2' });
    expect(lastCall!.method).toBe('PATCH');
    expect(lastCall!.url).toContain('/agents/a1');
    expect(lastCall!.body).toEqual({ name: 'A2' });
  });

  it('cloneAgent hits POST /agents/{id}/clone with options', async () => {
    const opts: CloneOptions = {
      name: 'C', prompt_set: 'copy', risk_policy: 'copy',
      knowledge_scope: 'reference', eval_suite: 'copy',
      channel_config: 'shell_only', model_config_choice: 'reference',
    };
    await cloneAgent('a1', opts);
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents/a1/clone');
    expect(lastCall!.body).toEqual(opts);
  });

  it('readiness/manifest are GET', async () => {
    await getReadiness('a1');
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/readiness');
    await getCloneManifest('a1');
    expect(lastCall!.url).toContain('/agents/a1/clone-manifest');
  });

  it('activate/pause/archive are POST', async () => {
    await activateAgent('a1');
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents/a1/activate');
    await activateAgent('a1', { eval: 'reason' });
    expect(lastCall!.body).toEqual({ waiver_reasons: { eval: 'reason' } });

    await pauseAgent('a1');
    expect(lastCall!.url).toContain('/agents/a1/pause');
    await archiveAgent('a1');
    expect(lastCall!.url).toContain('/agents/a1/archive');
  });
});
