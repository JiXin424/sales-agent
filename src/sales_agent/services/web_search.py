"""Bocha 联网搜索兜底——当 ontology + RAG 都为空时调用。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Bocha 官方 API（飞书文档: bocha-ai.feishu.cn/wiki/HmtOw1z6vik14Fkdu5uc9VaInBb）
_BOCHA_BASE = "https://api.bocha.cn/v1"
_BOCHA_AI_SEARCH = f"{_BOCHA_BASE}/ai-search"
_BOCHA_WEB_SEARCH = f"{_BOCHA_BASE}/web-search"


@dataclass
class WebSearchResult:
    """联网搜索结果。"""

    query: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    raw_answer: str = ""
    success: bool = False
    error: str = ""


async def bocha_search(query: str, api_key: str, top_n: int = 5) -> WebSearchResult:
    """调用 Bocha AI Search API 进行联网搜索。

    Args:
        query: 搜索查询
        api_key: Bocha API key (sk-...)
        top_n: 返回结果数（默认 5）

    Returns:
        WebSearchResult: 搜索结果，包含 sources 列表和 raw_answer
    """
    if not api_key or not api_key.startswith("sk-"):
        return WebSearchResult(query=query, error="Invalid or missing API key", success=False)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                _BOCHA_WEB_SEARCH,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "count": top_n,
                    "summary": True,
                    "freshness": "noLimit",
                },
            )
            if resp.status_code != 200:
                logger.warning("Bocha API returned %d: %s", resp.status_code, resp.text[:300])
                return WebSearchResult(
                    query=query,
                    error=f"Bocha API error {resp.status_code}",
                    success=False,
                )

            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("Bocha API timeout for query: %s", query[:100])
        return WebSearchResult(query=query, error="Timeout", success=False)
    except Exception as e:
        logger.warning("Bocha API call failed: %s", e)
        return WebSearchResult(query=query, error=str(e), success=False)

    # 解析 Bocha Web Search 返回格式
    # 官方格式: {code, data: {webPages: {value: [{name,url,snippet,summary,siteName,datePublished}]}}}
    web_result = WebSearchResult(query=query, success=True)

    if isinstance(data, dict):
        inner = data.get("data") or data  # 先解包外层 data 字段
        web_result.raw_answer = inner.get("answer") or inner.get("summary") or ""

        # 优先 data.webPages.value，兼容多种格式
        pages_container = inner.get("webPages") or inner
        pages = (
            pages_container.get("value", [])
            if isinstance(pages_container, dict)
            else inner.get("results") or inner.get("data") or []
        )
        if isinstance(pages, list):
            for p in pages[:top_n]:
                if isinstance(p, dict):
                    web_result.sources.append({
                        "document_id": p.get("url", p.get("id", "")),
                        "title": p.get("name") or p.get("title", "联网搜索"),
                        "display_title": p.get("name") or p.get("title", "联网搜索"),
                        "score": p.get("score", p.get("relevance", 0.5)),
                        "source_type": "web_search",
                        "text": (
                            p.get("summary") or p.get("snippet")
                            or p.get("contents") or p.get("description", "")
                        )[:2000],
                        "url": p.get("url", ""),
                        "site_name": p.get("siteName", ""),
                        "date": p.get("datePublished", ""),
                    })
    elif isinstance(data, list):
        for p in data[:top_n]:
            if isinstance(p, dict):
                web_result.sources.append({
                    "document_id": p.get("url", ""),
                    "title": p.get("name") or p.get("title", "联网搜索"),
                    "display_title": p.get("name") or p.get("title", "联网搜索"),
                    "score": 0.5,
                    "source_type": "web_search",
                    "text": (p.get("summary") or p.get("snippet", ""))[:2000],
                    "url": p.get("url", ""),
                })

    logger.info(
        "Bocha web search: query=%s, sources=%d, answer_len=%d",
        query[:100], len(web_result.sources), len(web_result.raw_answer),
    )
    return web_result


def web_search_sources_to_context(search_result: WebSearchResult) -> str:
    """将联网搜索结果格式化为 LLM 上下文。"""
    if not search_result.success or not search_result.sources:
        return ""

    lines = ["## 联网搜索结果（Bocha Web Search）", ""]

    if search_result.raw_answer:
        lines.append(f"**AI 摘要**：{search_result.raw_answer[:500]}")
        lines.append("")

    lines.append(f"**网页来源** ({len(search_result.sources)} 个)：")
    for i, s in enumerate(search_result.sources, 1):
        title = s.get("title", "?")
        url = s.get("url", "")
        text = s.get("text", "")
        lines.append(f"\n### 来源 {i}：{title}")
        if url:
            lines.append(f"URL: {url}")
        if text:
            lines.append(f"> {text[:500]}")
        lines.append("")

    return "\n".join(lines)
