import { beforeEach, describe, expect, it, vi } from 'vitest';

// @ts-expect-error - stubbing browser globals in node
globalThis.window = { location: { origin: 'http://localhost' } };

import {
  getOntologyStatus,
  startOntologyIngest,
  listOntologyJobs,
} from '@/api/knowledge';

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
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
});

describe('Ontology knowledge API wrappers', () => {
  it('gets ontology status', async () => {
    await getOntologyStatus('a1');
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/ontology/status');
  });

  it('starts ontology ingest', async () => {
    await startOntologyIngest('a1', '/tmp/sample.md');
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents/a1/ontology/ingest');
    expect(lastCall!.body).toEqual({ path: '/tmp/sample.md' });
  });

  it('lists jobs', async () => {
    await listOntologyJobs('a1', 20, 0);
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/ontology/jobs');
    expect(lastCall!.url).toContain('limit=20');
  });
});
