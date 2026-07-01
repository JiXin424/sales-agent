/** Graph Debug Page — Mermaid visualization + test-run for LangGraph graphs.

Left panel: Tab-switched Mermaid diagrams + input box + Send.
Right panel: Two tabs:
  - 「实时 trace」: per-node execution trace (SSE live updates) + token output.
  - 「Checkpoint 时间轴」: read-only node-boundary state snapshots for the
    last run's checkpointer thread (read-only time-travel, no re-run).
*/

import { useState, useRef, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Input, Button, Collapse, Tag, Spin, Alert, Tabs, Empty, Select, Modal } from 'antd';
import {
  SendOutlined,
  ApiOutlined,
  HistoryOutlined,
  ThunderboltOutlined,
  EditOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import mermaid from 'mermaid';
import {
  getGraphDebugGraphs,
  runGraphDebug,
  getCheckpoints,
  getCheckpointState,
  updateCheckpointState,
  replayCheckpoint,
} from '@/api/graphDebug';
import type {
  GraphInfo,
  GraphDebugNodeEnd,
  GraphDebugDone,
  GraphDebugStarted,
  CheckpointMeta,
  CheckpointListResponse,
  CheckpointStateResponse,
  UpdateStateRequest,
  RecentDebugRun,
} from '@/api/types';
import JsonNode from './JsonNode';
import CheckpointDAG from './CheckpointDAG';
import './GraphDebugPage.css';

// Initialize mermaid
mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });

const RECENT_RUNS_KEY = 'graph-debug:runs';
const MAX_RECENT_RUNS = 20;

interface TraceEntry {
  node: string;
  startTime: number;
  endTime?: number;
  duration_ms?: number;
  input?: unknown;
  output?: Record<string, unknown>;
}

/** Load the recent-debug-runs list from localStorage (best-effort, never throws). */
function loadRunsFromStorage(): RecentDebugRun[] {
  try {
    const raw = localStorage.getItem(RECENT_RUNS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (r): r is RecentDebugRun =>
        r &&
        typeof r === 'object' &&
        typeof r.thread_id === 'string' &&
        typeof r.graph_id === 'string' &&
        typeof r.message === 'string' &&
        typeof r.ts === 'string',
    );
  } catch {
    return [];
  }
}

/** Persist a run into localStorage, deduping by thread_id and capping at MAX_RECENT_RUNS. */
function saveRunToStorage(run: RecentDebugRun) {
  try {
    const existing = loadRunsFromStorage();
    const deduped = existing.filter(r => r.thread_id !== run.thread_id);
    deduped.unshift(run);
    const capped = deduped.slice(0, MAX_RECENT_RUNS);
    localStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(capped));
  } catch {
    // localStorage may be unavailable (private mode, quota) — fail silently.
  }
}

function summarizeMessage(msg: string, max = 40): string {
  const trimmed = msg.trim().replace(/\s+/g, ' ');
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
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

  // ── Time-travel state ──
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointMeta[]>([]);
  const [selectedCheckpoint, setSelectedCheckpoint] = useState<CheckpointMeta | null>(null);
  const [checkpointState, setCheckpointState] = useState<CheckpointStateResponse | null>(null);
  const [checkpointLoading, setCheckpointLoading] = useState(false);
  const [checkpointError, setCheckpointError] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<'trace' | 'timeline'>('trace');
  const [recentRuns, setRecentRuns] = useState<RecentDebugRun[]>(() => loadRunsFromStorage());

  // ── Fork (A2) state ──
  // When editing, draftValues holds the user-edited copy of checkpointState.values;
  // the timeline is replaced by an editable JsonNode until Submit or Cancel.
  const [editingFork, setEditingFork] = useState(false);
  const [draftValues, setDraftValues] = useState<Record<string, unknown> | null>(null);
  const [forkLoading, setForkLoading] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);

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
    if (mermaidSvg[activeGraphId]) return; // already rendered

    // Remove Mermaid YAML front-matter block (v11 doesn't parse it)
    const cleanMermaid = g.mermaid.replace(/^---\n[\s\S]*?\n---\n/, '');

    (async () => {
      try {
        const { svg } = await mermaid.render(`mermaid-${activeGraphId}`, cleanMermaid);
        setMermaidSvg(prev => ({ ...prev, [activeGraphId]: svg }));
      } catch {
        setMermaidSvg(prev => ({ ...prev, [activeGraphId]: `<pre>${cleanMermaid}</pre>` }));
      }
    })();
  }, [graphs, activeGraphId, mermaidSvg]);

  // ── Fetch checkpoint list for a thread (used by both run-done and history select) ──
  const fetchCheckpoints = useCallback(
    async (threadId: string) => {
      if (!agentId || !threadId) return;
      setCheckpointLoading(true);
      setCheckpointError(null);
      setSelectedCheckpoint(null);
      setCheckpointState(null);
      setEditingFork(false);
      setDraftValues(null);
      setForkError(null);
      try {
        const resp: CheckpointListResponse = await getCheckpoints(agentId, threadId);
        setCheckpoints(resp.checkpoints ?? []);
      } catch (e: any) {
        setCheckpointError(e?.message || String(e));
        setCheckpoints([]);
      } finally {
        setCheckpointLoading(false);
      }
    },
    [agentId],
  );

  // ── Selecting a checkpoint → fetch its full state ──
  const handleSelectCheckpoint = useCallback(
    async (meta: CheckpointMeta) => {
      if (!agentId || !currentThreadId) return;
      setSelectedCheckpoint(meta);
      setCheckpointState(null);
      setCheckpointError(null);
      setEditingFork(false);
      setDraftValues(null);
      setForkError(null);
      try {
        const resp: CheckpointStateResponse = await getCheckpointState(
          agentId,
          currentThreadId,
          meta.checkpoint_id,
        );
        setCheckpointState(resp);
      } catch (e: any) {
        setCheckpointError(e?.message || String(e));
      }
    },
    [agentId, currentThreadId],
  );

  // ── Selecting a historical run from the dropdown ──
  const handleSelectRun = useCallback(
    (threadId: string) => {
      const run = recentRuns.find(r => r.thread_id === threadId);
      if (!run) return;
      // Only switch graph if it differs and exists.
      if (run.graph_id && run.graph_id !== activeGraphId) {
        const exists = graphs.some(g => g.id === run.graph_id);
        if (exists) setActiveGraphId(run.graph_id);
      }
      setCurrentThreadId(run.thread_id);
      setRightTab('timeline');
      void fetchCheckpoints(run.thread_id);
    },
    [recentRuns, graphs, activeGraphId, fetchCheckpoints],
  );

  // ── Fork: enter edit mode ──
  const handleStartEdit = useCallback(() => {
    if (!checkpointState?.values) return;
    // Deep clone so draft edits do not mutate the displayed checkpoint state.
    setDraftValues(JSON.parse(JSON.stringify(checkpointState.values)));
    setForkError(null);
    setEditingFork(true);
  }, [checkpointState]);

  const handleCancelEdit = useCallback(() => {
    setEditingFork(false);
    setDraftValues(null);
    setForkError(null);
  }, []);

  // ── Fork: submit edited values (update) + replay the tail ──
  // Flow: update → Modal.confirm → replay SSE → done refreshes timeline.
  const handleSubmitFork = useCallback(async () => {
    if (!agentId || !currentThreadId || !selectedCheckpoint || !draftValues) return;
    setForkLoading(true);
    setForkError(null);
    try {
      const body: UpdateStateRequest = { values: draftValues, graph_id: activeGraphId };
      const resp = await updateCheckpointState(
        agentId,
        currentThreadId,
        selectedCheckpoint.checkpoint_id,
        body,
      );
      const newCheckpointId = resp.checkpoint_id;

      Modal.confirm({
        title: '确认重跑后半段?',
        content: '重跑会重新调用 LLM / 查库,产生实际开销。确定从该 checkpoint 重跑吗?',
        okText: '重跑',
        cancelText: '取消',
        onOk: () =>
          doReplay(agentId, currentThreadId, newCheckpointId, activeGraphId, message),
      });
    } catch (e: any) {
      setForkError(e?.message || String(e));
    } finally {
      setForkLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, currentThreadId, selectedCheckpoint, draftValues, activeGraphId, message]);

  /** Run the replay SSE stream and route events through dispatchSSE.
   *  Lives outside useCallback so it can close over the latest handler refs. */
  function doReplay(
    agent: string,
    threadId: string,
    checkpointId: string,
    graphId: string,
    runMessage: string,
  ) {
    // Reset trace panel for the replay run.
    setStreaming(true);
    setTrace([]);
    setTokens('');
    setDoneInfo(null);
    setError(null);
    setRightTab('trace');

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const traceMap = new Map<string, TraceEntry>();

    (async () => {
      try {
        const resp = await replayCheckpoint(agent, threadId, checkpointId, { graph_id: graphId });
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
            if (line.startsWith('event: ')) continue;
            if (!line.startsWith('data: ')) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (!evt) continue;
              dispatchSSE(evt, traceMap);
            } catch {
              /* skip parse errors */
            }
          }
        }
      } catch (e: any) {
        if (e.name !== 'AbortError') {
          setError(e.message || String(e));
          setForkError(e.message || String(e));
        }
      } finally {
        setStreaming(false);
        setForkLoading(false);
        abortRef.current = null;
      }
    })();
  }

  // ── Run graph ──
  const handleSend = useCallback(async () => {
    if (!message.trim() || streaming || !agentId) return;
    setStreaming(true);
    setTrace([]);
    setTokens('');
    setDoneInfo(null);
    setError(null);
    setCurrentThreadId(null);
    setCheckpoints([]);
    setSelectedCheckpoint(null);
    setCheckpointState(null);
    setRightTab('trace');

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
    if (type === 'started') {
      const started = evt as unknown as GraphDebugStarted;
      const tid = started.thread_id;
      if (typeof tid === 'string' && tid) {
        setCurrentThreadId(tid);
      }
    } else if (type === 'node_start') {
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
      const doneEvt = evt as unknown as GraphDebugDone;
      setDoneInfo(doneEvt);
      // After run completes, pull the checkpoint timeline for this thread.
      // currentThreadId may still be null in the closure if `started` was missed —
      // read it via the setter form to get the latest value.
      setCurrentThreadId(prevTid => {
        if (prevTid) {
          void fetchCheckpoints(prevTid);
          // Persist to localStorage.
          saveRunToStorage({
            thread_id: prevTid,
            graph_id: activeGraphId,
            message: summarizeMessage(message),
            ts: new Date().toISOString(),
          });
          setRecentRuns(loadRunsFromStorage());
        }
        return prevTid;
      });
    } else if (type === 'error') {
      setError((evt as any).message || 'Unknown error');
    }
    // Update trace state
    setTrace(Array.from(traceMap.values()));
  }

  // Cleanup on unmount
  useEffect(() => () => abortRef.current?.abort(), []);

  // ── Runs dropdown options: only show those matching the current activeGraphId ──
  const runOptions = recentRuns
    .filter(r => r.graph_id === activeGraphId)
    .map(r => ({
      value: r.thread_id,
      label: (
        <span>
          <span style={{ color: '#8c8c8c', fontSize: 11 }}>{r.ts.slice(0, 19).replace('T', ' ')}</span>
          {'  '}
          <span>{r.message || r.thread_id.slice(0, 16)}</span>
        </span>
      ),
    }));

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
          {currentThreadId && (
            <Tag color="geekblue" style={{ marginLeft: 8 }} title={currentThreadId}>
              thread: {currentThreadId.slice(0, 18)}…
            </Tag>
          )}
          <Select
            className="gd-runs-select"
            showSearch
            placeholder="最近调试 run"
            value={currentThreadId ?? undefined}
            onChange={handleSelectRun}
            options={runOptions}
            allowClear
            notFoundContent="暂无历史 run"
          />
        </div>
        <div className="gd-right-body">
          {error && (
            <Alert type="error" message={error} closable style={{ marginBottom: 12 }} />
          )}
          {checkpointError && (
            <Alert
              type="error"
              message={`Checkpoint 加载失败：${checkpointError}`}
              closable
              onClose={() => setCheckpointError(null)}
              style={{ marginBottom: 12 }}
            />
          )}
          <Tabs
            activeKey={rightTab}
            onChange={key => setRightTab(key as 'trace' | 'timeline')}
            items={[
              {
                key: 'trace',
                label: (
                  <span>
                    <ThunderboltOutlined style={{ marginRight: 4 }} />
                    实时 trace
                  </span>
                ),
                children: (
                  <>
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
                  </>
                ),
              },
              {
                key: 'timeline',
                label: (
                  <span>
                    <HistoryOutlined style={{ marginRight: 4 }} />
                    Checkpoint 时间轴
                    {checkpoints.length > 0 && (
                      <Tag style={{ marginLeft: 6 }}>{checkpoints.length}</Tag>
                    )}
                  </span>
                ),
                children: (
                  <>
                    {!currentThreadId && (
                      <Empty description="发起一次 run 后,此处显示每个节点执行完毕时的 state 快照" />
                    )}
                    {currentThreadId && checkpointLoading && (
                      <div style={{ textAlign: 'center', padding: 24 }}>
                        <Spin tip="加载 checkpoint 时间轴..." />
                      </div>
                    )}
                    {currentThreadId && !checkpointLoading && checkpoints.length === 0 && !checkpointError && (
                      <div className="gd-checkpoint-empty">
                        该 run 没有 checkpoint(可能图未接 checkpointer,或 run 未完成)。
                      </div>
                    )}
                    {checkpoints.length > 0 && (
                      <>
                        <CheckpointDAG
                          checkpoints={checkpoints}
                          selectedCheckpointId={selectedCheckpoint?.checkpoint_id ?? null}
                          onSelect={cp => void handleSelectCheckpoint(cp)}
                        />
                        {selectedCheckpoint && checkpointState && (
                          <div className="gd-checkpoint-state">
                            <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center' }}>
                              <Tag color="blue">{selectedCheckpoint.node ?? '-'}</Tag>
                              {selectedCheckpoint.step != null && (
                                <Tag>step {selectedCheckpoint.step}</Tag>
                              )}
                              <strong style={{ marginLeft: 8 }}>values</strong>
                              <span style={{ marginLeft: 'auto' }}>
                                {!editingFork ? (
                                  <Button
                                    size="small"
                                    icon={<EditOutlined />}
                                    onClick={handleStartEdit}
                                    disabled={streaming || forkLoading}
                                  >
                                    编辑并 fork
                                  </Button>
                                ) : (
                                  <>
                                    <Button
                                      size="small"
                                      icon={<PlayCircleOutlined />}
                                      type="primary"
                                      onClick={handleSubmitFork}
                                      loading={forkLoading || streaming}
                                      style={{ marginRight: 8 }}
                                    >
                                      提交并重跑
                                    </Button>
                                    <Button
                                      size="small"
                                      onClick={handleCancelEdit}
                                      disabled={forkLoading || streaming}
                                    >
                                      取消
                                    </Button>
                                  </>
                                )}
                              </span>
                            </div>
                            {forkError && (
                              <Alert
                                type="error"
                                message={`Fork 失败：${forkError}`}
                                closable
                                onClose={() => setForkError(null)}
                                style={{ marginBottom: 8 }}
                              />
                            )}
                            {editingFork && draftValues ? (
                              <JsonNode
                                value={draftValues}
                                defaultCollapsed={false}
                                editable
                                onChange={(v) => setDraftValues(v as Record<string, unknown>)}
                              />
                            ) : (
                              <JsonNode value={checkpointState.values} defaultCollapsed={false} />
                            )}
                            {!editingFork &&
                              checkpointState.next &&
                              checkpointState.next.length > 0 && (
                                <div style={{ marginTop: 8 }}>
                                  <strong>next:</strong>{' '}
                                  <Tag>{checkpointState.next.join(', ')}</Tag>
                                </div>
                              )}
                            {editingFork && (
                              <div style={{ marginTop: 8, fontSize: 11, color: '#8c8c8c' }}>
                                编辑叶节点(string/number/boolean)→ 提交后从该 checkpoint 重跑后半段。
                                重跑会调用 LLM / 查库。
                              </div>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </>
                ),
              },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
