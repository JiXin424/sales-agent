/**
 * 本体探索器（Ontology Explorer）—— 三栏调试 UI。
 *
 * 从影响力本体问答系统 web/index.html 移植为 React + TS + Ant Design：
 *   左「检索过程」实时展示 GraphEvidence 检索命中（SSE step/search_process）
 *   中「问答」提问 + markdown 答案 + 相关实体卡（点击再问）
 *   右「完整上下文」展示喂给大模型的 system prompt + 图谱证据
 *
 * 后端复用 OntologyRetrievalService + OntologyAnswerService（POST /query/stream SSE）。
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { Alert, Spin } from 'antd';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getOntologyStats, ontologyQueryStream } from '@/api/ontology';
import { getOntologyStatus } from '@/api/knowledge';
import { queryKeys } from '@/utils/queryKeys';
import type {
  OntologyEntityNode,
  OntologySearchProcess,
  OntologyFullContext,
  OntologySSEEvent,
} from '@/api/types';
import './OntologyExplorer.css';

const EXAMPLE_QUESTIONS = [
  '客户说价格太高，如何应对？',
  '初次拜访如何快速建立信任？',
  '客户拖延决策，怎么推进成交？',
  '如何运用社会认同增强说服力？',
  '遇到冷漠客户，怎么唤起兴趣？',
  '丢单后如何维护客户关系？',
];

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  related?: OntologyEntityNode[];
  isError?: boolean;
}

interface LiveStep {
  step: number;
  message: string;
  status: 'processing' | 'success' | 'error';
}

const ENTITY_TYPE_LABELS: Record<string, string> = {
  principle: '原则', concept: '概念', term: '术语', scenario: '场景',
  case: '案例', experiment: '实验', tactic: '策略', script: '话术',
  action_step: '行动步骤', reasoning_chain: '推理链', person: '人物',
  product: '产品', fact: '事实',
};

function typeLabel(t?: string): string {
  if (!t) return '实体';
  return ENTITY_TYPE_LABELS[t] || t;
}

function entityName(e: OntologyEntityNode): string {
  return String(e.name || e.id || '未命名实体');
}

function entityDesc(e: OntologyEntityNode): string {
  const d = e.description ?? e.summary ?? e.definition ?? e.value;
  return d ? String(d) : '';
}

/** 把答案 summary + sections 组合成 markdown 字符串。 */
function answerToMarkdown(answer: { summary: string; sections: Array<{ title: string; content: string }> }): string {
  let md = answer.summary || '';
  if (answer.sections?.length) {
    md += '\n\n' + answer.sections.map((s) => `**${s.title}**\n\n${s.content || ''}`).join('\n\n');
  }
  return md.trim();
}

export default function OntologyExplorerPage() {
  const { agentId } = useParams<{ agentId: string }>();

  const statusQuery = useQuery({
    queryKey: queryKeys.ontologyStatus(agentId!),
    queryFn: () => getOntologyStatus(agentId!),
    enabled: !!agentId,
    retry: false,
  });
  const engineReady =
    statusQuery.data?.knowledge_engine === 'ontology_neo4j' && !!statusQuery.data?.neo4j_ready;

  const statsQuery = useQuery({
    queryKey: queryKeys.ontologyStats(agentId!),
    queryFn: () => getOntologyStats(agentId!),
    enabled: !!agentId && engineReady,
    retry: false,
  });

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [searchProcess, setSearchProcess] = useState<OntologySearchProcess | null>(null);
  const [fullContext, setFullContext] = useState<OntologyFullContext | null>(null);
  const [steps, setSteps] = useState<LiveStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState('');

  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming]);

  // 卸载时中断未完成的流
  useEffect(() => () => abortRef.current?.abort(), []);

  const upsertStep = useCallback((s: LiveStep) => {
    setSteps((prev) => {
      const idx = prev.findIndex((x) => x.step === s.step);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = s;
        return next;
      }
      return [...prev, s];
    });
  }, []);

  const sendMessage = useCallback(
    async (raw: string) => {
      const query = raw.trim();
      if (!query || !agentId || streaming) return;

      setMessages((prev) => [...prev, { role: 'user', content: query }]);
      setInput('');
      setStreaming(true);
      setSteps([]);
      setSearchProcess(null);
      setFullContext(null);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        const resp = await ontologyQueryStream(agentId, { query });
        if (!resp.ok || !resp.body) {
          const text = await resp.text().catch(() => '');
          throw new Error(`请求失败 (${resp.status}) ${text || ''}`.trim());
        }

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
            if (!line.startsWith('data: ')) continue;
            let evt: OntologySSEEvent;
            try {
              evt = JSON.parse(line.slice(6));
            } catch {
              continue;
            }
            if (evt.type === 'step') {
              upsertStep({ step: evt.step, message: evt.message, status: evt.status });
            } else if (evt.type === 'search_process') {
              setSearchProcess(evt.data);
            } else if (evt.type === 'result') {
              setFullContext(evt.full_context);
              setMessages((prev) => [
                ...prev,
                {
                  role: 'assistant',
                  content: answerToMarkdown(evt.answer),
                  related: evt.full_context.matched_entities?.slice(0, 8) || [],
                },
              ]);
            } else if (evt.type === 'error') {
              setMessages((prev) => [...prev, { role: 'assistant', content: `❌ ${evt.message}`, isError: true }]);
            }
          }
        }
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: `❌ 网络错误: ${(err as Error).message}`, isError: true },
        ]);
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [agentId, streaming, upsertStep],
  );

  const exploreEntity = useCallback(
    (name: string) => {
      sendMessage(`请详细介绍「${name}」，并展示相关的策略和依据`);
    },
    [sendMessage],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const statsTotal = statsQuery.data
    ? Object.values(statsQuery.data.entity_type_counts).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <div className="oe-root">
      {statusQuery.data && !engineReady && (
        <Alert
          type="warning"
          showIcon
          message="Neo4j 知识图谱未就绪"
          description={
            statusQuery.data.neo4j_configured
              ? '已配置但无法连接，请检查 Neo4j 服务状态。'
              : '当前实例未启用 ontology_neo4j 引擎或未配置 Neo4j。探索器需先在「设置」中启用并导入知识。'
          }
        />
      )}

      <div className="oe-grid">
        {/* ---------- 左：检索过程 ---------- */}
        <div className="oe-panel oe-search-panel">
          <div className="oe-panel-head">
            <h2>🔍 本体检索过程</h2>
            <div className="sub">实时显示知识图谱命中</div>
          </div>
          <div className="oe-panel-body oe-search-body">
            {!searchProcess && steps.length === 0 ? (
              <div style={{ color: '#999', textAlign: 'center', padding: 20, fontSize: 11 }}>
                提问后会实时显示检索过程
              </div>
            ) : (
              <>
                {steps.map((s) => (
                  <div key={s.step} className={`oe-step ${s.status}`}>
                    <div className="oe-step-title">
                      {s.step}. {s.message}
                    </div>
                  </div>
                ))}

                {searchProcess && (
                  <>
                    <div className="oe-step success">
                      <div className="oe-step-title">🎯 检索策略</div>
                      <div className="oe-step-detail">
                        {searchProcess.strategy}
                        {searchProcess.vector_fallback_used ? '（向量兜底）' : ''}
                        {' · 置信度 '}
                        {(searchProcess.confidence ?? 0).toFixed(2)}
                      </div>
                      {searchProcess.timings_ms?.ontology_retrieval != null && (
                        <div className="oe-step-detail">
                          耗时 {searchProcess.timings_ms.ontology_retrieval} ms
                        </div>
                      )}
                    </div>

                    <div className="oe-step success">
                      <div className="oe-step-title">🎯 核心实体（{searchProcess.center_entities.length}）</div>
                      {searchProcess.center_entities.map((e, i) => (
                        <div key={i} className="oe-ent-match">
                          <span className="oe-ent-match-name">
                            [{typeLabel(e.type)}] {entityName(e)}
                          </span>
                        </div>
                      ))}
                      {searchProcess.center_entities.length === 0 && (
                        <div className="oe-step-detail">无精确命中</div>
                      )}
                    </div>

                    <div className="oe-step success">
                      <div className="oe-step-title">📊 命中实体（{searchProcess.matched_entities.length}）</div>
                      {searchProcess.matched_entities.slice(0, 12).map((e, i) => (
                        <div key={i} className="oe-ent-match">
                          <span className="oe-ent-match-name">
                            [{typeLabel(e.type)}] {entityName(e)}
                          </span>
                        </div>
                      ))}
                      {searchProcess.matched_entities.length === 0 && (
                        <div className="oe-step-detail">未匹配到实体</div>
                      )}
                    </div>

                    {searchProcess.facts_used.length > 0 && (
                      <div className="oe-step success">
                        <div className="oe-step-title">🔗 使用事实（{searchProcess.facts_used.length}）</div>
                        {searchProcess.facts_used.slice(0, 8).map((f, i) => (
                          <div key={i} className="oe-ent-match">
                            {f.predicate ? `${f.subject_name || ''} · ${f.predicate}` : entityName(f)}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {/* ---------- 中：问答 ---------- */}
        <div className="oe-panel oe-chat-panel">
          <div className="oe-chat-head">
            <h1>🎯 本体探索问答</h1>
            <div className="oe-badge">
              {statsQuery.isLoading
                ? '加载中…'
                : statsQuery.isError
                  ? '统计加载失败'
                  : `${statsTotal} 个实体`}
            </div>
          </div>

          <div className="oe-chat-body">
            {messages.length === 0 ? (
              <div className="oe-welcome">
                <h2>👋 欢迎使用本体探索器</h2>
                <p>基于 Neo4j 知识图谱 + 大模型，可实时查看检索过程与完整上下文。</p>
                <p>试试这些问题：</p>
                <div className="oe-examples">
                  {EXAMPLE_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      className="oe-example"
                      onClick={() => sendMessage(q)}
                      disabled={!engineReady || streaming}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m, i) => (
                <div key={i} className={`oe-msg ${m.role}`}>
                  <div className="oe-avatar">{m.role === 'user' ? '你' : 'AI'}</div>
                  <div className="oe-msg-content" style={m.isError ? { borderColor: '#f56565', color: '#c53030' } : undefined}>
                    {m.role === 'assistant' ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                    ) : (
                      m.content
                    )}
                    {m.related && m.related.length > 0 && (
                      <div className="oe-related">
                        <div className="oe-related-title">🔗 相关知识（点击可深入探索）</div>
                        <div className="oe-related-list">
                          {m.related.map((e, j) => (
                            <div
                              key={j}
                              className="oe-related-item"
                              onClick={() => exploreEntity(entityName(e))}
                            >
                              <div className="oe-type-badge">{typeLabel(e.type)}</div>
                              <div className="oe-ent-name">{entityName(e)}</div>
                              <div className="oe-arrow">→</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
            {streaming && (
              <div className="oe-msg assistant">
                <div className="oe-avatar">AI</div>
                <div className="oe-msg-content">
                  <div className="oe-loading" />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="oe-input-bar">
            <textarea
              value={input}
              placeholder={engineReady ? '输入你的问题…（Enter 发送，Shift+Enter 换行）' : 'Neo4j 未就绪，暂不可查询'}
              disabled={!engineReady || streaming}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              className="oe-send-btn"
              disabled={!engineReady || streaming || !input.trim()}
              onClick={() => sendMessage(input)}
            >
              {streaming ? <Spin size="small" /> : '发送'}
            </button>
          </div>
        </div>

        {/* ---------- 右：完整上下文 ---------- */}
        <div className="oe-panel oe-context-panel">
          <div className="oe-panel-head">
            <h2>📋 完整上下文</h2>
            <div className="sub">输入给大模型的所有内容</div>
          </div>
          <div className="oe-panel-body oe-context-body">
            {!fullContext ? (
              <div className="oe-ctx-empty">提问后这里会显示完整上下文</div>
            ) : (
              <ContextView ctx={fullContext} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 右栏：渲染完整上下文（system prompt + 图谱证据）。 */
function ContextView({ ctx }: { ctx: OntologyFullContext }) {
  const hopGroups: Array<{ key: string; label: string; items: OntologyEntityNode[] }> = [
    { key: 'center', label: '🎯 核心实体', items: ctx.center_entities },
    { key: 'matched', label: '📊 命中实体', items: ctx.matched_entities },
    { key: 'facts', label: '🔗 使用事实', items: ctx.facts_used },
    { key: 'evidence', label: '📑 证据摘录', items: ctx.evidence },
    { key: 'sources', label: '📁 来源文档', items: ctx.source_documents },
  ];

  return (
    <>
      {ctx.system_prompt && (
        <div className="oe-ctx-section">
          <div className="oe-ctx-title">🤖 System Prompt</div>
          <div className="oe-ctx-item oe-ctx-system-prompt">{ctx.system_prompt}</div>
        </div>
      )}
      {ctx.user_query && (
        <div className="oe-ctx-section">
          <div className="oe-ctx-title">❓ 你的问题</div>
          <div className="oe-ctx-item oe-ctx-user-q">{ctx.user_query}</div>
        </div>
      )}
      {hopGroups.map(
        (g) =>
          g.items.length > 0 && (
            <div key={g.key} className="oe-ctx-section">
              <div className="oe-ctx-title">{g.label}（{g.items.length}）</div>
              {g.items.map((e, i) => (
                <div key={i} className="oe-ctx-item">
                  {e.type && <div className="oe-ctx-type">{typeLabel(e.type)}</div>}
                  <div className="oe-ctx-name">{entityName(e)}</div>
                  {entityDesc(e) && <div className="oe-ctx-desc">{entityDesc(e)}</div>}
                </div>
              ))}
            </div>
          ),
      )}
    </>
  );
}
