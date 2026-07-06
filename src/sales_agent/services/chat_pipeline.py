"""共享 Chat 管道 — 串联完整的请求生命周期，支持 fast / standard / slow 三级路径。

已废弃：HTTP /agent/chat 与钉钉 Stream 均已改走 LangGraph Graph，生产零调用。
保留待清理，勿按本类追调用链。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import get_settings, Settings
from sales_agent.core.tenant_runtime import get_tenant_runtime
from sales_agent.models.base import generate_id
from sales_agent.models.conversation import ConversationMessage
from sales_agent.services.latency_tracker import PipelineTimings
from sales_agent.services.path_router import PathRouter, PathResult
from sales_agent.services.request_validator import validate_chat_request
from sales_agent.services.task_router import (
    route_task,
    _match_rules,
    _resolve_priority,
    TASK_DEFAULT_RETRIEVAL,
)
from sales_agent.services.retriever import HybridRetriever, Retriever
from sales_agent.services.agent_executor import execute_agent
from sales_agent.services.risk_checker import RiskChecker, RiskCheckResult, merge_risk_results
from sales_agent.services import conversation_logger
from sales_agent.services.tenant_resolver import TenantResolver
from sales_agent.services.response_formatter import build_chat_response
from sales_agent.services.latency_stats import get_latency_stats_collector
from sales_agent.services.prompt_registry import PromptRegistry
from sales_agent.services.run_tracer import RunTracer
from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.repository import OntologyRepository
from sales_agent.ontology.retrieval_service import OntologyRetrievalService

logger = logging.getLogger(__name__)


def _build_retriever(db, embedding_model):
    """根据 retrieval.mode 配置构建向量/关键词/混合检索器。

    Returns:
        Retriever | HybridRetriever — 两者都实现了 retrieve_for_task() 接口
    """
    settings = get_settings()
    mode = settings.retrieval.mode

    if mode == "keyword":
        from sales_agent.rag.keyword_retriever import KeywordRetriever
        kr = KeywordRetriever(db)
        # KeywordRetriever.search() 直接使用，不需要 retrieve_for_task 接口。
        # 构造一个简单 wrapper 或回退到 Retriever（vector）以保证兼容性。
        # 实际使用中，keyword-only 模式较少用，这里回退到 hybrid。
        logger.warning("retrieval.mode=keyword is not fully integrated as standalone; using hybrid instead.")
        return HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )

    if mode == "hybrid":
        from sales_agent.rag.keyword_retriever import KeywordRetriever
        kr = KeywordRetriever(db)
        return HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )

    # 默认：纯向量检索
    return Retriever(db, embedding_model)


def _build_ontology_answer_service(settings: "Settings", model_provider) -> OntologyAnswerService:
    """构建 OntologyAnswerService（Neo4j 知识图谱回答）。

    在 settings.ontology.knowledge_engine == "ontology_neo4j" 时由管道检索分支调用。
    引用模块全局 `OntologyAnswerService`，便于测试 monkeypatch 替换。
    """
    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)
    retrieval = OntologyRetrievalService(repository, model_provider.embedding, model_provider.chat)
    return OntologyAnswerService(retrieval, model_provider.chat)

# --- 快速命令集 ---
_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}

_HELP_TEXT = (
    "你可以直接问销售问题，例如：\n"
    "1. 客户说太贵了怎么回？\n"
    "2. 帮我写一段温和跟进话术\n"
    "3. 帮我复盘这段聊天记录\n\n"
    '发送"新话题"可以清空当前上下文。'
)

_RESET_TEXT = "已开启新话题。你可以直接说当前要处理的销售问题。"


@dataclass
class PipelineResult:
    """管道执行结果。"""

    answer_dict: dict[str, Any]
    sources: list[dict]
    risk_result: RiskCheckResult
    route_result: Any  # RouteResult
    path_result: PathResult
    timings: PipelineTimings
    conversation_id: str
    # 快速命令直接回复
    fast_reply: str | None = None
    # Agent 运行追踪 ID
    run_id: str | None = None
    # Token 用量（prompt_tokens, completion_tokens, total_tokens）
    usage: dict[str, int] = field(default_factory=dict)


class _ProcessingNoticeGuard:
    """管理钉钉 slow path 的"处理中"提示。

    在阈值时间后发送提示，主管道完成后取消。
    """

    def __init__(
        self,
        reply_fn: Callable[[str], Awaitable[None]],
        threshold_seconds: float,
    ):
        self._reply_fn = reply_fn
        self._threshold = threshold_seconds
        self._task: asyncio.Task | None = None
        self._cancelled = False

    async def start(self) -> None:
        """启动延迟提示任务。"""
        self._task = asyncio.create_task(self._delayed_notice())

    async def _delayed_notice(self) -> None:
        try:
            await asyncio.sleep(self._threshold)
            if not self._cancelled:
                await self._reply_fn("我正在结合资料整理，稍等一下。")
        except asyncio.CancelledError:
            pass

    def cancel(self) -> None:
        """取消提示任务。"""
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()


class ChatPipeline:
    """共享 Chat 管道。

    用法::

        pipeline = ChatPipeline(db, settings)
        result = await pipeline.execute(
            tenant_id="t1", user_id="u1", message="帮助",
            conversation_id="conv1",
        )
    """

    def __init__(self, db: AsyncSession, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.path_router = PathRouter(
            latency_config=self.settings.latency,
            path_router_config=self.settings.path_router,
        )

    async def execute(
        self,
        *,
        tenant_id: str,
        user_id: str,
        message: str,
        conversation_id: str,
        context: dict[str, Any] | None = None,
        channel: str = "local",
        reply_fn: Callable[[str], Awaitable[None]] | None = None,
        agent_id: str | None = None,
        model: str | None = None,
    ) -> PipelineResult:
        """执行 Chat 管道。

        根据 PathRouter 选择 fast / standard / slow 路径，
        条件化各步骤（LLM router、RAG、LLM risk check）。
        """
        timings = PipelineTimings()

        # =============================================
        # 1. 快速命令检查（在所有重量级操作之前）
        # =============================================
        stripped = message.strip()
        if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
            timings.start("fast_command")
            path_result = self.path_router.select_path(
                message=message, route_confidence=1.0,
            )
            timings.end("fast_command")
            await self._record_stats(path_result.path, timings.total_ms)
            return PipelineResult(
                answer_dict={"summary": _HELP_TEXT, "sections": []},
                sources=[],
                risk_result=RiskCheckResult(),
                route_result=None,
                path_result=path_result,
                timings=timings,
                conversation_id=conversation_id,
                fast_reply=_HELP_TEXT,
            )

        if stripped in _RESET_COMMANDS:
            timings.start("fast_command")
            path_result = self.path_router.select_path(
                message=message, route_confidence=1.0,
            )
            timings.end("fast_command")
            # 返回 fast_reply 表示需要新开 conversation_id（由调用方处理）
            await self._record_stats(path_result.path, timings.total_ms)
            return PipelineResult(
                answer_dict={"summary": _RESET_TEXT, "sections": []},
                sources=[],
                risk_result=RiskCheckResult(),
                route_result=None,
                path_result=path_result,
                timings=timings,
                conversation_id=generate_id(),  # 新 conversation_id
                fast_reply=_RESET_TEXT,
            )

        # =============================================
        # 2. 请求校验
        # =============================================
        timings.start("validation")
        validate_chat_request(tenant_id, user_id, message)
        timings.end("validation")

        # 初始化 Run Tracer（在 validation 通过后）
        tracer = RunTracer(self.db)

        # =============================================
        # 3. 租户解析
        # =============================================
        timings.start("tenant_resolve")
        resolver = TenantResolver(self.db)
        tenant_info = await resolver.resolve(tenant_id)
        tenant_config = tenant_info.get("config", {})
        model_provider = resolver.get_model_provider(tenant_info)
        timings.end("tenant_resolve")

        # =============================================
        # 3b. 模型覆盖（可选）— 评估脚本用此切换模型
        # =============================================
        runtime = get_tenant_runtime()
        chat_model_override = None
        if model and model != runtime.chat_model:
            from sales_agent.core.model_registry import ModelRegistry
            _registry = ModelRegistry.load()
            if _registry:
                _entry = _registry.get(model)
                if _entry and _entry.api_key:
                    from sales_agent.llm import OpenAICompatibleChat as _Chat
                    chat_model_override = _Chat(
                        api_key=_entry.api_key,
                        base_url=_entry.base_url,
                        model=_entry.chat_model,
                        temperature=_entry.temperature,
                        timeout_seconds=_entry.timeout_seconds,
                        max_retries=_entry.max_retries,
                    )
                    logger.info(
                        "Model override: %s (base_url=%s) for request",
                        model, _entry.base_url,
                    )
                else:
                    logger.warning(
                        "Model override %r not found or missing API key, using default", model,
                    )
            else:
                logger.warning("models.json not found, model override disabled")

        # 决定本次请求实际使用的 chat model
        _effective_chat = chat_model_override if chat_model_override else model_provider.chat
        # 初始化 token 用量（后续在主生成阶段覆盖）
        _main_usage: dict[str, int] = {}

        # 3b. Agent 解析（向后兼容：agent_id 为 None 时回退到 tenant 默认 Agent）
        resolved_agent_id: str | None = agent_id
        allowed_doc_ids: set[str] | None = None
        agent_risk_config: dict[str, Any] = {}
        try:
            from sales_agent.services.agent_service import (
                resolve_tenant_agent_id,
                load_agent_document_scope,
            )
            from sales_agent.models.agent_risk_policy import AgentRiskPolicy
            agent_obj = await resolve_tenant_agent_id(self.db, tenant_id, agent_id)
            resolved_agent_id = agent_obj.id
            allowed_doc_ids = await load_agent_document_scope(self.db, agent_obj)
            if agent_obj.risk_policy_id:
                rp = (
                    await self.db.execute(
                        __import__("sqlalchemy").select(AgentRiskPolicy).where(
                            AgentRiskPolicy.id == agent_obj.risk_policy_id
                        )
                    )
                ).scalar_one_or_none()
                if rp and rp.rules_json:
                    import json as _json
                    rules = _json.loads(rp.rules_json)
                    if isinstance(rules, dict):
                        agent_risk_config = {
                            f"default_{k}_action": v for k, v in rules.items()
                        }
        except Exception as e:
            logger.warning("Agent scope resolution failed, falling back to tenant scope: %s", e)
            resolved_agent_id = agent_id

        # =============================================
        # 3d. Release version resolution (pinned knowledge version)
        # =============================================
        pinned_knowledge_version_id: str | None = None
        try:
            from sqlalchemy import select
            from sales_agent.models.runtime_release import AgentRuntimeBinding
            from sales_agent.services.release_service import ReleaseService

            binding = await self.db.scalar(
                select(AgentRuntimeBinding).where(
                    AgentRuntimeBinding.tenant_id == tenant_id,
                    AgentRuntimeBinding.agent_id == resolved_agent_id,
                )
            )
            if binding is not None:
                manifest = await ReleaseService(self.db).get_manifest(
                    tenant_id, binding.active_release_id,
                )
                pinned_knowledge_version_id = manifest.knowledge_version_id
        except Exception:
            pinned_knowledge_version_id = None

        # =============================================
        # 3c. Coach 报告意图拦截（早于正常任务路由；失败不得影响正常会话）
        # =============================================
        try:
            from sales_agent.coach.intent_router import match_coach_intent
            coach_report_type = match_coach_intent(stripped)
        except Exception as e:
            logger.warning("Coach intent match failed, fallback to normal routing: %s", e)
            coach_report_type = None

        if coach_report_type is not None and resolved_agent_id:
            try:
                from sales_agent.coach.report_service import CoachReportService
                from sales_agent.models.coach import CoachReportRequest

                coach_timings = PipelineTimings()
                coach_timings.start("coach_report")
                report = await CoachReportService(self.db).render_report(
                    tenant_id=tenant_id,
                    agent_id=resolved_agent_id,
                    user_id=user_id,
                    report_type=coach_report_type,
                    query_text=stripped,
                )
                coach_timings.end("coach_report")

                # 报告请求审计
                self.db.add(CoachReportRequest(
                    tenant_id=tenant_id,
                    agent_id=resolved_agent_id,
                    user_id=user_id,
                    report_type=coach_report_type,
                    query_text=stripped,
                    rendered_summary=str(report.get("summary", ""))[:500],
                ))
                # 记录会话日志（task_type=coach_report），不计入每日评分输入
                await conversation_logger.log_conversation(
                    self.db,
                    tenant_id=tenant_id, user_id=user_id, channel=channel,
                    agent_id=resolved_agent_id, conversation_id=conversation_id,
                    message=stripped, task_type="coach_report",
                    answer_dict=report, status="completed",
                    stage_latency_ms=coach_timings.to_dict(),
                )
                await self.db.flush()

                coach_path = self.path_router.select_path(message=stripped, route_confidence=1.0)
                await self._record_stats(coach_path.path, coach_timings.total_ms)
                return PipelineResult(
                    answer_dict=report,
                    sources=[],
                    risk_result=RiskCheckResult(),
                    route_result=None,
                    path_result=coach_path,
                    timings=coach_timings,
                    conversation_id=conversation_id,
                )
            except Exception as e:
                logger.warning("Coach report rendering failed, fallback to normal routing: %s", e)

        # 启动 run trace（在 tenant resolve 之后，因为需要确认 tenant 存在）
        await tracer.start_run(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            agent_id=resolved_agent_id,
        )
        await tracer.record_step("validation", latency_ms=int(timings.stages.get("validation", 0)))
        await tracer.record_step("tenant_resolve", latency_ms=int(timings.stages.get("tenant_resolve", 0)))

        try:
            # =============================================
            # 4. 上下文加载
            # =============================================
            timings.start("context_load")
            history_messages = await _load_recent_history(
                self.db, tenant_id, user_id, conversation_id, self.settings,
            )
            history_length = sum(len(m.get("content", "")) for m in history_messages)
            message_length = len(message)
            timings.end("context_load")
            await tracer.record_step("context_load", latency_ms=int(timings.stages.get("context_load", 0)))

            # =============================================
            # 5. 规则风险预检（快速，所有请求都执行）
            # =============================================
            timings.start("risk_precheck")
            risk_checker = RiskChecker()
            input_risk = risk_checker.check_input(message)
            timings.end("risk_precheck")

            # =============================================
            # 6. 任务路由（规则优先，LLM 兜底）
            # =============================================
            timings.start("routing")
            route_result = await route_task(
                message=message,
                chat_model=_effective_chat,
                db=self.db,
                tenant_id=tenant_id,
                agent_id=resolved_agent_id,
            )
            task_type = route_result.task_type
            needs_retrieval = route_result.needs_retrieval
            timings.end("routing")
            await tracer.record_step(
                "routing",
                latency_ms=int(timings.stages.get("routing", 0)),
                metadata={
                    "task_type": task_type,
                    "confidence": route_result.confidence,
                    "needs_retrieval": needs_retrieval,
                },
            )
            # Record structured route trace for evaluation attribution
            await tracer.record_route_trace(
                router_type=route_result.router_type if hasattr(route_result, "router_type") else "llm",
                task_type=task_type,
                confidence=route_result.confidence,
                llm_called=getattr(route_result, "llm_router_called", False),
                needs_retrieval=needs_retrieval,
                decision_reason=getattr(route_result, "reason", ""),
            )

            # =============================================
            # 6b. Prompt 解析（task + system，均经 registry 三级回退）
            # =============================================
            from sales_agent.services.prompt_resolver_helper import (
                resolve_execution_prompts,
            )
            prompt_text, system_prompt_text = await resolve_execution_prompts(
                self.db, resolved_agent_id, tenant_id, task_type
            )

            # =============================================
            # 7. 路径选择
            # =============================================
            path_result = self.path_router.select_path(
                message=message,
                task_type=task_type,
                route_confidence=route_result.confidence,
                needs_retrieval=needs_retrieval,
                history_length=history_length,
                message_length=message_length,
                input_risk_level=input_risk.level,
            )
            logger.info(
                "Path selected: path=%s reason=%s task=%s",
                path_result.path, path_result.reason, task_type,
            )

            # =============================================
            # 8. 处理中提示（slow path）
            # =============================================
            notice_guard = None
            if path_result.allow_processing_notice and reply_fn is not None:
                notice_guard = _ProcessingNoticeGuard(
                    reply_fn, self.settings.latency.processing_notice_after_seconds,
                )
                await notice_guard.start()

            try:
                # =============================================
                # 9. 检索（支持 hybrid: ontology + RAG 并行）
                # =============================================
                sources: list[dict] = []
                retrieval_result = None
                retrieval_info: dict[str, Any] = {}
                skip_generation = False
                ontology_graph_evidence: Any = None  # hybrid 模式下的图谱证据
                ontology_context_text: str = ""      # hybrid 模式下的图谱上下文

                if path_result.needs_retrieval:
                    use_hybrid = self.settings.ontology.hybrid_retrieval
                    engine = self.settings.ontology.knowledge_engine
                    use_onto = use_hybrid or engine == "ontology_neo4j"
                    use_rag = use_hybrid or engine != "ontology_neo4j"

                    # 并行执行 ontology + RAG（asyncio.gather）
                    async def _run_ontology():
                        """运行 ontology 检索（仅获取 GraphEvidence，不生成答案）。"""
                        nonlocal ontology_graph_evidence, ontology_context_text
                        from sales_agent.ontology.answer_service import graph_evidence_to_sources
                        try:
                            ontology_service = _build_ontology_answer_service(
                                self.settings, model_provider,
                            )
                            evidence = await ontology_service.retrieval.retrieve(
                                tenant_id=tenant_id,
                                agent_id=resolved_agent_id,
                                question=message,
                            )
                            ontology_graph_evidence = evidence
                            onto_sources = graph_evidence_to_sources(evidence)
                            ontology_context_text = evidence.to_context_text()
                            return ("ontology", onto_sources, evidence)
                        except Exception as e:
                            logger.warning("Ontology retrieval failed (hybrid fallback): %s", e)
                            return ("ontology_error", [], None)

                    async def _run_rag():
                        """运行 RAG 检索。"""
                        try:
                            retriever = _build_retriever(self.db, model_provider.embedding)
                            rr = await retriever.retrieve_for_task(
                                tenant_id=tenant_id,
                                message=message,
                                task_type=task_type,
                                needs_retrieval=needs_retrieval,
                                allowed_document_ids=allowed_doc_ids,
                                knowledge_version_id=pinned_knowledge_version_id,
                            )
                            rag_sources = [s.to_source_item() for s in rr.sources]
                            return ("rag", rag_sources, rr)
                        except Exception as e:
                            logger.warning("RAG retrieval failed (hybrid fallback): %s", e)
                            return ("rag_error", [], None)

                    import asyncio
                    tasks = []
                    if use_onto:
                        tasks.append(_run_ontology())
                    if use_rag:
                        tasks.append(_run_rag())

                    if tasks:
                        gather_results = await asyncio.gather(*tasks, return_exceptions=True)

                        # 合并结果
                        all_sources: list[dict] = []
                        for item in gather_results:
                            if isinstance(item, Exception):
                                logger.warning("Retrieval task failed: %s", item)
                                continue
                            tag, srcs, result_obj = item
                            if tag in ("ontology",):
                                all_sources.extend(srcs)
                            elif tag in ("rag",):
                                all_sources.extend(srcs)
                                retrieval_result = result_obj
                            # tag in ("ontology_error", "rag_error"): skip

                        sources = all_sources

                        # 兜底：hybrid 检索全部为空 → 联网搜索（Bocha）
                        if not sources and self.settings.web_search.enabled:
                            try:
                                from sales_agent.services.web_search import (
                                    bocha_search, web_search_sources_to_context,
                                )
                                web_result = await bocha_search(
                                    query=message,
                                    api_key=self.settings.web_search.api_key,
                                    top_n=self.settings.web_search.top_n,
                                )
                                if web_result.success and web_result.sources:
                                    sources = web_result.sources
                                    if web_result.raw_answer:
                                        ontology_context_text = (
                                            ontology_context_text
                                            + "\n\n"
                                            + web_search_sources_to_context(web_result)
                                        ).strip()
                                    logger.info(
                                        "Web search fallback: %d sources for query=%s",
                                        len(sources), message[:80],
                                    )
                            except Exception as e:
                                logger.warning("Web search fallback failed: %s", e)

                        retrieval_info = {
                            "called": True,
                            "provider": "hybrid" if use_hybrid else engine,
                            "source_count": len(sources),
                            "ontology_used": use_onto and ontology_graph_evidence is not None,
                            "rag_used": use_rag and retrieval_result is not None,
                            "web_search_used": any(
                                isinstance(s, dict) and s.get("source_type") == "web_search"
                                for s in sources
                            ) if sources else False,
                        }
                        if ontology_graph_evidence:
                            retrieval_info["graph_evidence"] = ontology_graph_evidence.to_dict()
                            # Record ontology trace
                            await tracer.record_step(
                                "ontology_answer",
                                latency_ms=int(ontology_graph_evidence.timings_ms.get("ontology_retrieval", 0)),
                                metadata={"provider": "ontology_neo4j", "graph_evidence": ontology_graph_evidence.to_dict()},
                            )
                        if retrieval_result:
                            retrieval_info["top_k"] = self.settings.retrieval.top_k
                            # Record retrieval trace
                            await tracer.record_step(
                                "retrieval",
                                latency_ms=int(retrieval_result.retrieval_latency_ms),
                                metadata={"top_k": self.settings.retrieval.top_k, "source_count": len(sources)},
                            )
                            await tracer.record_retrieval_trace(
                                original_query=message,
                                top_k=self.settings.retrieval.top_k,
                                vector_weight=1.0 - self.settings.retrieval.keyword_weight,
                                keyword_weight=self.settings.retrieval.keyword_weight,
                                rrf_constant=self.settings.retrieval.rrf_k,
                                retrieval_triggered=True,
                                trace_hits=getattr(retrieval_result, "trace_hits", []),
                                retrieval_latency_ms=retrieval_result.retrieval_latency_ms,
                            )
                    else:
                        retrieval_info = {"called": False, "reason": "no_engine_configured"}
                        await tracer.record_step("retrieval", status="skipped", metadata=retrieval_info)
                else:
                    retrieval_info = {
                        "called": False,
                        "reason": "path_does_not_need_retrieval",
                    }
                    await tracer.record_step("retrieval", status="skipped", metadata=retrieval_info)

                # =============================================
                # 9b. 实时教练观察 + 引导（Phase 4，默认关闭；失败不影响会话）
                #     coach_observe（在路由后）→ coach_guidance（在 execute_agent 前）
                #     引导文本会作为 prompt 上下文注入，并在最终回复中自然融入。
                # =============================================
                coach_guidance_text = ""
                _coach_obs_record: dict[str, Any] | None = None
                try:
                    from sales_agent.models.coach import CoachSettings, CoachRealtimeObservation
                    from sales_agent.coach.coach_observe import observe as coach_observe
                    from sales_agent.coach.coach_guidance import generate_guidance

                    rt_settings = (
                        await self.db.execute(
                            select(CoachSettings).where(
                                CoachSettings.tenant_id == tenant_id,
                                CoachSettings.agent_id == resolved_agent_id,
                            )
                        )
                    ).scalar_one_or_none()
                    realtime_enabled = bool(rt_settings and rt_settings.realtime_enabled)
                    guidance_limit = int(rt_settings.daily_realtime_guidance_limit) if rt_settings else 3

                    obs = coach_observe(
                        tenant_id=tenant_id, agent_id=resolved_agent_id, user_id=user_id,
                        message=message, recent_history=history_messages,
                        task_type=task_type, conversation_id=conversation_id,
                    )

                    # 当前六维分数（用于分数带决策）
                    from sales_agent.models.coach import CoachCompetencyScore
                    score_rows = (
                        await self.db.execute(
                            select(CoachCompetencyScore).where(
                                CoachCompetencyScore.tenant_id == tenant_id,
                                CoachCompetencyScore.agent_id == resolved_agent_id,
                                CoachCompetencyScore.user_id == user_id,
                            )
                        )
                    ).scalars().all()
                    scores_map = {r.dimension: int(r.score) for r in score_rows}

                    # 当日已用引导数（按当天 UTC 日期）
                    today_prefix = datetime.now(timezone.utc).date().isoformat()
                    used_today = int(
                        (
                            await self.db.execute(
                                select(func.count())
                                .select_from(CoachRealtimeObservation)
                                .where(
                                    CoachRealtimeObservation.tenant_id == tenant_id,
                                    CoachRealtimeObservation.agent_id == resolved_agent_id,
                                    CoachRealtimeObservation.user_id == user_id,
                                    CoachRealtimeObservation.applied_to_reply == True,  # noqa: E712
                                    CoachRealtimeObservation.created_at.like(f"{today_prefix}%"),
                                )
                            )
                        ).scalar() or 0
                    )

                    guidance = generate_guidance(
                        scene_hint=obs.scene_hint, scores=scores_map,
                        weak_dimensions=None, task_type=task_type, message=message,
                        daily_guidance_used=used_today, daily_guidance_limit=guidance_limit,
                        observe_confidence=obs.confidence,
                    ) if realtime_enabled else None

                    applied = bool(
                        realtime_enabled and guidance
                        and guidance.guidance_level != "suppressed"
                        and guidance.guidance_text
                    )
                    coach_guidance_text = guidance.guidance_text if applied else ""
                    _coach_obs_record = {
                        "scene_hint": obs.scene_hint,
                        "confidence": obs.confidence,
                        "observed_signals": obs.observed_signals,
                        "dimension_focus": guidance.dimension_focus if guidance else None,
                        "guidance_level": guidance.guidance_level if guidance else "suppressed",
                        "guidance_text": coach_guidance_text,
                        "applied": applied,
                        "suppressed_reason": guidance.suppressed_reason if guidance else "realtime_disabled",
                    }

                    # 记录实时观察（无论是否应用；agent_id 缺失时跳过）
                    if resolved_agent_id:
                        self.db.add(CoachRealtimeObservation(
                            tenant_id=tenant_id, agent_id=resolved_agent_id, user_id=user_id,
                            conversation_id=conversation_id, scene_hint=obs.scene_hint,
                            confidence=obs.confidence,
                            observed_signals_json=json.dumps(obs.observed_signals, ensure_ascii=False),
                            dimension_focus=(guidance.dimension_focus if guidance else None),
                            guidance_level=(guidance.guidance_level if guidance else "suppressed"),
                            guidance_text=coach_guidance_text,
                            applied_to_reply=applied,
                            suppressed_reason=(guidance.suppressed_reason if guidance else "realtime_disabled"),
                        ))
                except Exception as e:
                    logger.warning("Realtime coach guidance failed (non-fatal): %s", e)
                    coach_guidance_text = ""

                # =============================================
                # 9c. 联网搜索兜底：hybrid/rag/ontology 全部为空 → Bocha
                # =============================================
                if not sources and self.settings.web_search.enabled:
                    try:
                        from sales_agent.services.web_search import (
                            bocha_search, web_search_sources_to_context,
                        )
                        web_result = await bocha_search(
                            query=message,
                            api_key=self.settings.web_search.api_key,
                            top_n=self.settings.web_search.top_n,
                        )
                        if web_result.success and web_result.sources:
                            sources = web_result.sources
                            web_ctx = web_search_sources_to_context(web_result)
                            ontology_context_text = (
                                (ontology_context_text + "\n\n" + web_ctx).strip()
                                if ontology_context_text else web_ctx
                            )
                            retrieval_info["web_search_used"] = True
                            retrieval_info["web_search_source_count"] = len(sources)
                            logger.info(
                                "Web search fallback: %d sources for query=%s",
                                len(sources), message[:80],
                            )
                    except Exception as e:
                        logger.warning("Web search fallback failed: %s", e)

                # =============================================
                # 10. Agent 执行（知识图谱路径已在检索阶段预计算 answer_dict，
                #     通过 skip_generation 跳过 agent LLM 生成，保留图谱答案）
                # =============================================
                context_dict = context or {}
                if not skip_generation:
                    timings.start("generation")
                    tenant_style = tenant_config if isinstance(tenant_config, dict) else {}

                    # 把实时教练引导注入 execute_agent 上下文（仅在有引导文本时）
                    exec_context = dict(context_dict)
                    if coach_guidance_text:
                        exec_context["coach_guidance"] = coach_guidance_text

                    answer_dict = await execute_agent(
                        chat_model=_effective_chat,
                        task_type=task_type,
                        message=message,
                        context=exec_context,
                        retrieval_result=retrieval_result,
                        history_messages=history_messages,
                        tenant_style=tenant_style,
                        prompt_text=prompt_text,
                        system_prompt_text=system_prompt_text,
                        ontology_context=ontology_context_text,
                    )

                    # 标准化卡片类任务输出（访前作战卡、访后机会推进卡）
                    from sales_agent.services.output_normalizer import normalize_answer
                    answer_dict = normalize_answer(task_type, answer_dict)
                    timings.end("generation")
                    await tracer.record_step("generation", latency_ms=int(timings.stages.get("generation", 0)))

                    # 采集主生成阶段的 token 用量（在风险检查前保存，避免被覆盖）
                    _main_usage = getattr(_effective_chat, "last_usage", {}) or {}

                # =============================================
                # 11. 风险检查（分级）
                # =============================================
                timings.start("risk_check")
                answer_text = json.dumps(answer_dict, ensure_ascii=False)

                # 规则风险检查（必执行）
                risk_config = {}
                if isinstance(tenant_config, dict):
                    rp = tenant_config.get("risk_policy", {})
                    if isinstance(rp, dict):
                        risk_config = {f"default_{k}_action": v for k, v in rp.items()}
                # Agent 级风险策略覆盖 tenant 默认（按 key 合并）
                if agent_risk_config:
                    risk_config.update(agent_risk_config)

                rule_checker = RiskChecker(risk_config)
                risk_result = rule_checker.full_check(
                    message=message,
                    sources=sources,
                    tenant_id=tenant_id,
                    answer_text=answer_text,
                )

                # LLM 风险检查（条件化）
                if path_result.needs_llm_risk_check and risk_result.action != "block":
                    try:
                        from sales_agent.services.prompt_resolver_helper import (
                            resolve_risk_prompt,
                        )
                        risk_prompt = await resolve_risk_prompt(
                            self.db, tenant_id, resolved_agent_id
                        )
                        llm_risk = await rule_checker.check_llm_risk(
                            message=message,
                            answer_text=answer_text,
                            chat_model=_effective_chat,
                            risk_prompt=risk_prompt,
                        )
                        risk_result = merge_risk_results(risk_result, llm_risk)
                    except Exception as e:
                        logger.warning("LLM risk check failed, using rule result: %s", e)

                # 如果被 block，替换为安全建议
                # 注意：summary 和 sections 不要放相同内容，避免 format_text_output 输出两遍。
                if risk_result.action == "block":
                    _notice = risk_result.notice or "该请求涉及高风险承诺，已改为安全建议"
                    answer_dict = {
                        "summary": _notice,
                        "sections": [
                            {"title": "建议", "content": "请使用合规的销售表达，不要对外做出未确认的承诺。"},
                        ],
                    }
                timings.end("risk_check")
                await tracer.record_step(
                    "risk_check",
                    latency_ms=int(timings.stages.get("risk_check", 0)),
                    metadata={
                        "level": risk_result.level,
                        "action": risk_result.action,
                        "flags": risk_result.flags,
                    },
                )

            finally:
                # 取消处理中提示
                if notice_guard:
                    notice_guard.cancel()

            # =============================================
            # 12. 日志记录
            # =============================================
            timings.start("logging")
            runtime = get_tenant_runtime()
            safe_model_config = runtime.get_debug_info() if runtime else {}
            llm_calls = {
                "router": route_result.llm_router_called if hasattr(route_result, "llm_router_called") else False,
                "main": True,
                "risk": path_result.needs_llm_risk_check,
                "summary": False,
            }
            await conversation_logger.log_conversation(
                self.db,
                tenant_id=tenant_id,
                user_id=user_id,
                channel=channel,
                agent_id=resolved_agent_id,
                conversation_id=conversation_id,
                message=message,
                task_type=task_type,
                task_confidence=route_result.confidence,
                answer_dict=answer_dict,
                risk_dict=risk_result.to_dict(),
                sources=sources,
                model_config=safe_model_config,
                status="completed",
                stage_latency_ms=timings.to_dict(),
                llm_calls=llm_calls,
                path=path_result.path,
                path_reason=path_result.reason,
                retrieval_info=retrieval_info,
                stage=context_dict.get("stage") if context_dict else None,
            )
            timings.end("logging")
            await tracer.record_step("logging", latency_ms=int(timings.stages.get("logging", 0)))

            # =============================================
            # 13. 延迟统计
            # =============================================
            await self._record_stats(path_result.path, timings.total_ms)

            # 完成 run trace
            await tracer.complete_run(
                total_latency_ms=timings.total_ms,
                route_confidence=route_result.confidence,
            )

            return PipelineResult(
                answer_dict=answer_dict,
                sources=sources,
                risk_result=risk_result,
                route_result=route_result,
                path_result=path_result,
                timings=timings,
                conversation_id=conversation_id,
                run_id=tracer.run_id,
                usage=_main_usage,
            )

        except Exception as exc:
            # 记录失败的 run trace
            try:
                await tracer.fail_run(str(exc))
            except Exception:
                logger.warning("Failed to record run trace for exception: %s", exc)
            raise

    async def _record_stats(self, path: str, latency_ms: float) -> None:
        """记录延迟统计。"""
        try:
            collector = get_latency_stats_collector()
            await collector.record(path, latency_ms)
        except Exception:
            pass  # 统计失败不影响主流程


async def _load_recent_history(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    settings: Settings,
) -> list[dict[str, str]]:
    """从 DB 加载最近 N 轮历史消息。"""
    history_turns = settings.conversation.history_turns
    limit = history_turns * 2

    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    messages = list(reversed(messages))

    return [{"role": m.role, "content": m.content} for m in messages]
