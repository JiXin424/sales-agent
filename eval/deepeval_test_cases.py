"""
测试用例构建器 —— 加载问题，直接调用 Online Graph（模拟钉钉用户路径），构建 DeepEval LLMTestCase。

走与钉钉用户完全相同的生产链路：invoke_online_turn() → Online Graph →
Chat Graph → DingTalkMessageRenderer 渲染。

数据来源：
1. eval/questions.md —— 126 题（80 题有参考答案 + 46 题纯问题）
2. eval/ground_truth_30q.json —— 30 题，有分类标注和关键词
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepeval.test_case import LLMTestCase

logger = logging.getLogger(__name__)


# ── DB 初始化（一次性） ──────────────────────────────────────────────

def init_eval_db() -> None:
    """初始化数据库引擎和 session factory（幂等）。

    必须在任何 invoke_online_turn 调用前执行一次。
    复用 Agent 自身的 database.py 模块，不需要单独配置。
    """
    from sales_agent.core.database import get_engine, get_session_factory

    # 触发引擎创建 + session factory 初始化
    get_engine()
    get_session_factory()
    logger.info("Eval DB engine initialized")


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
    """Agent 执行结果（从 invoke_online_turn 返回值提取的评估所需字段）。"""

    answer_text: str = ""
    rendered_output: str = ""        # 钉钉用户实际看到的渲染文本
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


# ── Golden 文件解析（DeepEval Synthesizer 生成） ───────────────────


def parse_golden_json(path: str | Path) -> list[QuestionItem]:
    """解析 Synthesizer 生成的 goldens.json。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items: list[QuestionItem] = []
    for i, g in enumerate(data, 1):
        inp = g.get("input", "")
        exp = g.get("expected_output", "")
        src = g.get("source_file", "")
        items.append(QuestionItem(
            id=f"golden_{i:04d}",
            text=inp,
            reference=exp,
            has_reference=bool(exp),
            source=f"goldens.json:{Path(src).name}" if src else "goldens.json",
        ))
    return items


def parse_golden_csv(path: str | Path) -> list[QuestionItem]:
    """解析 Synthesizer 生成的 goldens.csv。"""
    import csv
    items: list[QuestionItem] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            inp = row.get("input", "") or row.get("问题", "")
            exp = row.get("expected_output", "") or row.get("expected output", "") or row.get("参考答案", "")
            items.append(QuestionItem(
                id=f"csv_{i:04d}",
                text=inp,
                reference=exp,
                has_reference=bool(exp),
                source=f"goldens.csv",
            ))
    return items


def parse_golden_md(path: str | Path) -> list[QuestionItem]:
    """解析 Synthesizer 生成的 goldens.md（Markdown 格式）。"""
    text = Path(path).read_text(encoding="utf-8")
    items: list[QuestionItem] = []

    # 匹配 ## 第 N 题 ... **问题**：... **参考答案**：...
    sections = re.split(r"\n## 第 \d+ 题\n", text)
    for i, sec in enumerate(sections):
        if not sec.strip():
            continue
        # 提取问题
        q_match = re.search(r"\*\*问题\*\*[：:]\s*(.+?)(?=\n\n|\n\*\*|$)", sec, re.DOTALL)
        # 提取答案
        a_match = re.search(r"\*\*参考答案\*\*[：:]\s*(.+?)(?=\n\n\*\*来源|$)", sec, re.DOTALL)
        inp = q_match.group(1).strip() if q_match else ""
        exp = a_match.group(1).strip() if a_match else ""
        if inp:
            items.append(QuestionItem(
                id=f"md_{i:04d}",
                text=inp,
                reference=exp,
                has_reference=bool(exp),
                source="goldens.md",
            ))
    return items


def parse_golden_file(path: str | Path) -> list[QuestionItem]:
    """根据扩展名自动选择解析器。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".json":
        return parse_golden_json(p)
    elif suffix == ".csv":
        return parse_golden_csv(p)
    elif suffix in (".md", ".markdown"):
        return parse_golden_md(p)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}（支持 .json / .csv / .md）")


# ── 问题集汇总 ─────────────────────────────────────────────────────


def load_all_questions(
    include_questions_md: bool = True,
    include_ground_truth: bool = True,
    golden_file: str | None = None,
    dedup: bool = True,
) -> list[QuestionItem]:
    """加载所有数据源的问题，可选的去重合并。"""
    all_items: list[QuestionItem] = []

    if golden_file:
        all_items.extend(parse_golden_file(golden_file))

    if include_questions_md:
        all_items.extend(parse_questions_markdown())

    if include_ground_truth:
        all_items.extend(parse_ground_truth_json())

    if dedup:
        all_items = _deduplicate(all_items)

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
        found_key = None
        for key in deduped:
            if _text_overlap(norm, key) > 0.8:
                found_key = key
                break

        if found_key:
            existing = deduped[found_key]
            if item.has_reference and not existing.has_reference:
                deduped[found_key] = item
            elif item.has_reference == existing.has_reference:
                source_order = ["qa_extracted.md", "questions.md", "ground_truth_30q.json"]
                try:
                    item_rank = source_order.index(item.source)
                except ValueError:
                    item_rank = len(source_order)  # unknown sources get lowest priority
                try:
                    existing_rank = source_order.index(existing.source)
                except ValueError:
                    existing_rank = len(source_order)
                if item_rank < existing_rank:
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


# ── 从 answer_dict 提取纯文本 ──────────────────────────────────────


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


# ── 从 pipeline 的 sources (list[dict]) 提取检索文本 ─────────────────


def _extract_sources(sources: list[dict] | None) -> list[str]:
    """从 pipeline 返回的 sources 中提取检索文本列表。

    用于填充 LLMTestCase.retrieval_context。
    RAG 路径：to_source_item() 现在包含 text 字段（截断 2000 字符）
    Ontology 路径：包含 content/text/snippet/title 字段
    """
    if not sources:
        return []
    texts: list[str] = []
    for s in sources:
        if isinstance(s, dict):
            # 优先用 text 字段（RAG: to_source_item 包含，Ontology: content 映射到 text）
            text = s.get("text") or s.get("content") or s.get("snippet") or ""
            if text:
                texts.append(str(text))
                continue
            # 回退：拼接元信息
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


# ── 核心：直接调用 Online Graph（模拟钉钉用户） ────────────────────

_MAX_ACTUAL_OUTPUT_CHARS = 2500
_MAX_SOURCE_CHARS = 1500


def _resolve_chat_model_override(model: str | None):
    """按 models.json 中的模型名构建 ChatModel 实例，用于 eval 多模型对比。

    model=None 时返回 None，由 invoke_online_turn 内部自动 resolve 租户默认模型。
    """
    if not model:
        return None
    from sales_agent.core.model_registry import ModelRegistry
    registry = ModelRegistry.load()
    if not registry:
        logger.warning("ModelRegistry not loaded, cannot override model %r", model)
        return None
    entry = registry.get(model)
    if not entry or not entry.api_key:
        logger.warning("Model override %r not found or missing API key, using default", model)
        return None
    from sales_agent.llm.openai_compatible import OpenAICompatibleChat
    return OpenAICompatibleChat(
        api_key=entry.api_key,
        base_url=entry.base_url,
        model=entry.chat_model,
        temperature=entry.temperature,
        timeout_seconds=entry.timeout_seconds,
        max_retries=entry.max_retries,
    )


async def call_agent_pipeline(
    question: QuestionItem,
    tenant_id: str = "taishan",
    model: str | None = None,
    agent_id: str | None = None,
) -> AgentResponse:
    """直接调用 invoke_online_turn，模拟钉钉用户的消息处理路径。

    与 handle_dingtalk_event() 走完全相同的代码路径：
    Online Graph → context_resolution → evidence_routing → Chat Graph
    → routing → retrieval → generation → risk check →
    DingTalkMessageRenderer 渲染输出。

    Args:
        question: 问题对象
        tenant_id: 租户 ID
        model: 可选模型覆盖（通过 models.json 切换）
        agent_id: 可选 Agent ID

    Returns:
        AgentResponse：包含回答文本、检索来源、耗时、token 用量等
    """
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import get_session_factory
    from sales_agent.integrations.dingtalk.config import DingTalkConfig
    from sales_agent.integrations.dingtalk.message_renderer import DingTalkMessageRenderer
    from sales_agent.models.base import generate_id
    from sales_agent.services.online_conversation import invoke_online_turn

    settings = get_settings()
    renderer = DingTalkMessageRenderer(DingTalkConfig(max_reply_chars=20000))

    chat_model_override = _resolve_chat_model_override(model)

    t_start = time.monotonic()
    response = AgentResponse()

    try:
        factory = get_session_factory()
        async with factory() as db:
            result = await invoke_online_turn(
                db=db,
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id="deepeval_eval",
                # 每题唯一 session_user_id → 唯一 thread_id，上下文不串
                session_user_id=f"deepeval_eval_{question.id}",
                channel="dingtalk",
                conversation_id=f"eval_{generate_id()}",
                message=question.text,
                event_id=f"eval_{generate_id()}",
                chat_model=chat_model_override,
            )

            response.latency_ms = int((time.monotonic() - t_start) * 1000)

            answer_dict = result.get("answer_dict") or {}
            response_kind = result.get("response_kind", "chat")

            # 快速命令（帮助/reset）：answer_dict.summary 直接是回复文本
            if response_kind == "fast" or response_kind == "duplicate":
                response.answer_text = answer_dict.get("summary", "")
                response.rendered_output = response.answer_text
                response.task_type = response_kind
            else:
                if isinstance(answer_dict, dict):
                    response.answer_text = _extract_answer_text(answer_dict)
                    response.summary = answer_dict.get("summary", "")
                    response.sections = answer_dict.get("sections", [])
                else:
                    response.answer_text = str(answer_dict)

                response.task_type = result.get("task_type", "") or ""

                # 用 DingTalkMessageRenderer 渲染（模拟钉钉用户看到的文本）
                render_answer = answer_dict if isinstance(answer_dict, dict) else {
                    "summary": str(answer_dict), "sections": [],
                }
                sources = result.get("sources", [])
                risk_result = result.get("risk_result", {})
                response.rendered_output = renderer.render(
                    render_answer,
                    sources,
                    risk_result,
                )

                # 检索来源（graph retrieve_node 已在 sources 里携带 text 字段）
                response.sources = _extract_sources(sources)

                # 风险（risk_result 是 dict：level/flags/action/notice/rewrite_summary）
                response.risk_level = risk_result.get("level", "")
                response.risk_flags = risk_result.get("flags", [])

                # Token 用量
                usage = result.get("usage", {})
                response.prompt_tokens = usage.get("prompt_tokens", 0)
                response.completion_tokens = usage.get("completion_tokens", 0)
                response.total_tokens = usage.get("total_tokens", 0)

            # TTFT：graph 无逐阶段计时，弃用（报告显示 "—"）
            response.ttft_ms = 0

    except Exception as e:
        logger.error("invoke_online_turn failed for %s: %s", question.id, e)
        response.error = f"{type(e).__name__}: {e}"
        response.latency_ms = int((time.monotonic() - t_start) * 1000)

    return response


# ── 构建 LLMTestCase ─────────────────────────────────────────────────


def build_llm_test_case(
    question: QuestionItem,
    agent_response: AgentResponse,
) -> LLMTestCase:
    """用 Agent 返回结果构建 DeepEval LLMTestCase。

    Args:
        question: 问题对象（含 reference 等元信息）
        agent_response: 从 invoke_online_turn 获取的响应

    Returns:
        LLMTestCase：可直接传给 DeepEval 的 evaluate() 或 assert_test()
    """
    # 截断过长的回答
    actual = agent_response.answer_text
    if len(actual) > _MAX_ACTUAL_OUTPUT_CHARS:
        actual = actual[:_MAX_ACTUAL_OUTPUT_CHARS]

    # 截断过长的来源文本
    sources = [
        s[:_MAX_SOURCE_CHARS] for s in agent_response.sources
    ] if agent_response.sources else []

    return LLMTestCase(
        input=question.text,
        actual_output=actual,
        expected_output=question.reference,
        retrieval_context=sources,
        context=sources,
        tools_called=[],
    )


# ── 导出 ─────────────────────────────────────────────────────────────

__all__ = [
    "init_eval_db",
    "QuestionItem",
    "AgentResponse",
    "parse_questions_markdown",
    "parse_ground_truth_json",
    "parse_golden_json",
    "parse_golden_csv",
    "parse_golden_md",
    "parse_golden_file",
    "load_all_questions",
    "call_agent_pipeline",
    "build_llm_test_case",
]
