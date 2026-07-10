"""配置加载模块，支持 YAML 文件 + 环境变量覆盖。"""

import os
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent"
    echo: bool = False


class ModelConfig(BaseModel):
    provider: str = "openai_compatible"
    api_key_env: str = "SALES_AGENT_API_KEY"
    base_url: str = "https://api.example.com/v1"
    chat_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    temperature: float = 0.3
    timeout_seconds: int = 30
    max_retries: int = 2
    embedding_dimensions: int = 1024
    # 独立 embedding provider（可选，不填则与 chat 共用 base_url/api_key）
    embedding_base_url: str = ""
    embedding_api_key_env: str = ""


class ConversationConfig(BaseModel):
    history_turns: int = 4
    history_turns_configurable: bool = True
    expire_after_hours: int = 8
    force_previous_on_continuation_intent: bool = True
    summary_update_policy: str = "threshold"
    summary_after_turns: int = 8
    summary_after_chars: int = 5000
    idle_summary_check_minutes: int = 30
    refresh_summary_before_context_overflow: bool = True
    reset_commands: list[str] = Field(
        default_factory=lambda: [
            "新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"
        ]
    )


class RetrievalConfig(BaseModel):
    top_k: int = 5
    min_score: float = 0.35
    chunk_size: int = 700
    chunk_overlap: int = 120
    # 检索模式：vector | keyword | hybrid（默认 hybrid，RRF 融合）
    mode: str = "hybrid"
    # 关键词检索在 RRF 融合中的权重（0.0 ~ 1.0，默认 0.5）
    keyword_weight: float = 0.5
    # RRF 常数 k（越大排名差异越不明显，默认 60）
    rrf_k: int = 60
    # 同义词文件路径（相对项目根目录）
    synonyms_path: str = "data/synonyms.json"
    # MD 优化预处理开关（在 chunk 前调用 LLM 增强 MD，默认关闭）
    md_optimization_enabled: bool = False
    # Graph 路径（钉钉 Stream）并行 Ontology + RAG 检索开关
    # True = LangGraph Send fan-out 同时执行两条检索路径，结果合并
    parallel_enabled: bool = True


class SourceDisplayConfig(BaseModel):
    sales_visible_mode: str = "title_only"
    max_visible_sources: int = 3
    keep_snippet_ref: bool = True
    provide_snippet_page: bool = True
    show_chunk_text_in_message: bool = False


class RiskConfig(BaseModel):
    tenant_custom_rules_enabled: bool = False
    # 是否在图节点 check_risk 中启用 LLM 风控（规则 full_check 之后的增强层）。
    # 默认关闭：LLM 风控失败兜底为 allow，需配合 _merge_risk_results + try/except
    # 回退规则结果，避免静默放行。灰度时按租户/配置打开。
    enable_llm_risk_check: bool = False
    default_price_commitment_action: str = "warn"
    default_delivery_commitment_action: str = "block"
    default_unsupported_claim_action: str = "rewrite"
    default_discount_commitment_action: str = "warn"
    default_contract_commitment_action: str = "block"
    default_competitor_attack_action: str = "rewrite"
    default_sensitive_external_message_action: str = "warn"
    default_cross_tenant_leakage_action: str = "block"
    default_manipulative_sales_action: str = "block"


class LatencyConfig(BaseModel):
    """延迟优化配置。"""
    enabled: bool = True
    default_path: str = "standard"
    long_message_chars: int = 3000
    long_history_chars: int = 5000
    processing_notice_after_seconds: float = 5.0


class PathRouterConfig(BaseModel):
    """路径路由配置。"""
    enable_fast_path: bool = True
    enable_slow_path_notice: bool = True
    # 是否在图节点 route_task 中启用 LLM 路由兜底（规则置信度不足时调 LLM 分类）。
    # 默认关闭：每轮多一次 LLM 调用增加延迟，且 LLM 路由失败需回退规则。
    # 灰度时打开；关闭时节点走 route_task_rules_only，行为与现状一致。
    enable_llm_router: bool = False
    llm_router_confidence_threshold: float = 0.75
    clarify_confidence_threshold: float = 0.45


class LoggingConfig(BaseModel):
    store_prompts: bool = True
    store_retrieval_sources: bool = True
    store_full_conversation: bool = True
    redact_sensitive_text: bool = False


class OntologyConfig(BaseModel):
    """Ontology knowledge engine config."""

    knowledge_engine: str = "legacy_rag"  # legacy_rag | ontology_neo4j | hybrid
    hybrid_retrieval: bool = False  # True = 同时跑 ontology + RAG，LLM 整合
    vector_fallback: str = "conservative"
    # 视觉模型名称（用于图片/扫描件解读，默认为 qwen-vl-plus）
    vision_model: str = "qwen-vl-plus"
    # 是否在 ingestion 中开启图片视觉解读（默认关闭）
    vision_enabled: bool = False
    # ── 运行时可控的检索参数（优化器可调） ──
    entity_limit: int = 15          # Cypher 返回的最多实体数
    facts_per_entity: int = 20      # 每个实体的最多 fact 数
    max_entities_for_prompt: int = 10   # 塞给 LLM 的最多实体数
    max_facts_for_prompt: int = 25      # 塞给 LLM 的最多 fact 数
    vector_fallback_top_k: int = 5      # 向量回退返回数


class WebSearchConfig(BaseModel):
    """联网搜索兜底配置（Bocha API）。"""

    enabled: bool = True
    api_key: str = ""
    top_n: int = 5


class Neo4jConfig(BaseModel):
    """Neo4j connection and visualization config."""

    uri: str = ""
    user: str = ""
    password: str = ""
    database: str = "neo4j"
    visual_url: str = ""
    connection_timeout_seconds: float = 5.0


class AppConfig(BaseModel):
    """应用全局配置。"""

    log_level: str = "info"
    max_message_chars: int = 6000
    process_role: str = "all"  # "all" | "api" | "stream" | "worker"
    # 前端静态文件目录（打进镜像时为 /app/console/dist）。
    # 为空字符串时不托管前端（dev 模式由 vite 自行服务）。
    console_dist_dir: str = ""
    # 数据文件存储目录（上传文件、临时文件等）。为空时使用代码仓库下的 data/。
    data_dir: str = ""

    VALID_ROLES: ClassVar[tuple[str, ...]] = ("all", "api", "stream", "worker")

    def get_process_role(self) -> str:
        """返回合法的 process_role，无效值回退为 all。"""
        if self.process_role in self.VALID_ROLES:
            return self.process_role
        import logging
        logging.getLogger(__name__).warning(
            "Invalid PROCESS_ROLE=%r, falling back to 'all'", self.process_role,
        )
        return "all"


class TopicRoutingConfig(BaseModel):
    """Bounded intent routing — topic management, context resolution, evidence routing."""

    enabled: bool = False
    idle_minutes: int = 30
    restore_hours: int = 24
    max_clarification_attempts: int = 2


class GuidedFlowsConfig(BaseModel):
    """Unified online guided conversation flows (访前准备, 访后复盘, 小赢欣赏, 卡点破框)."""

    enabled: bool = True
    timezone: str = "Asia/Shanghai"


class UserProfileMemoryConfig(BaseModel):
    """Evidence-backed user profile projection and bounded recall."""

    enabled: bool = False
    recall_enabled: bool = True
    transparency_enabled: bool = True
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 2.0
    rebuild_batch_size: int = 20
    rebuild_max_attempts: int = 5
    max_recall_items: int = 5
    max_recall_chars: int = 1200
    retrieval_timeout_ms: int = 120


class LongTermMemoryConfig(BaseModel):
    """Governed long-term atomic memory."""

    enabled: bool = False
    candidate_extraction_enabled: bool = True
    outbox_worker_enabled: bool = True
    outbox_poll_interval_seconds: float = 2.0
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5
    explicit_confirmation_required_for_broad_forget: bool = True


class ScenarioCoachConfig(BaseModel):
    """场景教练：识别预设销售场景问题，命中即返回预设答案。默认关闭。"""

    enabled: bool = False
    confidence_threshold: float = 0.8


class Settings(BaseModel):
    """顶层设置，聚合所有子配置。"""

    app: AppConfig = AppConfig()
    database: DatabaseConfig = DatabaseConfig()
    model: ModelConfig = ModelConfig()
    conversation: ConversationConfig = ConversationConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    source_display: SourceDisplayConfig = SourceDisplayConfig()
    risk: RiskConfig = RiskConfig()
    logging: LoggingConfig = LoggingConfig()
    latency: LatencyConfig = LatencyConfig()
    path_router: PathRouterConfig = PathRouterConfig()
    ontology: OntologyConfig = OntologyConfig()
    neo4j: Neo4jConfig = Neo4jConfig()
    web_search: WebSearchConfig = WebSearchConfig()
    topic_routing: TopicRoutingConfig = TopicRoutingConfig()
    guided_flows: GuidedFlowsConfig = GuidedFlowsConfig()
    scenario_coach: ScenarioCoachConfig = ScenarioCoachConfig()
    long_term_memory: LongTermMemoryConfig = LongTermMemoryConfig()
    user_profile_memory: UserProfileMemoryConfig = UserProfileMemoryConfig()

    # LLM 调用参数（temperature/max_tokens）默认值文件路径，开发者维护、git 管版本
    llm_call_defaults_path: str = "config/llm_call_defaults.yaml"

    # 延迟导入避免循环依赖
    @property
    def dingtalk(self):
        from sales_agent.integrations.dingtalk.config import DingTalkConfig
        if not hasattr(self, "_dingtalk"):
            self._dingtalk = DingTalkConfig()
        return self._dingtalk

    @dingtalk.setter
    def dingtalk(self, value):
        self._dingtalk = value

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Settings":
        """从 YAML 文件加载配置，环境变量可覆盖。"""
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        # 环境变量覆盖 app.process_role
        process_role = os.getenv("PROCESS_ROLE")
        if process_role:
            raw.setdefault("app", {})["process_role"] = process_role

        # 环境变量覆盖 database.url
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            # Docker 内 asyncpg 需要换成 asyncpg 驱动
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            raw.setdefault("database", {})["url"] = db_url

        # 环境变量覆盖 model 配置
        model_base_url = os.getenv("MODEL_BASE_URL")
        if model_base_url:
            raw.setdefault("model", {})["base_url"] = model_base_url

        model_api_key = os.getenv("MODEL_API_KEY")
        if model_api_key:
            raw.setdefault("model", {})["api_key_env"] = "MODEL_API_KEY"

        chat_model = os.getenv("CHAT_MODEL")
        if chat_model:
            raw.setdefault("model", {})["chat_model"] = chat_model

        embedding_model = os.getenv("EMBEDDING_MODEL")
        if embedding_model:
            raw.setdefault("model", {})["embedding_model"] = embedding_model

        # 独立 embedding provider（可选）
        embedding_base_url = os.getenv("EMBEDDING_BASE_URL")
        if embedding_base_url:
            raw.setdefault("model", {})["embedding_base_url"] = embedding_base_url

        embedding_api_key = os.getenv("EMBEDDING_API_KEY")
        if embedding_api_key:
            raw.setdefault("model", {})["embedding_api_key_env"] = "EMBEDDING_API_KEY"

        # 环境变量覆盖钉钉配置
        from sales_agent.integrations.dingtalk.config import DingTalkConfig

        dt_env_vars = {
            "enabled": os.getenv("DINGTALK_ENABLED"),
            "message_mode": os.getenv("DINGTALK_MESSAGE_MODE"),
            "corp_id": os.getenv("DINGTALK_CORP_ID"),
            "app_key": os.getenv("DINGTALK_APP_KEY"),
            "app_secret": os.getenv("DINGTALK_APP_SECRET"),
            "robot_code": os.getenv("DINGTALK_ROBOT_CODE"),
            "agent_id": os.getenv("DINGTALK_AGENT_ID"),
            "encrypt_token": os.getenv("DINGTALK_ENCRYPT_TOKEN"),
            "aes_key": os.getenv("DINGTALK_AES_KEY"),
            "card_template_id": os.getenv("DINGTALK_CARD_TEMPLATE_ID"),
            "public_url": os.getenv("DINGTALK_PUBLIC_URL"),
            "stream_update_interval_ms": os.getenv("DINGTALK_STREAM_UPDATE_INTERVAL_MS"),
            "stream_min_chunk_chars": os.getenv("DINGTALK_STREAM_MIN_CHUNK_CHARS"),
            "media_enabled": os.getenv("DINGTALK_MEDIA_ENABLED"),
            "media_base_url": os.getenv("DINGTALK_MEDIA_BASE_URL"),
            "media_api_key_env": "DINGTALK_MEDIA_API_KEY" if os.getenv("DINGTALK_MEDIA_API_KEY") else os.getenv("DINGTALK_MEDIA_API_KEY_ENV"),
            "vision_model": os.getenv("DINGTALK_VISION_MODEL"),
            "audio_model": os.getenv("DINGTALK_AUDIO_MODEL"),
            "media_download_timeout_seconds": os.getenv("DINGTALK_MEDIA_DOWNLOAD_TIMEOUT_SECONDS"),
        }
        dt_overrides = {k: v for k, v in dt_env_vars.items() if v}
        if dt_overrides:
            raw.setdefault("dingtalk", {}).update(dt_overrides)

        # enabled 特殊处理：环境变量 "true" → True
        dt_enabled = os.getenv("DINGTALK_ENABLED")
        if dt_enabled is not None:
            raw.setdefault("dingtalk", {})["enabled"] = dt_enabled.lower() in ("true", "1", "yes")

        # media_enabled 特殊处理
        dt_media_enabled = os.getenv("DINGTALK_MEDIA_ENABLED")
        if dt_media_enabled is not None:
            raw.setdefault("dingtalk", {})["media_enabled"] = dt_media_enabled.lower() in ("true", "1", "yes")

        # streaming_enabled 特殊处理
        dt_streaming = os.getenv("DINGTALK_STREAMING_ENABLED")
        if dt_streaming is not None:
            raw.setdefault("dingtalk", {})["streaming_enabled"] = dt_streaming.lower() in ("true", "1", "yes")

        # 数值字段转换
        for int_key in ("stream_update_interval_ms", "stream_min_chunk_chars", "media_download_timeout_seconds"):
            val = raw.get("dingtalk", {}).get(int_key)
            if isinstance(val, str):
                raw.setdefault("dingtalk", {})[int_key] = int(val)

        # 环境变量覆盖 ontology 配置
        knowledge_engine = os.getenv("KNOWLEDGE_ENGINE")
        if knowledge_engine:
            raw.setdefault("ontology", {})["knowledge_engine"] = knowledge_engine
        hybrid_retrieval = os.getenv("HYBRID_RETRIEVAL", "").lower()
        if hybrid_retrieval in ("1", "true", "yes"):
            raw.setdefault("ontology", {})["hybrid_retrieval"] = True

        ontology_vector_fallback = os.getenv("ONTOLOGY_VECTOR_FALLBACK")
        if ontology_vector_fallback:
            raw.setdefault("ontology", {})["vector_fallback"] = ontology_vector_fallback

        # 环境变量覆盖 neo4j 配置
        neo4j_env = {
            "uri": os.getenv("NEO4J_URI"),
            "user": os.getenv("NEO4J_USER"),
            "password": os.getenv("NEO4J_PASSWORD"),
            "database": os.getenv("NEO4J_DATABASE"),
            "visual_url": os.getenv("NEO4J_VISUAL_URL"),
        }
        neo4j_overrides = {k: v for k, v in neo4j_env.items() if v}
        if neo4j_overrides:
            raw.setdefault("neo4j", {}).update(neo4j_overrides)

        # 环境变量覆盖 web_search 配置
        web_search_api_key = os.getenv("BOCHA_API_KEY", "")
        if web_search_api_key:
            raw.setdefault("web_search", {})["api_key"] = web_search_api_key
            raw.setdefault("web_search", {})["enabled"] = True
        web_search_top_n = os.getenv("BOCHA_TOP_N", "")
        if web_search_top_n:
            raw.setdefault("web_search", {})["top_n"] = int(web_search_top_n)

        # 环境变量覆盖 guided_flows 配置
        guided_flows_enabled = os.getenv("GUIDED_FLOWS_ENABLED")
        if guided_flows_enabled is not None:
            raw.setdefault("guided_flows", {})["enabled"] = (
                guided_flows_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )

        # 环境变量覆盖 topic_routing 配置
        topic_routing_enabled = os.getenv("TOPIC_ROUTING_ENABLED")
        if topic_routing_enabled is not None:
            raw.setdefault("topic_routing", {})["enabled"] = (
                topic_routing_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )

        # 环境变量覆盖 scenario_coach 配置
        scenario_coach_enabled = os.getenv("SCENARIO_COACH_ENABLED")
        if scenario_coach_enabled is not None:
            raw.setdefault("scenario_coach", {})["enabled"] = (
                scenario_coach_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )
        scenario_coach_threshold = os.getenv("SCENARIO_COACH_CONFIDENCE_THRESHOLD")
        if scenario_coach_threshold is not None:
            try:
                raw.setdefault("scenario_coach", {})["confidence_threshold"] = float(
                    scenario_coach_threshold
                )
            except ValueError:
                pass

        # 环境变量覆盖 LLM 路由/风控开关（图节点 route_task / check_risk 灰度用）
        llm_router_enabled = os.getenv("PATH_ROUTER_ENABLE_LLM_ROUTER")
        if llm_router_enabled is not None:
            raw.setdefault("path_router", {})["enable_llm_router"] = (
                llm_router_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )
        llm_risk_enabled = os.getenv("RISK_ENABLE_LLM_RISK_CHECK")
        if llm_risk_enabled is not None:
            raw.setdefault("risk", {})["enable_llm_risk_check"] = (
                llm_risk_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )

        instance = cls(**raw)
        # 构造 DingTalkConfig 并设置到 instance
        dt_raw = raw.get("dingtalk", {})
        instance._dingtalk = DingTalkConfig(**dt_raw)
        return instance


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        config_path = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
        _settings = Settings.from_yaml(config_path)
    return _settings
