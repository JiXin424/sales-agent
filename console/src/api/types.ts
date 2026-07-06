// --- Graph Debug ---

/** 单个图节点的结构化元数据（取自后端 node_metadata 集中表）。 */
export interface NodeInfo {
  id: string;
  name: string;
  type: 'function' | 'subgraph';
  calls_llm: boolean;
  desc: string;
  prompts: { name: string; source: string; note?: string }[];
}

/** 图的一条边（取自后端 compiled.get_graph().edges）。 */
export interface EdgeInfo {
  source: string;
  target: string;
}

/** 节点 ↔ prompt 对应关系的一行。无 prompt 的纯函数节点 prompt_name 为「—」。 */
export interface PromptMapping {
  node: string;
  calls_llm: boolean;
  prompt_name: string;
  prompt_source: string;
  note: string;
}

export interface GraphInfo {
  id: string;
  name: string;
  mermaid: string;
  node_count: number;
  edge_count: number;
  nodes: NodeInfo[];
  edges: EdgeInfo[];
  prompt_map: PromptMapping[];
}

export interface GraphListResponse {
  graphs: GraphInfo[];
}

export interface GraphDebugNodeStart {
  node: string;
  input?: unknown;
}

export interface GraphDebugNodeOutput {
  node: string;
  output: Record<string, unknown>;
}

export interface GraphDebugNodeEnd {
  node: string;
  duration_ms: number;
  result?: unknown;
}

export interface GraphDebugCustom {
  data: Record<string, unknown>;
}

export interface GraphDebugDone {
  total_duration_ms: number;
  graph_id: string;
}

export interface GraphDebugError {
  message: string;
}

/** /run SSE `started` event — carries the checkpointer thread_id. */
export interface GraphDebugStarted {
  thread_id?: string;
  [key: string]: unknown;
}

// --- Graph Debug checkpoint time-travel (read-only) ---

/** Single checkpoint metadata entry on the history timeline.

`parent_checkpoint_id` is the precise parent lineage from
`snapshot.parent_config["configurable"]["checkpoint_id"]` — null for the
root checkpoint, and for A2 forks it points at the checkpoint the user edited.

`source` is optional: the backend may include `"update"` for checkpoints
created by `aupdate_state` (fork origin). If absent, the DAG infers fork
points structurally from the parent→child graph (a node with >1 child). */
export interface CheckpointMeta {
  checkpoint_id: string;
  step: number | null;
  node: string | null;
  ts: string | null;
  next: string[] | null;
  parent_checkpoint_id: string | null;
  /** Optional backend hint for fork detection ('update' = created by update_state). */
  source?: string | null;
}

export interface CheckpointListResponse {
  checkpoints: CheckpointMeta[];
}

export interface CheckpointStateResponse {
  values: Record<string, unknown>;
  next: string[] | null;
}

/** Body for POST .../checkpoints/{cid}/state — update state values for a fork. */
export interface UpdateStateRequest {
  values: Record<string, unknown>;
  graph_id?: string;
}

/** Response from POST .../checkpoints/{cid}/state — the new checkpoint_id created by the fork. */
export interface UpdateStateResponse {
  checkpoint_id: string;
}

/** Body for POST .../checkpoints/{cid}/replay — re-run the tail from a checkpoint. */
export interface ReplayRequest {
  graph_id?: string;
}

/** Persisted recent debug run record (localStorage["graph-debug:runs"]). */
export interface RecentDebugRun {
  thread_id: string;
  graph_id: string;
  message: string;
  ts: string; // ISO timestamp
}


/** TypeScript interfaces mirroring backend Pydantic schemas. */

// --- Generic ---

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// --- Health ---

export interface HealthStatus {
  status: string;
}

export interface ReadyStatus {
  status: 'ready' | 'not_ready';
  errors: string[];
  tenant_id?: string;
  deployment_mode?: string;
}

export interface DiagnosticsResult {
  status: 'ok' | 'degraded';
  tenant_id?: string;
  chat?: Record<string, unknown>;
  embedding?: Record<string, unknown>;
  debug?: Record<string, unknown>;
}

export interface LatencyStatsGlobal {
  status: string;
  stats: {
    total_requests: number;
    avg_latency_ms: number;
    p50_latency_ms: number;
    p90_latency_ms: number;
    p95_latency_ms: number;
    error_rate: number;
    by_path: Record<string, { count: number; avg_latency_ms: number; p50: number; p90: number; p95: number }>;
  };
}

// --- Tenant ---

export interface TenantConfig {
  tone?: string;
  forbid_words?: string[];
  default_script_versions?: string[];
  knowledge_base?: { standard_format: string; namespace: string };
  model?: {
    provider: string;
    api_key_env: string;
    base_url: string;
    chat_model: string;
    embedding_model: string;
    temperature: number;
    timeout_seconds: number;
    max_retries: number;
  };
  risk_policy?: Record<string, string>;
}

export interface TenantResponse {
  tenant_id: string;
  name: string;
  status: string;
  config: TenantConfig;
  created_at: string;
  updated_at: string;
}

export interface CreateTenantRequest {
  tenant_id: string;
  name: string;
  config?: TenantConfig;
}

// --- Conversations ---

export interface ConversationItem {
  id: string;
  tenant_id: string;
  user_id: string;
  channel: string;
  message: string;
  task_type: string | null;
  task_confidence: number | null;
  status: string;
  risk: Record<string, unknown> | null;
  sources: Record<string, unknown>[] | null;
  created_at: string;
  updated_at: string;
}

export interface AnswerSection {
  title: string;
  content: string;
}

export interface ConversationDetail extends ConversationItem {
  answer: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  messages: ConversationMessage[];
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

// --- Run Traces ---

export interface AgentRunDetail {
  id: string;
  tenant_id: string;
  conversation_id: string;
  user_id: string;
  task_type: string | null;
  path: string | null;
  status: string;
  total_latency_ms: number | null;
  route_confidence: number | null;
  error: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface AgentRunStep {
  id: string;
  step_name: string;
  step_order: number;
  status: string;
  latency_ms: number | null;
  metadata: Record<string, unknown>;
  error_summary: string | null;
  created_at: string;
}

export interface RunStepsResponse {
  run_id: string;
  steps: AgentRunStep[];
}

// --- Documents ---

export interface DocumentItem {
  id: string;
  tenant_id: string;
  title: string;
  source_path: string;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SourceFileItem {
  id: string;
  tenant_id: string;
  original_filename: string;
  stored_path: string;
  content_hash: string;
  mime_type: string;
  status: string;
  created_at: string;
  updated_at: string;
}

// --- Feedback ---

export type ReviewStatus = 'open' | 'reviewed' | 'ignored';

export interface FeedbackItem {
  id: string;
  tenant_id: string;
  conversation_id: string;
  user_id: string;
  rating: 'up' | 'down';
  feedback_text: string;
  labels: string[];
  review_status: ReviewStatus;
  created_at: string;
}

export interface FeedbackSummary {
  up: number;
  down: number;
  total: number;
}

/** 会话消息总数（按 role 分）。与 conversation 线程数区分。 */
export interface MessageCount {
  total: number;
  user: number;
  assistant: number;
  system: number;
}

// --- Prompts ---

export type PromptCategory = 'task' | 'system' | 'router' | 'risk' | 'coach';

export interface PromptVersion {
  id: string;
  tenant_id: string;
  task_type: string | null;
  prompt_category: PromptCategory;
  prompt_key: string | null;
  required_placeholders: string[];
  version: string;
  status: 'draft' | 'active' | 'archived';
  template_text: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface BuiltinPrompt {
  prompt_category: PromptCategory;
  prompt_key: string;
  template: string;
  required_placeholders: string[];
  description: string;
}

export interface EffectivePrompt {
  prompt_category: PromptCategory;
  prompt_key: string;
  template: string;
  required_placeholders: string[];
  description: string;
  source: 'db_active' | 'builtin';
  version_id: string | null;
  version: string;
}

export interface PromptPreviewRequest {
  prompt_category?: PromptCategory;
  prompt_key?: string | null;
  task_type?: string | null;
  version_id?: string | null;
  sample_message?: string;
  sample_context?: Record<string, unknown> | null;
  sample_variables?: Record<string, string> | null;
  run_generation?: boolean;
}

export interface PromptPreviewResponse {
  rendered_prompt: string;
  model_output: string | null;
  version_id: string | null;
  prompt_category?: PromptCategory;
  prompt_key?: string | null;
  task_type?: string | null;
}

// --- Knowledge / Ingestion ---

export interface UploadResponse {
  job_id: string;
  source_file_id: string;
  status: string;
}

export interface IngestionJobItem {
  id: string;
  tenant_id: string;
  source_file_id: string | null;
  document_id: string | null;
  status: string;
  documents_seen: number;
  documents_ingested: number;
  chunks_created: number;
  warnings: string[];
  errors: Record<string, unknown>[];
  error_summary: string | null;
  created_at: string;
  updated_at: string;
}

// --- Ontology (Neo4j knowledge engine) ---

export interface OntologyStatus {
  knowledge_engine: string;
  ontology_status: 'not_configured' | 'ready' | 'degraded' | 'failed';
  neo4j_configured: boolean;
  neo4j_ready: boolean;
  visual_url: string;
}

export interface OntologyJob {
  id: string;
  tenant_id: string;
  agent_id: string | null;
  engine: string;
  status: string;
  stage: string;
  documents_seen: number;
  documents_ingested: number;
  entities_created: number;
  entities_merged: number;
  facts_created: number;
  facts_active: number;
  facts_pending_review: number;
  facts_rejected: number;
  conflicts_created: number;
  warnings: string[];
  errors: Record<string, unknown>[];
  error_summary: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface IngestStartResponse {
  job_id: string;
  filename: string;
}

export interface JobProgressEvent {
  stage: string;
  status: 'running' | 'completed' | 'completed_with_errors' | 'failed';
  stats?: Record<string, number>;
  error_summary?: string;
}

// --- Ontology Explorer（本体探索器：查询/调试） ---

/** 实体节点（图谱证据里的 entity/fact/evidence/document 通用结构）。 */
export interface OntologyEntityNode {
  id?: string;
  name?: string;
  type?: string;
  [key: string]: unknown;
}

/** 镜像后端 GraphEvidence.to_dict()。 */
export interface GraphEvidenceLike {
  ontology_intent?: string;
  center_entities: OntologyEntityNode[];
  matched_entities: OntologyEntityNode[];
  facts_used: OntologyEntityNode[];
  evidence: OntologyEntityNode[];
  source_documents: OntologyEntityNode[];
  retrieval_strategy: 'graph' | 'graph_vector_fallback' | string;
  vector_fallback_used: boolean;
  confidence: number;
  timings_ms: Record<string, number>;
}

export interface OntologyStats {
  stats: { total_entities: number };
  entity_type_counts: Record<string, number>;
}

export interface OntologyQueryRequest {
  query: string;
  history?: Array<{ role: string; content: string }>;
}

/** 左栏「检索过程」摘要。 */
export interface OntologySearchProcess {
  query: string;
  strategy: string;
  vector_fallback_used: boolean;
  confidence: number;
  matched_entities: OntologyEntityNode[];
  center_entities: OntologyEntityNode[];
  facts_used: OntologyEntityNode[];
  timings_ms: Record<string, number>;
}

/** 右栏「完整上下文」：渲染后的 system prompt + 用户问题 + GraphEvidence 字段。 */
export interface OntologyFullContext {
  system_prompt: string;
  user_query: string;
  ontology_intent?: string;
  center_entities: OntologyEntityNode[];
  matched_entities: OntologyEntityNode[];
  facts_used: OntologyEntityNode[];
  evidence: OntologyEntityNode[];
  source_documents: OntologyEntityNode[];
  retrieval_strategy: string;
  vector_fallback_used: boolean;
  confidence: number;
  timings_ms: Record<string, number>;
}

/** AI 答案（summary + sections）。 */
export interface OntologyAnswerPayload {
  summary: string;
  sections: Array<{ title: string; content: string }>;
}

export interface OntologyQueryResponse {
  query: string;
  answer: OntologyAnswerPayload;
  sources: Array<Record<string, unknown>>;
  search_process: OntologySearchProcess;
  full_context: OntologyFullContext;
}

/** /query/stream 的 SSE 事件（判别联合）。 */
export type OntologySSEEvent =
  | { type: 'step'; step: number; message: string; status: 'processing' | 'success' | 'error' }
  | { type: 'search_process'; data: OntologySearchProcess }
  | { type: 'result'; answer: OntologyAnswerPayload; full_context: OntologyFullContext }
  | { type: 'error'; message: string };



export interface ModelCallItem {
  id: string;
  tenant_id: string;
  provider: string;
  base_url_host: string;
  chat_model: string;
  embedding_model: string;
  request_type: string;
  status: string;
  latency_ms: number;
  error_code: string | null;
  created_at: string;
}

export interface ModelCallSummary {
  [provider: string]: {
    [status: string]: { count: number; avg_latency_ms: number | null };
  };
}

// --- Workflow Metrics ---

export interface WorkflowMetrics {
  task_type_distribution: Record<string, number>;
  stage_distribution: Record<string, number>;
  low_score_conversations: { conversation_id: string; overall_score: number }[];
  feedback_by_workflow_task: Record<string, Record<string, number>>;
  common_missing_fields: { field: string; count: number }[];
}

// --- Tenant-scoped Latency Stats ---

export interface TenantLatencyStats {
  stats: Record<string, unknown>;
}

// --- Filter types ---

export interface ConversationFilters {
  user_id?: string;
  status?: string;
  task_type?: string;
  limit?: number;
  offset?: number;
}

export interface FeedbackFilters {
  rating?: 'up' | 'down';
  review_status?: ReviewStatus;
  limit?: number;
  offset?: number;
}

export interface PromptFilters {
  prompt_category?: PromptCategory;
  task_type?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface IngestionJobFilters {
  status?: string;
  limit?: number;
  offset?: number;
}

// --- Agent Instance ---

export type AgentStatus = 'draft' | 'active' | 'paused' | 'archived';
export type KnowledgeScopeMode = 'tenant_all' | 'document_subset' | 'source_file_subset';

export interface AgentInstance {
  id: string;
  tenant_id: string;
  name: string;
  agent_type: string;
  description: string;
  status: AgentStatus;
  source_agent_id: string | null;
  model_config_ref: string;
  prompt_set_id: string | null;
  knowledge_scope_id: string | null;
  risk_policy_id: string | null;
  eval_suite_id: string | null;
  feature_flags: Record<string, unknown>;
  is_tenant_default: boolean;
  created_by: string | null;
  activated_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentCreateRequest {
  tenant_id: string;
  name: string;
  agent_type?: string;
  description?: string;
  model_config_ref?: string;
  knowledge_scope_mode?: KnowledgeScopeMode;
  document_ids?: string[];
  source_file_ids?: string[];
  feature_flags?: Record<string, unknown>;
  created_by?: string;
}

export interface CloneOptions {
  name: string;
  tenant_id?: string;
  prompt_set: 'copy' | 'reference';
  risk_policy: 'copy' | 'reference';
  knowledge_scope: 'reference' | 'copy_subset' | 'empty';
  eval_suite: 'copy' | 'reference' | 'empty';
  channel_config: 'shell_only' | 'skip';
  model_config_choice: 'reference' | 'new';
  description?: string;
  created_by?: string;
}

export interface CloneManifest {
  id: string;
  source_agent_id: string;
  target_agent_id: string;
  tenant_id: string;
  options: Record<string, unknown>;
  copied_resources: Record<string, unknown>;
  referenced_resources: Record<string, unknown>;
  reset_resources: Record<string, unknown>;
  skipped_resources: Record<string, unknown>;
  created_at: string;
}

export interface CloneResult {
  agent: AgentInstance;
  manifest: CloneManifest;
}

export interface ReadinessCheck {
  check: string;
  label: string;
  passed: boolean;
  required: boolean;
  reason: string;
}

export interface ReadinessReport {
  agent_id: string;
  ready: boolean;
  checks: ReadinessCheck[];
  blockers: string[];
  waivers: string[];
}

// --- Phase D: Pilot Validation ---

// R2: Pilot Metrics
export interface PilotMetrics {
  usage: {
    total_conversations: number;
    unique_users: number;
    avg_dau: number;
    questions_per_user: number;
    repeat_users: number;
    repeat_usage_rate: number;
  };
  distribution: {
    task_distribution: Record<string, number>;
    stage_distribution: Record<string, number>;
  };
  quality: {
    feedback_up: number;
    feedback_down: number;
    feedback_positive_ratio: number;
    rag_usage_rate: number;
    risk_interception_count: number;
  };
  performance: {
    latency_p50_ms: number;
    latency_p95_ms: number;
    total_runs: number;
    failed_runs: number;
    error_rate: number;
  };
  period: { start_date: string; end_date: string };
}

// R3: Review Queue
export type ReviewReason = 'negative_feedback' | 'high_risk' | 'low_score' | 'model_error' | 'retrieval_miss' | 'manual_flag';
export type ReviewPriority = 'low' | 'medium' | 'high' | 'critical';
export type ReviewItemStatus = 'open' | 'in_progress' | 'resolved' | 'ignored';

export interface ReviewItem {
  id: string;
  tenant_id: string;
  conversation_id: string;
  feedback_id: string | null;
  agent_run_id: string | null;
  reason: ReviewReason;
  priority: ReviewPriority;
  status: ReviewItemStatus;
  assignee: string | null;
  notes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReviewItemFilters {
  status?: ReviewItemStatus;
  reason?: ReviewReason;
  priority?: ReviewPriority;
  assignee?: string;
  limit?: number;
  offset?: number;
}

// R4: Feedback Classification
export type FeedbackCategory =
  | 'wrong_answer' | 'missing_knowledge' | 'bad_retrieval'
  | 'bad_prompt' | 'wrong_task_route' | 'unsafe_answer'
  | 'too_generic' | 'too_slow' | 'format_problem';

export interface FeedbackCategorySummary {
  [category: string]: number;
}

// R5: Knowledge Gaps
export type GapStatus = 'open' | 'document_needed' | 'uploaded' | 'verified' | 'ignored';

export interface KnowledgeGap {
  id: string;
  tenant_id: string;
  source_conversation_id: string | null;
  source_feedback_id: string | null;
  linked_document_id: string | null;
  title: string;
  description: string | null;
  status: GapStatus;
  priority: string;
  keywords: string[];
  created_at: string;
  updated_at: string;
}

export interface KnowledgeGapSummary {
  open: number;
  document_needed: number;
  uploaded: number;
  verified: number;
  ignored: number;
  total: number;
}

// R6: Eval Suite / Runs
export interface EvalSuite {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  fixture_path: string | null;
  case_count: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface EvalRun {
  id: string;
  tenant_id: string;
  eval_suite_id: string;
  status: string;
  total_cases: number;
  passed: number;
  failed: number;
  skipped: number;
  prompt_version_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvalRunResult {
  id: string;
  eval_case_id: string;
  conversation_id: string | null;
  passed: boolean;
  actual_task_type: string | null;
  actual_risk_level: string | null;
  route_match: boolean;
  content_checks: Record<string, unknown>;
  failure_reasons: string[];
  latency_ms: number | null;
  created_at: string;
}

// R7: Comparison
export interface ComparisonResult {
  before_run_id: string;
  after_run_id: string;
  before_pass_rate: number;
  after_pass_rate: number;
  changed_pass_rate: number;
  regression_count: number;
  improvement_count: number;
  new_failures: { case_id: string; failure_reasons: string[] }[];
  new_passes: { case_id: string }[];
}

export interface ReviewOutcomeComparison {
  before_date: string;
  after_date: string;
  reason_comparison: Record<string, { before: number; after: number; change: number }>;
  total_before: number;
  total_after: number;
}

// R8: Alerts
export type AlertMetric = 'model_failures' | 'dingtalk_failures' | 'ingestion_failures' | 'slow_p95' | 'high_error_rate' | 'high_negative_feedback' | 'retrieval_misses';
export type AlertSeverity = 'info' | 'warning' | 'critical';
export type AlertStatus = 'active' | 'acknowledged' | 'resolved';

export interface AlertRule {
  id: string;
  tenant_id: string;
  name: string;
  metric: AlertMetric;
  condition: string;
  threshold: number;
  window_minutes: number;
  severity: AlertSeverity;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertItem {
  id: string;
  tenant_id: string;
  alert_rule_id: string;
  severity: AlertSeverity;
  metric: string;
  threshold_value: number;
  observed_value: number;
  status: AlertStatus;
  first_seen_at: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

// R9: Pilot Reports
export type ReportType = 'weekly' | 'monthly' | 'custom';

export interface PilotReport {
  id: string;
  tenant_id: string;
  report_type: ReportType;
  start_date: string;
  end_date: string;
  status: string;
  summary: Record<string, unknown>;
  report: Record<string, unknown>;
  markdown_content: string | null;
  created_at: string;
  updated_at: string;
}

// R10: Pilot Status
export type PilotClassification = 'expand' | 'continue_pilot' | 'needs_remediation' | 'stop';

export interface PilotStatus {
  tenant_id: string;
  classification: PilotClassification;
  reasons: string[];
  next_actions: string[];
  metrics: {
    total_conversations: number;
    unique_users: number;
    repeat_users: number;
    repeat_usage_rate: number;
    feedback_positive_ratio: number;
    total_feedback: number;
    open_reviews: number;
    unresolved_gaps: number;
    active_critical_alerts: number;
    recent_error_rate: number;
  };
}

// --- Instance Config ---

export interface SensitiveValue {
  value: string;     // full plaintext (revealed on click)
  sensitive: true;
  masked: string;    // truncated preview (e.g. "sk-2e2a...2242a")
}

export type ConfigValue = string | SensitiveValue;

export type InstanceConfigGroup = Record<string, ConfigValue>;

export interface InstanceConfigResponse {
  groups: Record<string, InstanceConfigGroup>;
}
