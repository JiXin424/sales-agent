import { beforeEach, describe, expect, it, vi } from 'vitest';

// @ts-expect-error - stubbing browser globals in node
globalThis.window = { location: { origin: 'http://localhost' } };

import {
  getOntologyStatus,
  startOntologyIngest,
  listOntologyJobs,
  subscribeJobEvents,
} from '@/api/knowledge';

let lastCall: { method: string; url: string; body: unknown } | null = null;

beforeEach(() => {
  lastCall = null;
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === 'string' ? input : input.toString();
    lastCall = {
      method: init?.method ?? 'GET',
      url: u,
      body: init?.body instanceof FormData ? null : (init?.body ? JSON.parse(init.body as string) : null),
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

  it('starts ontology ingest with multiple files', async () => {
    const f1 = new File(['content1'], 'test1.md', { type: 'text/markdown' });
    const f2 = new File(['content2'], 'test2.md', { type: 'text/markdown' });
    await startOntologyIngest('a1', [f1, f2]);
    expect(lastCall!.method).toBe('POST');
    expect(lastCall!.url).toContain('/agents/a1/ontology/ingest');
  });

  it('creates EventSource for job events', () => {
    const orgES = globalThis.EventSource;
    // stub EventSource for this test
    const esUrl: string[] = [];
    globalThis.EventSource = class {
      url: string;
      constructor(url: string) { this.url = url; esUrl.push(url); }
      close() {}
      onmessage: ((e: any) => void) | null = null;
      onerror: ((e: any) => void) | null = null;
    } as any;
    const es = subscribeJobEvents('a1', 'j1');
    expect(es.url).toContain('/agents/a1/ontology/jobs/j1/events');
    es.close();
    globalThis.EventSource = orgES;
  });

  it('lists jobs', async () => {
    await listOntologyJobs('a1', 20, 0);
    expect(lastCall!.method).toBe('GET');
    expect(lastCall!.url).toContain('/agents/a1/ontology/jobs');
    expect(lastCall!.url).toContain('limit=20');
  });
});
