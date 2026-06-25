"""钉钉流式处理管道 — 互动卡片 + LLM streaming。

完整流程：
  1. 发送初始卡片（"思考中..."）
  2. 执行管道前半段（验证、路由、RAG）— 同步等待
  3. 流式 Agent 执行 + 节流更新卡片
  4. 最终更新（完整渲染 + 来源 + 风险提示）
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import Settings
from sales_agent.core.tenant_runtime import TenantRuntime
from sales_agent.integrations.dingtalk.card_sender import (
    DingTalkCardSender,
    StreamUpdateThrottle,
)
from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.message_renderer import DingTalkMessageRenderer
from sales_agent.services.agent_executor import stream_execute_agent, _parse_json_response
from sales_agent.services.request_validator import validate_chat_request
from sales_agent.services.risk_checker import RiskChecker
from sales_agent.services.task_router import route_task
from sales_agent.services.retriever import Retriever
from sales_agent.services.tenant_resolver import TenantResolver
from sales_agent.services import conversation_logger
from sales_agent.services.response_formatter import format_text_output, format_sales_visible_sources
from sales_agent.services.chat_pipeline import _load_recent_history

logger = logging.getLogger(__name__)


async def handle_dingtalk_streaming(
    db: AsyncSession,
    config: DingTalkConfig,
    settings: Settings,
    runtime: TenantRuntime,
    card_sender: DingTalkCardSender,
    *,
    event_id: str,
    corp_id: str,
    sender_id: str,
    sender_name: str,
    message: str,
    message_id: str,
    dingtalk_conversation_id: str,
    user_id: str,
    conversation_id: str,
) -> None:
    """处理钉钉消息，使用流式卡片输出。

    Args:
        db: 数据库 session
        config: 钉钉配置
        settings: 全局设置
        runtime: 租户运行时
        card_sender: 卡片发送器
        event_id: 事件 ID
        corp_id: 企业 ID
        sender_id: 钉钉用户 ID
        sender_name: 发送者昵称
        message: 消息文本
        message_id: 钉钉消息 ID
        dingtalk_conversation_id: 钉钉会话 ID
        user_id: 内部用户 ID
        conversation_id: 内部会话 ID
    """
    start_time = time.time()
    dingtalk_user_id = sender_id
    tenant_id = runtime.tenant_id

    # =============================================
    # 0. 快捷入口多轮对话拦截（小赢欣赏 / 卡点破框）
    # =============================================
    # 该钉钉用户若有活跃的多轮会话，直接推进状态机并回复，不走
    # route_task / LLM 流式（与 Coach 报告拦截同理）。会话状态落库
    # quick_sessions，由 whoami 点击按钮时创建。
    try:
        from sales_agent.coach.quick_session import (
            advance_active_session,
            has_active_or_recent_session,
        )
        # 只在确有快捷会话时才解析模型（卡点破框出卡需 LLM 合成高质量内容）
        _qs_chat = None
        if await has_active_or_recent_session(db, tenant_id, dingtalk_user_id):
            try:
                _resolver = TenantResolver(db)
                _tinfo = await _resolver.resolve(tenant_id)
                _mp = _resolver.get_model_provider(_tinfo)
                _qs_chat = _mp.chat if _mp is not None else None
            except Exception as _e:
                logger.warning("Quick-session model resolve failed (use rules): %s", _e)
        _qs_result = await advance_active_session(
            db,
            tenant_id=tenant_id,
            external_user_id=dingtalk_user_id,
            user_text=message,
            chat_model=_qs_chat,
        )
        if _qs_result is not None:
            _qs_reply, _qs_completed = _qs_result
            try:
                await db.commit()
            except Exception as _e:
                logger.warning("Quick-session commit failed: %s", _e)
            try:
                _qs_card_id = await card_sender.send_markdown_card(
                    dingtalk_user_id=dingtalk_user_id,
                    title="销售教练",
                    markdown_text=_qs_reply,
                )
                # send_markdown_card 开的是 STREAM 流式卡片，必须 finalize
                # 关闭「生成中」指示器，否则卡片永远停在「思考中」。
                await card_sender.streaming_finalize(_qs_card_id, _qs_reply)
            except Exception as _e:
                logger.warning("Quick-session reply card failed: %s", _e)
            # 留痕（best-effort，不影响主流程）
            try:
                await conversation_logger.log_conversation(
                    db, tenant_id=tenant_id, user_id=user_id,
                    channel="dingtalk_streaming", conversation_id=conversation_id,
                    message=message, task_type="quick_session",
                    answer_dict={"reply": _qs_reply, "completed": _qs_completed},
                    status="completed",
                )
                await db.commit()
            except Exception as _e:
                logger.warning("Quick-session log failed: %s", _e)
            logger.info(
                "DingTalk quick-session handled: user=%s completed=%s",
                dingtalk_user_id, _qs_completed,
            )
            return
    except Exception as e:
        logger.warning("Quick-session intercept failed, fallback to pipeline: %s", e)

    # =============================================
    # 1. 发送初始卡片（"思考中..."）
    # =============================================
    try:
        card_id = await card_sender.send_markdown_card(
            dingtalk_user_id=dingtalk_user_id,
            title="销售助手",
            markdown_text="🤔 正在结合资料思考...",
        )
    except Exception as e:
        logger.error("Failed to send initial card: %s, falling back", e)
        # 降级：直接返回，让调用方走非流式路径
        raise

    # =============================================
    # 2. 管道前半段（同步）
    # =============================================
    try:
        # 请求校验
        validate_chat_request(tenant_id, user_id, message)

        # 租户解析
        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(tenant_id)
        tenant_config = tenant_info.get("config", {})
        model_provider = resolver.get_model_provider(tenant_info)

        # Agent 解析（与 ChatPipeline/processor 一致：回退 tenant 默认 Agent）。
        # streaming 路径独立于 ChatPipeline，必须自行解析 agent_id 并传入日志，
        # 否则 conversation_messages.agent_id 为 NULL，Coach run_daily 查不到该用户对话。
        from sales_agent.services.agent_service import resolve_tenant_agent_id
        try:
            agent_id = (await resolve_tenant_agent_id(db, tenant_id, None)).id
        except Exception as e:
            logger.warning("Streaming agent resolution failed: %s", e)
            agent_id = None

        # Coach 报告意图拦截（与 ChatPipeline step 3c 一致；命中则直接渲染报告，
        # 不走 route_task / 流式生成 / 风控）。否则钉钉流式路径永远看不到 Coach 报告。
        from sales_agent.coach.intent_router import match_coach_intent
        try:
            _coach_report_type = match_coach_intent(message)
        except Exception as e:
            logger.warning("Coach intent match failed in streaming: %s", e)
            _coach_report_type = None

        if _coach_report_type is not None and agent_id:
            from sales_agent.coach.report_service import CoachReportService
            from sales_agent.services.risk_checker import RiskCheckResult
            try:
                report = await CoachReportService(db).render_report(
                    tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
                    report_type=_coach_report_type, query_text=message,
                )
                renderer = DingTalkMessageRenderer(config)
                report_text = renderer.render(report, [], RiskCheckResult())
                try:
                    await card_sender.streaming_finalize(card_id, report_text)
                except Exception:
                    await card_sender.update_card(card_id, report_text)
                await conversation_logger.log_conversation(
                    db, tenant_id=tenant_id, user_id=user_id,
                    channel="dingtalk_streaming", conversation_id=conversation_id,
                    message=message, task_type="coach_report",
                    answer_dict=report, status="completed", agent_id=agent_id,
                )
                logger.info(
                    "DingTalk coach report rendered: type=%s user=%s",
                    _coach_report_type, user_id,
                )
                return
            except Exception as e:
                logger.warning("Coach report rendering failed, fallback to normal: %s", e)

        # 任务路由
        route_result = await route_task(
            message=message,
            chat_model=model_provider.chat,
        )
        task_type = route_result.task_type
        needs_retrieval = route_result.needs_retrieval

        # 风险预检
        risk_checker = RiskChecker()
        input_risk = risk_checker.check_input(message)

        # RAG 检索
        sources: list[dict] = []
        retrieval_result = None
        if needs_retrieval or task_type == "knowledge_qa":
            retriever = Retriever(db, model_provider.embedding)
            retrieval_result = await retriever.retrieve_for_task(
                tenant_id=tenant_id,
                message=message,
                task_type=task_type,
                needs_retrieval=needs_retrieval,
            )
            sources = [s.to_source_item() for s in retrieval_result.sources]

    except Exception as e:
        logger.error("Pipeline pre-stage failed: %s", e, exc_info=True)
        # 流式卡片必须用 streaming_finalize 终止，否则钉钉端一直显示"思考中"
        try:
            await card_sender.streaming_update(card_id, "❌ 处理失败，请稍后再试。")
            await card_sender.streaming_finalize(card_id, "❌ 处理失败，请稍后再试。")
        except Exception:
            # 兜底：尝试普通更新
            try:
                await card_sender.update_card(card_id, "❌ 处理失败，请稍后再试。")
            except Exception:
                pass
        return

    # =============================================
    # 3. 流式 Agent 执行 + 节流更新卡片
    # =============================================
    throttle = StreamUpdateThrottle(
        interval_ms=config.stream_update_interval_ms,
        min_chars=config.stream_min_chunk_chars,
    )

    # 加载会话历史（复用 ChatPipeline 的逻辑）
    history_messages = await _load_recent_history(
        db, tenant_id, user_id, conversation_id, settings,
    )

    raw_buffer = ""
    # 三层降级：streaming API → card update → 最终兜底
    use_streaming_api = True
    # 解析 task + system prompt（接入 DB 版本管理；钉钉链路此前直接用代码常量）
    from sales_agent.services.prompt_resolver_helper import resolve_execution_prompts
    try:
        _task_prompt, _system_prompt = await resolve_execution_prompts(
            db, agent_id, tenant_id, task_type
        )
    except Exception as e:
        logger.warning("Streaming prompt resolve failed, fallback to builtin: %s", e)
        _task_prompt, _system_prompt = None, None
    try:
        async for chunk in stream_execute_agent(
            chat_model=model_provider.chat,
            task_type=task_type,
            message=message,
            context=None,
            retrieval_result=retrieval_result,
            history_messages=history_messages,
            tenant_style=tenant_config if isinstance(tenant_config, dict) else {},
            prompt_text=_task_prompt,
            system_prompt_text=_system_prompt,
        ):
            raw_buffer += chunk
            if throttle.should_update(raw_buffer):
                streaming_text = _render_streaming_text(raw_buffer)
                try:
                    if use_streaming_api:
                        await card_sender.streaming_update(card_id, streaming_text)
                    else:
                        await card_sender.update_card(card_id, streaming_text)
                except Exception as e:
                    logger.warning("Card update failed (streaming=%s): %s", use_streaming_api, e)
                    if use_streaming_api:
                        # 降级到普通卡片更新
                        use_streaming_api = False
                        try:
                            await card_sender.update_card(card_id, streaming_text)
                        except Exception as e2:
                            logger.warning("Card fallback update also failed: %s", e2)
                throttle.mark_updated(len(raw_buffer))

    except Exception as e:
        logger.error("Stream execution failed: %s", e, exc_info=True)
        try:
            await card_sender.update_card(card_id, "❌ 生成过程中断，请重新提问。")
        except Exception:
            pass
        return

    # =============================================
    # 4. 后处理：解析 JSON + 风险检查 + 最终渲染
    # =============================================
    try:
        # 解析完整响应（先剥离推理模型的 <think> 标签，避免污染 JSON 解析）
        answer_dict = _parse_json_response(_strip_think(raw_buffer))

        # 风险检查
        answer_text = json.dumps(answer_dict, ensure_ascii=False)
        risk_result = risk_checker.full_check(
            message=message,
            sources=sources,
            tenant_id=tenant_id,
            answer_text=answer_text,
        )

        # 如果被 block，替换为安全建议
        if risk_result.action == "block":
            answer_dict = {
                "summary": risk_result.notice or "该请求涉及高风险承诺，已改为安全建议",
                "sections": [
                    {"title": "安全提示", "content": risk_result.notice},
                    {"title": "建议", "content": "请使用合规的销售表达，不要对外做出未确认的承诺。"},
                ],
            }

        # 最终渲染
        renderer = DingTalkMessageRenderer(config)
        final_text = renderer.render(answer_dict, sources, risk_result)

        # 最终更新卡片
        try:
            if use_streaming_api:
                await card_sender.streaming_finalize(card_id, final_text)
            else:
                await card_sender.update_card(card_id, final_text)
        except Exception as e:
            logger.warning("Final card update failed (streaming=%s): %s", use_streaming_api, e)
            try:
                await card_sender.update_card(card_id, final_text)
            except Exception:
                pass

        # 日志记录
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "DingTalk streaming completed: event=%s user=%s task=%s latency=%dms",
            event_id, dingtalk_user_id, task_type, latency_ms,
        )

        await conversation_logger.log_conversation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            channel="dingtalk_streaming",
            conversation_id=conversation_id,
            message=message,
            task_type=task_type,
            task_confidence=route_result.confidence,
            answer_dict=answer_dict,
            risk_dict=risk_result.to_dict(),
            sources=sources,
            status="completed",
            agent_id=agent_id,
        )

    except Exception as e:
        logger.error("Post-processing failed: %s", e, exc_info=True)
        # 尽量展示原始内容
        try:
            fallback = _render_streaming_text(raw_buffer) if raw_buffer else "❌ 处理完成但渲染失败。"
            await card_sender.update_card(card_id, fallback)
        except Exception:
            pass


# 流式过程中没有任何可读内容时的占位文案（保持与初始卡片"思考中"语义一致）。
_STREAMING_PLACEHOLDER = "🤔 正在生成回答..."

# 思考标签：<think>...</think>（已完成）与未闭合的 <think>...（仍在思考）。
# 部分推理模型会把思考过程混进 content 字段，必须对用户隐藏。
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_DANGLING_RE = re.compile(r"<think>.*", re.DOTALL)

# 增量提取 summary / title / content 的字符串值，支持不完整的尾部（无闭合引号）。
# 不在末尾要求闭合 `"`：`(?:\\.|[^"\\])*` 跳过转义引号（\"），字符串未结束时
# 一直匹配到缓冲区末尾；已闭合时停在引号前。
_FIELD_VALUE_RE = re.compile(r'"(summary|title|content)"\s*:\s*"((?:\\.|[^"\\])*)')


def _strip_think(text: str) -> str:
    """剥离推理过程的 <think> 标签内容。先删完整块，再删未闭合的悬空标签。"""
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_DANGLING_RE.sub("", text)
    return text


def _strip_code_fence(text: str) -> str:
    """去掉 markdown 代码块包裹。"""
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1] if "\n" in clean else clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


def _unescape_json_string(body: str) -> str:
    """对（可能不完整的）JSON 字符串体做反转义，用于流式展示。"""
    try:
        return json.loads('"' + body + '"')
    except (json.JSONDecodeError, ValueError):
        # 末尾可能截断在转义序列中间，退化为常见转义的手动替换。
        return (
            body.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\/", "/")
        )


def _render_streaming_text(raw: str) -> str:
    """将流式累积的原始文本渲染为用户可读的 Markdown。

    流式阶段 LLM 输出是**不完整的 JSON**（外层 ``}`` 要到最后才闭合，
    ``json.loads`` / ``raw_decode`` 在几乎整个流式过程中都会失败）。旧实现
    此时会把原始 JSON（``{"summary": "...","sections":[{"title"...``）原样
    显示，用户看到的就像"思考过程/内部结构"在打字。

    本实现用正则**增量提取** summary 与各 section 的字符串值，逐段浮现成
    可读 Markdown（summary 粗体在前，section 标题 + 正文随后），绝不泄露
    JSON 键名与大括号。推理模型的 ``<think>`` 内容也被剥离。
    """
    clean = _strip_code_fence(_strip_think(raw))

    fields = [
        (m.group(1), _unescape_json_string(m.group(2)))
        for m in _FIELD_VALUE_RE.finditer(clean)
    ]

    blocks: list[str] = []

    summary = next((v for k, v in fields if k == "summary"), None)
    if summary:
        blocks.append(f"**{summary}**")

    # section：按 title→content 相邻顺序还原段落，title 缺失时仅展示正文。
    pending_title: str | None = None
    for key, value in fields:
        if key == "title":
            pending_title = value
        elif key == "content":
            title = pending_title or ""
            blocks.append(f"### {title}\n{value}" if title else value)
            pending_title = None

    if blocks:
        return "\n\n".join(blocks) + " ▍"

    return _STREAMING_PLACEHOLDER
