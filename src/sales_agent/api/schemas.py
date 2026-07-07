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
    model: str | None = None  # 模型名（可选，覆盖 models.json 的 default_model）
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
    # Token 用量
    usage: dict[str, int] = Field(default_factory=dict)


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
    task_type: str = ""  # task 类必填；非 task 类可空（用 prompt_key 定位）
    template_text: str
    description: str = ""
    version: str = ""
    prompt_category: str = "task"
    prompt_key: str | None = None


class PromptVersionUpdate(BaseModel):
    template_text: str | None = None
    description: str | None = None


class PromptPreviewRequest(BaseModel):
    prompt_category: str = "task"
    prompt_key: str | None = None
    task_type: str | None = None  # 兼容旧字段：task 类的 prompt_key
    version_id: str | None = None  # None = use current active
    sample_message: str = ""
    sample_context: dict[str, Any] | None = None
    sample_variables: dict[str, str] | None = None  # router/risk/coach 的示例变量
    run_generation: bool = False


class PromptVersionResponse(BaseModel):
    id: str
    tenant_id: str
    task_type: str | None = None
    prompt_category: str = "task"
    prompt_key: str | None = None
    required_placeholders: list[str] = []
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
    prompt_category: str = "task"
    prompt_key: str | None = None
    task_type: str | None = None


class BuiltinPromptResponse(BaseModel):
    """内置 prompt 参考（只读，供前端展示默认模板与占位符）。"""

    prompt_category: str
    prompt_key: str
    template: str
    required_placeholders: list[str]
    description: str = ""


class SetPromptBindingRequest(BaseModel):
    """Agent prompt 绑定请求。version_id 为 None 时解绑。"""

    version_id: str | None = None


class EffectivePromptResponse(BaseModel):
    """某 (category, key) 当前生效的 prompt（DB active 优先，否则内置默认）。"""

    prompt_category: str
    prompt_key: str
    template: str
    required_placeholders: list[str]
    description: str = ""
    source: str  # "db_active" | "builtin"
    version_id: str | None = None
    version: str = ""


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


class AgentStatusAction(BaseModel):
    waiver_reasons: dict[str, str] | None = None  # 激活时可选的豁免理由
