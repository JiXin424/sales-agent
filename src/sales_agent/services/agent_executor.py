"""Agent Executor：根据任务类型编排 prompt 和模型调用。"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from deepeval.tracing import observe

from sales_agent.llm.base import ChatModel
from sales_agent.prompts.system import SYSTEM_CONSTRAINT
from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS
from sales_agent.services.retriever import RetrievalResult

logger = logging.getLogger(__name__)

# task 类默认 prompt，从 prompt_defaults 注册表派生（single source of truth）。
# 兼容旧代码 ``from agent_executor import _TASK_PROMPTS``。
_TASK_PROMPTS = {b.key: b.template for b in BUILTIN_PROMPTS if b.category == "task"}
# task_type 未注册时的兜底 prompt
_DEFAULT_TASK_PROMPT = _TASK_PROMPTS.get("general_sales_coaching", "")


def _build_context_block(context: dict[str, Any] | None) -> str:
    """构建上下文信息块。"""
    if not context:
        return ""
    lines = ["## 用户提供的上下文"]
    for key, value in context.items():
        if not value:
            continue
        if key == "coach_guidance":
            # 实时教练引导：单独融合指令，不作为普通上下文展示
            continue
        label_map = {
            "industry": "客户行业",
            "product": "产品",
            "tone": "期望语气",
            "stage": "销售阶段",
        }
        label = label_map.get(key, key)
        lines.append(f"- {label}：{value}")

    # 实时教练引导融合指令（Phase 4）：自然融入 1-2 句，不暴露内部评分/维度。
    coach_guidance = context.get("coach_guidance")
    if coach_guidance:
        lines.append("")
        lines.append("## 教练融合")
        lines.append(
            "如存在下面的教练引导，请在回答末尾自然融入 1-2 句销售建议。"
            "不要暴露用户评分、等级、内部维度名或后台分析字段。"
            "不要使用系统检测到这类表达。"
        )
        lines.append(f"教练引导：{coach_guidance}")
    return "\n".join(lines)


def _build_retrieval_block(retrieval_result: RetrievalResult | None) -> str:
    """构建检索结果块。"""
    if not retrieval_result or not retrieval_result.has_results:
        return ""

    lines = ["## 检索到的企业知识库内容"]
    for i, source in enumerate(retrieval_result.sources, 1):
        lines.append(f"### 来源 {i}：《{source.title}》- {source.section_title}")
        lines.append(source.text[:500])  # 限制长度
        lines.append("")

    return "\n".join(lines)


def _build_retrieval_content(retrieval_result: RetrievalResult | None) -> str:
    """构建检索内容（knowledge_qa 和 objection_handling 专用）。"""
    if not retrieval_result or not retrieval_result.has_results:
        return "（未检索到相关企业知识库内容）"

    lines = []
    for i, source in enumerate(retrieval_result.sources, 1):
        lines.append(f"### 文档 {i}：《{source.title}》/ {source.section_title}")
        lines.append(source.text)
        lines.append("")

    return "\n".join(lines)


def _parse_json_response(raw: str) -> dict[str, Any]:
    """从模型输出中解析 JSON。"""
    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 代码块
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个完整的 JSON 对象
    brace_count = 0
    start = -1
    for i, ch in enumerate(raw):
        if ch == "{":
            if brace_count == 0:
                start = i
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0 and start >= 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    start = -1

    # 解析失败，返回兜底结构
    # 注意：不要同时在 summary 和 sections 里放相同内容，否则 format_text_output
    # 会输出两遍相同文字（summary 一遍 + "回答：" 标题下再一遍）。
    logger.warning("Failed to parse JSON from model output, returning raw as summary")
    return {
        "summary": raw,
        "sections": [],
    }


@observe(type="llm")
async def execute_agent(
    chat_model: ChatModel,
    task_type: str,
    message: str,
    context: dict[str, Any] | None = None,
    retrieval_result: RetrievalResult | None = None,
    history_messages: list[dict[str, str]] | None = None,
    tenant_style: dict[str, Any] | None = None,
    prompt_text: str | None = None,
    system_prompt_text: str | None = None,
) -> dict[str, Any]:
    """执行 Agent：构建 prompt，调用模型，返回结构化回答。

    Args:
        chat_model: 聊天模型
        task_type: 任务类型
        message: 用户消息
        context: 用户提供的上下文
        retrieval_result: RAG 检索结果
        history_messages: 多轮历史消息
        tenant_style: 租户话术风格配置
        prompt_text: 运行时解析的 prompt 模板文本（可选）。
            为 None 时回退到 _TASK_PROMPTS 静态映射。

    Returns:
        解析后的结构化回答 dict（包含 summary 和 sections）
    """
    # 1. 构建消息列表
    messages = _build_messages(
        task_type=task_type,
        message=message,
        context=context,
        retrieval_result=retrieval_result,
        history_messages=history_messages,
        tenant_style=tenant_style,
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )

    # 2. 调用模型
    start_time = time.time()
    raw_response = await chat_model.generate(
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
    )
    latency_ms = int((time.time() - start_time) * 1000)
    logger.info("Agent execution completed in %d ms for task %s", latency_ms, task_type)

    # 7. 解析 JSON 响应
    parsed = _parse_json_response(raw_response)

    # 确保必要字段存在
    if "summary" not in parsed:
        parsed["summary"] = ""
    if "sections" not in parsed:
        parsed["sections"] = [{"title": "回答", "content": raw_response}]

    return parsed


def _build_messages(
    task_type: str,
    message: str,
    context: dict[str, Any] | None = None,
    retrieval_result: RetrievalResult | None = None,
    history_messages: list[dict[str, str]] | None = None,
    tenant_style: dict[str, Any] | None = None,
    prompt_text: str | None = None,
    system_prompt_text: str | None = None,
) -> list[dict[str, str]]:
    """构建发送给模型的消息列表。

    被 :func:`execute_agent` 和 :func:`stream_execute_agent` 共用。

    Args:
        prompt_text: 运行时解析的 task prompt 模板；None 时回退到 _TASK_PROMPTS。
        system_prompt_text: 运行时解析的 system prompt；None 时回退到 SYSTEM_CONSTRAINT。
    """
    # 1. 获取 prompt 模板：优先运行时解析的模板，否则回退到默认映射
    template = prompt_text or _TASK_PROMPTS.get(task_type, _DEFAULT_TASK_PROMPT)

    # 2. 构建上下文块
    context_block = _build_context_block(context)

    # 3. 构建检索块
    if task_type in ("knowledge_qa", "objection_handling"):
        retrieval_block = ""
        retrieval_content = _build_retrieval_content(retrieval_result)
    else:
        retrieval_block = _build_retrieval_block(retrieval_result)
        retrieval_content = ""

    # 4. 填充模板
    user_prompt = template.format(
        message=message,
        context_block=context_block,
        retrieval_block=retrieval_block,
        retrieval_content=retrieval_content,
    )

    # 5. 构建消息列表
    messages = [{"role": "system", "content": system_prompt_text or SYSTEM_CONSTRAINT}]

    # 加入租户风格
    if tenant_style:
        forbid_words = tenant_style.get("forbid_words", [])
        if forbid_words:
            messages[0]["content"] += f"\n\n## 企业禁用表达\n{', '.join(forbid_words)}"

    # 加入历史消息
    if history_messages:
        messages.extend(history_messages)

    # 加入当前用户消息
    messages.append({"role": "user", "content": user_prompt})

    return messages


async def stream_execute_agent(
    chat_model: ChatModel,
    task_type: str,
    message: str,
    context: dict[str, Any] | None = None,
    retrieval_result: RetrievalResult | None = None,
    history_messages: list[dict[str, str]] | None = None,
    tenant_style: dict[str, Any] | None = None,
    prompt_text: str | None = None,
    system_prompt_text: str | None = None,
) -> AsyncIterator[str]:
    """流式执行 Agent：构建 prompt，流式调用模型，yield 原始文本块。

    与 :func:`execute_agent` 的区别：
    - 使用 ``stream_generate`` 逐 token 返回
    - 不做 JSON 解析，直接 yield 原始文本
    - 调用方负责收集完整文本后自行解析

    Args:
        chat_model: 聊天模型（需支持 stream_generate）
        task_type: 任务类型
        message: 用户消息
        context: 用户提供的上下文
        retrieval_result: RAG 检索结果
        history_messages: 多轮历史消息
        tenant_style: 租户话术风格配置
        prompt_text: 运行时解析的 prompt 模板文本（可选）。

    Yields:
        模型输出的原始文本 chunk
    """
    messages = _build_messages(
        task_type=task_type,
        message=message,
        context=context,
        retrieval_result=retrieval_result,
        history_messages=history_messages,
        tenant_style=tenant_style,
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )

    start_time = time.time()
    chunk_count = 0

    async for chunk in chat_model.stream_generate(
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
    ):
        chunk_count += 1
        yield chunk

    latency_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "Stream agent execution completed in %d ms, %d chunks for task %s",
        latency_ms, chunk_count, task_type,
    )
