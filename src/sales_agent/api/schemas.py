"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --- 通用 ---


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: str = ""


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- 租户 ---


class TenantModelConfig(BaseModel):
    provider: str = "openai_compatible"
    api_key_env: str = "SALES_AGENT_API_KEY"
    base_url: str = "https://api.example.com/v1"
    chat_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    temperature: float = 0.3
    timeout_seconds: int = 30
    max_retries: int = 2


class TenantStyleConfig(BaseModel):
    tone: str = "professional"
    forbid_words: list[str] = Field(default_factory=list)
    default_script_versions: list[str] = Field(
        default_factory=lambda: ["温和版", "推进版", "简短版"]
    )


class TenantRiskPolicy(BaseModel):
    price_commitment: str = "warn"
    delivery_commitment: str = "block"
    discount_commitment: str = "warn"
    contract_commitment: str = "block"
    unsupported_claim: str = "rewrite"
    competitor_attack: str = "rewrite"


class TenantKnowledgeBaseConfig(BaseModel):
    standard_format: str = "markdown"
    namespace: str = ""


class TenantConfig(BaseModel):
    tone: str = "professional"
    forbid_words: list[str] = Field(default_factory=list)
    default_script_versions: list[str] = Field(
        default_factory=lambda: ["温和版", "推进版", "简短版"]
    )
    knowledge_base: TenantKnowledgeBaseConfig = Field(default_factory=TenantKnowledgeBaseConfig)
    model: TenantModelConfig = Field(default_factory=TenantModelConfig)
    risk_policy: TenantRiskPolicy = Field(default_factory=TenantRiskPolicy)


class CreateTenantRequest(BaseModel):
    tenant_id: str
    name: str
    config: TenantConfig = Field(default_factory=TenantConfig)


class TenantResponse(BaseModel):
    tenant_id: str
    name: str
    status: str
    config: dict[str, Any]
    created_at: str
    updated_at: str


# --- 知识导入 ---


class IngestRequest(BaseModel):
    path: str
    mode: str = "sync"
    standard_format: str = "markdown"
    rebuild_index: bool = False


class IngestResponse(BaseModel):
    tenant_id: str
    status: str
    documents_seen: int
    documents_ingested: int
    chunks_created: int
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


# --- Chat ---


class ConversationOptions(BaseModel):
    use_history: bool = True
    reset_context: bool = False


class RequestContext(BaseModel):
    industry: str | None = None
    product: str | None = None
    tone: str | None = None
    stage: str | None = None


class ChatRequest(BaseModel):
    tenant_id: str
    user_id: str
    message: str
    channel: str = "local"
    conversation_id: str | None = None
    agent_id: str | None = None  # Agent 作用域；None 时回退到 tenant 默认 Agent
    conversation_options: ConversationOptions = Field(default_factory=ConversationOptions)
    context: RequestContext = Field(default_factory=RequestContext)


class AnswerSection(BaseModel):
    title: str
    content: str


class SourceItem(BaseModel):
    document_id: str
    title: str
    section_title: str = ""
    chunk_id: str = ""
    source_type: str = ""
    display_title: str = ""
    snippet_ref: str = ""
    snippet_url: str | None = None
    score: float = 0.0


class RiskResult(BaseModel):
    level: str = "none"
    flags: list[str] = Field(default_factory=list)
    action: str = "allow"
    notice: str = ""
    rewrite_summary: str = ""


class DebugInfo(BaseModel):
    retrieval_query: str = ""
    route_confidence: float = 0.0
    prompt_version: str = "v0"
    run_id: str | None = None
    model: str = ""
    latency_ms: int = 0
    # 脱敏的模型元信息（spec 7.4 debug 隔离）
    provider: str = ""
    base_url_host: str = ""
    api_key_ref: str = ""
    api_key_fingerprint: str = ""
    # 延迟优化新增字段
    path: str = ""
    path_reason: str = ""
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    llm_calls: dict[str, bool] = Field(default_factory=dict)
    retrieval_info: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: str
    tenant_id: str
    task_type: str
    answer: dict[str, Any]
    sources: list[SourceItem] = Field(default_factory=list)
    risk: RiskResult = Field(default_factory=RiskResult)
    debug: DebugInfo = Field(default_factory=DebugInfo)


# --- Feedback ---


class FeedbackRequest(BaseModel):
    conversation_id: str
    tenant_id: str | None = None
    user_id: str | None = None
    rating: str  # "up" / "down"
    feedback_text: str = ""


class FeedbackResponse(BaseModel):
    status: str = "ok"
    conversation_id: str


# --- Prompt 管理 ---


class PromptVersionCreate(BaseModel):
    task_type: str
    template_text: str
    description: str = ""
    version: str = ""


class PromptVersionUpdate(BaseModel):
    template_text: str | None = None
    description: str | None = None


class PromptPreviewRequest(BaseModel):
    task_type: str
    version_id: str | None = None  # None = use current active
    sample_message: str
    sample_context: dict[str, Any] | None = None
    run_generation: bool = False


class PromptVersionResponse(BaseModel):
    id: str
    tenant_id: str
    task_type: str
    version: str
    status: str
    template_text: str
    description: str
    created_at: str
    updated_at: str


class PromptVersionListResponse(BaseModel):
    items: list[PromptVersionResponse]
    total: int
    limit: int
    offset: int


class PromptPreviewResponse(BaseModel):
    rendered_prompt: str
    model_output: str | None = None
    version_id: str | None = None
    task_type: str


# --- 知识上传 & 导入任务 ---


class UploadResponse(BaseModel):
    job_id: str
    source_file_id: str
    status: str


class IngestionJobResponse(BaseModel):
    id: str
    tenant_id: str
    source_file_id: str | None = None
    document_id: str | None = None
    status: str
    documents_seen: int = 0
    documents_ingested: int = 0
    chunks_created: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    error_summary: str | None = None
    created_at: str = ""
    updated_at: str = ""


class IngestionJobListResponse(BaseModel):
    items: list[IngestionJobResponse]
    total: int
    limit: int
    offset: int


# --- 反馈详情 ---


class FeedbackDetailResponse(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str
    user_id: str
    rating: str
    feedback_text: str = ""
    labels: list[str] = Field(default_factory=list)
    review_status: str = "open"
    created_at: str = ""


class FeedbackListResponse(BaseModel):
    items: list[FeedbackDetailResponse]
    total: int
    limit: int
    offset: int


class ReviewStatusUpdate(BaseModel):
    review_status: str  # "open" | "reviewed" | "ignored"


# --- Admin 通用分页 ---


class AdminListParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# --- Phase D: Pilot Validation ---


class PilotMetricsResponse(BaseModel):
    usage: dict[str, Any] = Field(default_factory=dict)
    distribution: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    performance: dict[str, Any] = Field(default_factory=dict)
    period: dict[str, Any] = Field(default_factory=dict)


class ReviewItemCreate(BaseModel):
    conversation_id: str
    reason: str = "manual_flag"
    priority: str = "medium"
    notes: dict[str, Any] = Field(default_factory=dict)
    assignee: str | None = None


class ReviewItemResponse(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str
    feedback_id: str | None = None
    agent_run_id: str | None = None
    reason: str
    priority: str
    status: str
    assignee: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ReviewItemListResponse(BaseModel):
    items: list[ReviewItemResponse]
    total: int
    limit: int
    offset: int


class ReviewItemUpdate(BaseModel):
    status: str
    assignee: str | None = None
    notes: dict[str, Any] | None = None


class FeedbackClassifyRequest(BaseModel):
    categories: list[str]


class FeedbackCategorySummaryResponse(BaseModel):
    categories: dict[str, int] = Field(default_factory=dict)


class KnowledgeGapCreate(BaseModel):
    title: str
    description: str | None = None
    source_conversation_id: str | None = None
    source_feedback_id: str | None = None
    priority: str = "medium"
    keywords: list[str] = Field(default_factory=list)


class KnowledgeGapResponse(BaseModel):
    id: str
    tenant_id: str
    source_conversation_id: str | None = None
    source_feedback_id: str | None = None
    linked_document_id: str | None = None
    title: str
    description: str | None = None
    status: str
    priority: str
    keywords: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class KnowledgeGapListResponse(BaseModel):
    items: list[KnowledgeGapResponse]
    total: int
    limit: int
    offset: int


class KnowledgeGapTransition(BaseModel):
    status: str


class KnowledgeGapLinkDocument(BaseModel):
    document_id: str


class EvalSuiteCreate(BaseModel):
    name: str
    fixture_path: str
    description: str | None = None


class EvalSuiteResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None = None
    fixture_path: str | None = None
    case_count: int = 0
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""


class EvalSuiteListResponse(BaseModel):
    items: list[EvalSuiteResponse]
    total: int
    limit: int
    offset: int


class EvalRunTrigger(BaseModel):
    prompt_version_id: str | None = None


class EvalRunResponse(BaseModel):
    id: str
    tenant_id: str
    eval_suite_id: str
    status: str
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    prompt_version_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error_summary: str | None = None
    created_at: str = ""
    updated_at: str = ""


class EvalRunListResponse(BaseModel):
    items: list[EvalRunResponse]
    total: int
    limit: int
    offset: int


class EvalRunResultResponse(BaseModel):
    id: str
    eval_case_id: str
    conversation_id: str | None = None
    passed: bool = False
    actual_task_type: str | None = None
    actual_risk_level: str | None = None
    route_match: bool = True
    content_checks: dict[str, Any] = Field(default_factory=dict)
    failure_reasons: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    created_at: str = ""


class ComparisonRequest(BaseModel):
    before_run_id: str
    after_run_id: str


class ComparisonResponse(BaseModel):
    before_run_id: str
    after_run_id: str
    before_pass_rate: float = 0
    after_pass_rate: float = 0
    changed_pass_rate: float = 0
    regression_count: int = 0
    improvement_count: int = 0
    new_failures: list[dict[str, Any]] = Field(default_factory=list)
    new_passes: list[dict[str, Any]] = Field(default_factory=list)


class AlertRuleCreate(BaseModel):
    name: str
    metric: str
    threshold: float
    condition: str = "gt"
    window_minutes: int = 60
    severity: str = "warning"


class AlertRuleResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    metric: str
    condition: str
    threshold: float
    window_minutes: int = 60
    severity: str = "warning"
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class AlertRuleListResponse(BaseModel):
    items: list[AlertRuleResponse]
    total: int
    limit: int
    offset: int


class AlertResponse(BaseModel):
    id: str
    tenant_id: str
    alert_rule_id: str
    severity: str
    metric: str
    threshold_value: float
    observed_value: float
    status: str
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int
    limit: int
    offset: int


class ReportGenerateRequest(BaseModel):
    start_date: str
    end_date: str
    report_type: str = "weekly"


class ReportResponse(BaseModel):
    id: str
    tenant_id: str
    report_type: str
    start_date: str
    end_date: str
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    markdown_content: str | None = None
    created_at: str = ""
    updated_at: str = ""


class ReportListResponse(BaseModel):
    items: list[ReportResponse]
    total: int
    limit: int
    offset: int


class PilotStatusResponse(BaseModel):
    tenant_id: str
    classification: str
    reasons: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


# --- Agent Instance ---


class AgentCreateRequest(BaseModel):
    tenant_id: str
    name: str
    agent_type: str = "sales_assistant"
    description: str = ""
    model_config_ref: str = "runtime"
    knowledge_scope_mode: str = "tenant_all"  # tenant_all / document_subset / source_file_subset
    document_ids: list[str] = Field(default_factory=list)
    source_file_ids: list[str] = Field(default_factory=list)
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    feature_flags: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    agent_type: str
    description: str
    status: str
    source_agent_id: str | None = None
    model_config_ref: str
    prompt_set_id: str | None = None
    knowledge_scope_id: str | None = None
    risk_policy_id: str | None = None
    eval_suite_id: str | None = None
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    is_tenant_default: bool = False
    created_by: str | None = None
    activated_at: str | None = None
    archived_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class AgentListResponse(BaseModel):
    items: list[AgentResponse]
    total: int
    limit: int
    offset: int


class CloneOptions(BaseModel):
    name: str
    tenant_id: str | None = None  # 默认 = source tenant
    prompt_set: str = "copy"  # copy / reference
    risk_policy: str = "copy"  # copy / reference
    knowledge_scope: str = "reference"  # reference / copy_subset / empty
    eval_suite: str = "copy"  # copy / reference / empty
    channel_config: str = "shell_only"  # shell_only / skip
    model_config_choice: str = "reference"  # reference / new
    description: str | None = None
    created_by: str | None = None


class CloneManifestResponse(BaseModel):
    id: str
    source_agent_id: str
    target_agent_id: str
    tenant_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    copied_resources: dict[str, Any] = Field(default_factory=dict)
    referenced_resources: dict[str, Any] = Field(default_factory=dict)
    reset_resources: dict[str, Any] = Field(default_factory=dict)
    skipped_resources: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ReadinessCheck(BaseModel):
    check: str
    label: str
    passed: bool
    required: bool
    reason: str = ""


class ReadinessResponse(BaseModel):
    agent_id: str
    ready: bool
    checks: list[ReadinessCheck] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    waivers: list[str] = Field(default_factory=list)


class AgentStatusAction(BaseModel):
    waiver_reasons: dict[str, str] | None = None  # 激活时可选的豁免理由
