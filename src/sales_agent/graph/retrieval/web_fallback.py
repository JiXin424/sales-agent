"""Web 兜底 + 独立分析：ontology+rag 都空时调 Bocha，LLM 分析后返回 analysis 文本。

analysis 文本拼进 ontology_context_text，由 generate 节点随 retrieval_content 使用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.runtime import Runtime

from sales_agent.services.web_search import bocha_search, web_search_sources_to_context

logger = logging.getLogger(__name__)


async def web_fallback_and_analyze(
    *,
    message: str,
    tenant_id: str,
    runtime: Runtime,
    api_key: str,
    top_n: int = 5,
) -> dict | None:
    """调 Bocha 搜索 + LLM 分析，返回拼好 analysis 的 context dict。

    Args:
        message: 用户问题，作为搜索 query。
        tenant_id: 租户 ID（用于解析 web_analysis prompt）。
        runtime: LangGraph runtime，需 context 含 chat_model。
        api_key: Bocha API key，为空则跳过。
        top_n: 搜索结果数。

    Returns:
        {"ontology_context_text": str, "sources": list, "web_used": True} 或 None
        （未启用/搜索失败/无结果时返回 None）。
    """
    if not api_key:
        return None

    web_result = await bocha_search(query=message, api_key=api_key, top_n=top_n)
    if not web_result.success or not web_result.sources:
        logger.info("Web fallback: no sources for query=%s", message[:80])
        return None

    # 拼搜索结果文本供 LLM 分析
    search_text = web_search_sources_to_context(web_result)

    # 解析 web_analysis prompt（三级回退：DB active → 内置默认）
    from sales_agent.services.prompt_registry import PromptRegistry
    db = runtime.context.get("db")
    template = None
    if db is not None:
        registry = PromptRegistry(db)
        try:
            template = await registry.resolve_prompt("web", "web_analysis", tenant_id)
        except ValueError:
            template = None
    if template is None:
        from sales_agent.prompts.web_analysis_prompt import WEB_ANALYSIS_PROMPT
        template = WEB_ANALYSIS_PROMPT

    rendered = template.format(search_results=search_text)

    chat_model = runtime.context.get("chat_model")
    analysis_text = ""
    if chat_model is not None:
        try:
            raw = await chat_model.generate(
                messages=[{"role": "user", "content": rendered}],
                temperature=0.2,
                max_tokens=800,
            )
            parsed = json.loads(raw)
            analysis_text = parsed.get("analysis", raw)
        except Exception as e:
            logger.warning("Web analysis LLM failed: %s", e)
            analysis_text = web_result.raw_answer or search_text[:500]
    else:
        analysis_text = web_result.raw_answer or search_text[:500]

    context_text = "## 联网搜索分析\n" + analysis_text
    return {
        "ontology_context_text": context_text,
        "sources": [{"source_type": "web", **s} for s in web_result.sources],
        "web_used": True,
    }
