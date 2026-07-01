/** Graph Debug Page — Mermaid visualization + test-run for LangGraph graphs.

Left panel: Tab-switched Mermaid diagrams + input box + Send.
Right panel: Execution trace (per-node state snapshots) + token output.
*/

import { useState, useRef, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Input, Button, Collapse, Tag, Spin, Alert, Tabs, Empty } from 'antd';
import { SendOutlined, ApiOutlined } from '@ant-design/icons';
import mermaid from 'mermaid';
import { getGraphDebugGraphs, runGraphDebug } from '@/api/graphDebug';
import type { GraphInfo, GraphDebugNodeEnd, GraphDebugNodeOutput, GraphDebugDone } from '@/api/types';
import './GraphDebugPage.css';

// Initialize mermaid
mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });

interface TraceEntry {
  node: string;
  startTime: number;
  endTime?: number;
  duration_ms?: number;
  input?: unknown;
  output?: Record<string, unknown>;
}

export default function GraphDebugPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const [activeGraphId, setActiveGraphId] = useState<string>('chat');
  const [message, setMessage] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [tokens, setTokens] = useState('');
  const [doneInfo, setDoneInfo] = useState<GraphDebugDone | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mermaidSvg, setMermaidSvg] = useState<Record<string, string>>({});
  const abortRef = useRef<AbortController | null>(null);
  const mermaidRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // ── Load graph list ──
  const graphsQuery = useQuery({
    queryKey: ['graph-debug-graphs', agentId],
    queryFn: () => getGraphDebugGraphs(agentId!),
    enabled: !!agentId,
    refetchOnWindowFocus: false,
  });

  const graphs: GraphInfo[] = graphsQuery.data?.graphs ?? [];

  // ── Render Mermaid for active graph ──
  useEffect(() => {
    if (!graphs.length) return;
    const g = graphs.find(g => g.id === activeGraphId);
    if (!g || !g.mermaid) return;
    const el = mermaidRefs.current[activeGraphId];
    if (!el) return;
    if (mermaidSvg[activeGraphId]) return; // already rendered

    (async () => {
      try {
        const { svg } = await mermaid.render(`mermaid-${activeGraphId}`, g.mermaid);
        setMermaidSvg(prev => ({ ...prev, [activeGraphId]: svg }));
      } catch {
        // Mermaid render failed — show raw
        setMermaidSvg(prev => ({ ...prev, [activeGraphId]: `<pre>${g.mermaid}</pre>` }));
      }
    })();
  }, [graphs, activeGraphId, mermaidSvg]);

  // ── Run graph ──
  const handleSend = useCallback(async () => {
    if (!message.trim() || streaming || !agentId) return;
    setStreaming(true);
    setTrace([]);
    setTokens('');
    setDoneInfo(null);
    setError(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const traceMap = new Map<string, TraceEntry>();

    try {
      const resp = await runGraphDebug(agentId, {
        graph_id: activeGraphId,
        message: message.trim(),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            // SSE event name — stored for next data line
            continue;
          }
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (!evt) continue;
            dispatchSSE(evt, traceMap);
          } catch { /* skip parse errors */ }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message || String(e));
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [message, streaming, agentId, activeGraphId]);

  function dispatchSSE(evt: Record<string, unknown>, traceMap: Map<string, TraceEntry>) {
    const type = evt.type as string;
    if (type === 'node_start') {
      const n = (evt as any).node || 'unknown';
      traceMap.set(n, { node: n, startTime: Date.now(), input: (evt as any).input, output: undefined });
    } else if (type === 'node_output') {
      const n = (evt as any).node || 'unknown';
      const entry: TraceEntry = traceMap.get(n) || { node: n, startTime: Date.now(), output: undefined };
      entry.output = (evt as any).output;
      traceMap.set(n, entry);
    } else if (type === 'node_end') {
      const nd = evt as unknown as GraphDebugNodeEnd;
      const entry: TraceEntry = traceMap.get(nd.node) || { node: nd.node, startTime: Date.now(), output: undefined };
      entry.duration_ms = nd.duration_ms;
      entry.output = entry.output || (nd as any).result;
      traceMap.set(nd.node, entry);
    } else if (type === 'done') {
      setDoneInfo(evt as unknown as GraphDebugDone);
    } else if (type === 'error') {
      setError((evt as any).message || 'Unknown error');
    }
    // Update trace state
    setTrace(Array.from(traceMap.values()));
  }

  // Cleanup on unmount
  useEffect(() => () => abortRef.current?.abort(), []);

  // ── Render ──
  return (
    <div className="gd-container">
      {/* Left panel */}
      <div className="gd-left">
        <div className="gd-left-header">图结构</div>
        <div className="gd-left-body">
          {graphsQuery.isLoading ? (
            <Spin tip="加载图列表..." />
          ) : graphs.length === 0 ? (
            <Empty description="没有可用的图" />
          ) : (
            <Tabs
              activeKey={activeGraphId}
              onChange={setActiveGraphId}
              items={graphs.map(g => ({
                key: g.id,
                label: (
                  <span>
                    <ApiOutlined style={{ marginRight: 4 }} />
                    {g.name}
                    <Tag style={{ marginLeft: 6 }}>{g.node_count}N {g.edge_count}E</Tag>
                  </span>
                ),
                children: (
                  <div className="gd-mermaid-wrap">
                    {mermaidSvg[g.id] ? (
                      <div
                        className="gd-mermaid"
                        dangerouslySetInnerHTML={{ __html: mermaidSvg[g.id] }}
                      />
                    ) : (
                      <Spin tip="渲染图中..." />
                    )}
                  </div>
                ),
              }))}
            />
          )}
          <div className="gd-input-row">
            <Input.TextArea
              value={message}
              onChange={e => setMessage(e.target.value)}
              placeholder="输入测试消息... 例：客户说太贵了怎么回？"
              rows={3}
              onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleSend(); } }}
              disabled={streaming}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={streaming}
              disabled={!message.trim()}
              style={{ marginTop: 8 }}
            >
              {streaming ? '执行中...' : '发送'}
            </Button>
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="gd-right">
        <div className="gd-right-header">
          执行轨迹
          {doneInfo && (
            <Tag color="green" style={{ marginLeft: 8 }}>
              {doneInfo.total_duration_ms}ms
            </Tag>
          )}
          {error && <Tag color="red">{error}</Tag>}
        </div>
        <div className="gd-right-body">
          {error && (
            <Alert type="error" message={error} closable style={{ marginBottom: 12 }} />
          )}
          {trace.length === 0 && !streaming && !error && (
            <Empty description="输入消息并点击发送，查看执行轨迹" />
          )}
          {trace.length > 0 && (
            <Collapse
              size="small"
              items={trace.map((t, i) => ({
                key: `${t.node}-${i}`,
                label: (
                  <span>
                    <Tag color="blue">{t.node}</Tag>
                    {t.duration_ms != null && (
                      <Tag color={t.duration_ms > 500 ? 'orange' : 'green'}>{t.duration_ms}ms</Tag>
                    )}
                    {!t.duration_ms && streaming && <Tag>执行中...</Tag>}
                  </span>
                ),
                children: (
                  <div className="gd-trace-detail">
                    {t.input != null && (
                      <div>
                        <strong>Input:</strong>
                        <pre>{JSON.stringify(t.input, null, 2)}</pre>
                      </div>
                    )}
                    {t.output != null && (
                      <div>
                        <strong>Output:</strong>
                        <pre>{JSON.stringify(t.output, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                ),
              }))}
            />
          )}
          {tokens && (
            <div className="gd-tokens">
              <div className="gd-tokens-header">LLM 输出</div>
              <pre className="gd-tokens-body">{tokens}</pre>
            </div>
          )}
          {streaming && trace.length === 0 && (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin tip="等待图中..." />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
