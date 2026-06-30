"""
测试用例构建器 —— 从本地数据文件解析问题，调 Agent API 获取回答，构建 DeepEval LLMTestCase。

数据来源：
1. eval/questions.md —— 126 题（80 题有参考答案 + 46 题纯问题）
2. eval/ground_truth_30q.json —— 30 题，有分类标注和关键词
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from deepeval.test_case import LLMTestCase

# ── 数据模型 ────────────────────────────────────────────────────────


@dataclass
class QuestionItem:
    """单条待评估问题，汇总了所有数据源的元信息。"""

    id: str                          # 唯一标识，如 "qa_001" / "md_Q01" / "gt_01"
    text: str                        # 问题文本
    reference: str = ""              # 参考答案（可能为空）
    expected_keywords: list[str] = field(default_factory=list)
    category: str = ""               # 场景类别
    task_type: str = ""              # 预期任务类型
    source: str = ""                 # 数据来源文件名
    has_reference: bool = False      # 是否有参考答案


@dataclass
class AgentResponse:
    """Agent HTTP API 的返回（提取了评估需要的字段）。"""

    answer_text: str = ""
    summary: str = ""
    sections: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    task_type: str = ""
    risk_level: str = ""
    risk_flags: list[str] = field(default_factory=list)
    latency_ms: int = 0
    ttft_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str = ""


# ── 文件解析 ────────────────────────────────────────────────────────


def _get_eval_dir() -> Path:
    return Path(__file__).resolve().parent


def parse_questions_markdown(path: str | Path | None = None) -> list[QuestionItem]:
    """解析 questions.md 格式（合并版）。

    格式：``## Qxx`` 后跟问题，可选 ``## Axx`` 后跟参考答案。
    第一部分（Q01-Q80）有参考答案，第二部分（Q81+）无参考答案。
    """
    if path is None:
        path = _get_eval_dir() / "questions.md"

    text = Path(path).read_text(encoding="utf-8")
    pattern = r"##\s+([QA]\d+)\s*\n+(.+?)(?=\n##\s+[QA]\d+|\Z)"
    matches = re.findall(pattern, text, re.DOTALL)

    q_blocks: dict[str, str] = {}
    a_blocks: dict[str, str] = {}
    for tag, body in matches:
        body = body.strip()
        if tag.startswith("Q") and body:
            q_blocks[tag] = body
        elif tag.startswith("A") and body:
            a_blocks[tag] = body

    items: list[QuestionItem] = []
    for qid in sorted(q_blocks.keys(), key=lambda x: int(x[1:])):
        ref_id = f"A{qid[1:]}"
        ref = a_blocks.get(ref_id, "")
        items.append(QuestionItem(
            id=f"Q{qid[1:]}",
            text=q_blocks[qid],
            reference=ref,
            has_reference=bool(ref),
            source="questions.md",
        ))
    return items


def parse_ground_truth_json(path: str | Path | None = None) -> list[QuestionItem]:
    """解析 ground_truth_30q.json 格式。

    JSON 结构：{"questions": [{"q": "...", "category": "...", ...}, ...]}
    """
    if path is None:
        path = _get_eval_dir() / "ground_truth_30q.json"

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions: list[dict] = data.get("questions", [])

    items: list[QuestionItem] = []
    for i, q in enumerate(questions, 1):
        items.append(QuestionItem(
            id=f"gt_{i:02d}",
            text=q.get("q", ""),
            expected_keywords=q.get("expected_keywords", []),
            category=q.get("category", ""),
            task_type=q.get("task_type", ""),
            source="ground_truth_30q.json",
            has_reference=False,  # ground truth 有关键词但无完整参考答案
        ))
    return items


# ── 问题集汇总 ─────────────────────────────────────────────────────


def load_all_questions(
    include_questions_md: bool = True,
    include_ground_truth: bool = True,
    dedup: bool = True,
) -> list[QuestionItem]:
    """加载所有数据源的问题，可选的去重合并。

    Args:
        include_questions_md: 是否加载 questions.md（合并版，126 题）
        include_ground_truth: 是否加载 ground_truth_30q.json
        dedup: 是否去重

    Returns:
        合并后的问题列表
    """
    all_items: list[QuestionItem] = []

    if include_questions_md:
        all_items.extend(parse_questions_markdown())

    if include_ground_truth:
        all_items.extend(parse_ground_truth_json())

    if dedup:
        all_items = _deduplicate(all_items)

    # 按 source + id 排序
    all_items.sort(key=lambda x: (x.source, _natural_sort_key(x.id)))

    return all_items


def _natural_sort_key(s: str) -> tuple:
    """自然排序：Q1 < Q10 < Q01 < gt_01"""
    import re as _re
    parts = _re.split(r"(\d+)", s)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _normalize(text: str) -> str:
    """归一化问题文本用于去重。"""
    return re.sub(r"[^一-鿿\w]", "", text.lower().strip())


def _deduplicate(items: list[QuestionItem]) -> list[QuestionItem]:
    """去重：如果两条问题归一化后相似度 > 0.8，保留有 reference 的那条。"""
    deduped: dict[str, QuestionItem] = {}

    for item in items:
        norm = _normalize(item.text)
        # 检查是否与已有问题的归一化文本高度重叠
        found_key = None
        for key in deduped:
            if _text_overlap(norm, key) > 0.8:
                found_key = key
                break

        if found_key:
            existing = deduped[found_key]
            # 保留有 reference 的那条
            if item.has_reference and not existing.has_reference:
                deduped[found_key] = item
            # 如果都有 reference 或都没有，保留 source 优先级高的（qa_extracted > questions_md > ground_truth）
            elif item.has_reference == existing.has_reference:
                source_order = ["qa_extracted.md", "questions.md", "ground_truth_30q.json"]
                if source_order.index(item.source) < source_order.index(existing.source):
                    deduped[found_key] = item
        else:
            deduped[norm] = item

    return list(deduped.values())


def _text_overlap(a: str, b: str) -> float:
    """计算两个归一化后文本的字符级重叠率。"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = set_a & set_b
    return len(intersection) / min(len(set_a), len(set_b))


# ── Agent API 调用 ────────────────────────────────────────────────


def _extract_answer_text(answer: dict | str | None) -> str:
    """从 Agent 返回的 answer dict 中提取完整文本。

    Agent 返回格式：
    {"summary": "...", "sections": [{"title": "...", "content": "..."}, ...]}
    """
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        parts = []
        sections = answer.get("sections", [])
        if sections:
            for sec in sections:
                if isinstance(sec, dict):
                    content = sec.get("content", "")
                    if content:
                        parts.append(content)
        else:
            summary = answer.get("summary", "")
            if summary:
                parts.append(summary)
        return "\n\n".join(parts)
    return str(answer) if answer else ""


def _extract_sources(sources: list[dict] | None) -> list[str]:
    """从 Agent 返回的 sources 中提取检索文本列表。

    用于填充 LLMTestCase.retrieval_context（FaithfulnessMetric 需要）。
    支持两种格式：
    - chunk 模式：{content/text/snippet}
    - ontology 模式：{title, section_title, snippet_ref}（组合为描述性文本）
    """
    if not sources:
        return []
    texts: list[str] = []
    for s in sources:
        if isinstance(s, dict):
            content = s.get("content") or s.get("text") or s.get("snippet") or ""
            if content:
                texts.append(str(content))
                continue
            # ontology 格式：拼 title + snippet_ref
            parts = []
            title = s.get("title") or s.get("display_title") or ""
            if title:
                parts.append(title)
            section = s.get("section_title") or ""
            if section:
                parts.append(f"[{section}]")
            snippet = s.get("snippet_ref") or ""
            if snippet:
                parts.append(snippet)
            if parts:
                texts.append(" ".join(parts))
    return texts


async def call_agent_api(
    app_url: str,
    question: QuestionItem,
    model: str | None = None,
    tenant_id: str = "taishan",
    timeout: float = 120.0,
    streaming: bool = True,
) -> AgentResponse:
    """调用 Agent HTTP API，返回结构化的 AgentResponse。

    Args:
        app_url: Agent API 地址，如 http://localhost:8003
        question: 问题对象
        model: 模型名。streaming 模式下无效（端点不支持 model 覆盖）
        tenant_id: 租户 ID
        timeout: 请求超时时间（秒）
        streaming: 默认 True，走 /eval/streaming-chat 端点模拟钉钉流式场景，
                   可获取 TTFT 和 token 消耗
    """
    t_start = time.monotonic()
    response = AgentResponse()

    if streaming:
        endpoint = "/eval/streaming-chat"
        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": "deepeval_eval",
            "message": question.text,
            "channel": "eval_streaming",
        }
        # streaming 端点不支持 model 参数
    else:
        endpoint = "/agent/chat"
        payload = {
            "tenant_id": tenant_id,
            "user_id": "deepeval_eval",
            "message": question.text,
            "channel": "eval",
        }
        if model:
            payload["model"] = model

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{app_url.rstrip('/')}{endpoint}",
                json=payload,
                timeout=timeout,
            )
            response.latency_ms = int((time.monotonic() - t_start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("answer", {})
                response.answer_text = _extract_answer_text(answer)
                response.summary = answer.get("summary", "") if isinstance(answer, dict) else ""
                response.sections = answer.get("sections", []) if isinstance(answer, dict) else []
                response.sources = _extract_sources(data.get("sources", []))
                response.task_type = data.get("task_type", "")
                risk = data.get("risk", {})
                response.risk_level = risk.get("level", "none") if isinstance(risk, dict) else "none"
                response.risk_flags = risk.get("flags", []) if isinstance(risk, dict) else []

                # Token 用量（debug.usage）
                debug = data.get("debug") or {}
                usage = debug.get("usage", {})
                response.prompt_tokens = usage.get("prompt_tokens", 0)
                response.completion_tokens = usage.get("completion_tokens", 0)
                response.total_tokens = usage.get("total_tokens", 0)

                # TTFT（仅在 streaming 端点返回）
                retrieval_info = debug.get("retrieval_info", {})
                response.ttft_ms = retrieval_info.get("ttft_ms", 0)
            else:
                response.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
    except httpx.TimeoutException:
        response.error = f"Timeout after {timeout}s"
        response.latency_ms = int((time.monotonic() - t_start) * 1000)
    except Exception as e:
        response.error = f"{type(e).__name__}: {e}"
        response.latency_ms = int((time.monotonic() - t_start) * 1000)

    return response


# 裁判 LLM 单次 prompt 的上下文有限，过长的输入/输出/检索内容
# 会导致 JSON 解析失败。这里对进入评估的字段做安全截断。
_MAX_ACTUAL_OUTPUT_CHARS = 2500
_MAX_SOURCE_CHARS = 1500


def build_llm_test_case(
    question: QuestionItem,
    agent_response: AgentResponse,
) -> LLMTestCase:
    """用 Agent 的返回结果构建 DeepEval LLMTestCase。

    Args:
        question: 问题对象（含 reference 等元信息）
        agent_response: Agent API 返回的响应

    Returns:
        LLMTestCase：可直接传给 DeepEval 的 evaluate() 或 assert_test()
    """
    actual = agent_response.answer_text
    if len(actual) > _MAX_ACTUAL_OUTPUT_CHARS:
        actual = actual[:_MAX_ACTUAL_OUTPUT_CHARS]

    sources = [
        s[:_MAX_SOURCE_CHARS] for s in agent_response.sources
    ] if agent_response.sources else []

    return LLMTestCase(
        input=question.text,
        actual_output=actual,
        expected_output=question.reference,
        retrieval_context=sources,
        context=sources,
    )
