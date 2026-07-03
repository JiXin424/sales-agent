"""
测试用例构建器 —— 加载问题，直接调用 ChatPipeline（模拟钉钉用户路径），构建 DeepEval LLMTestCase。

不再通过 HTTP API 调 Agent，而是走与钉钉用户完
全相同的 FastAPI 流式链路：ChatPipeline.execute() → DingTalkMessageRenderer 渲染。

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

    必须在任何 ChatPipeline 调用前执行一次。
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
    """Agent 执行结果（从 ChatPipeline 提取的评估所需字段）。"""

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


# ── 核心：直接调用 ChatPipeline（模拟钉钉用户） ────────────────────

_MAX_ACTUAL_OUTPUT_CHARS = 2500
_MAX_SOURCE_CHARS = 1500


def _is_ontology_engine() -> bool:
    """检查当前租户是否使用 ontology 相关的知识引擎（ontology_neo4j 或 hybrid）。"""
    from sales_agent.core.config import get_settings
    settings = get_settings()
    return settings.ontology.knowledge_engine in ("ontology_neo4j", "hybrid")


async def _fetch_ontology_sources(
    tenant_id: str,
    agent_id: str | None,
    message: str,
    db,
) -> list[str]:
    """当 pipeline sources 为空时，直接查询 ontology 获取事实作为 source 文本。"""
    try:
        from sales_agent.ontology.retrieval_service import OntologyRetrievalService
        from sales_agent.ontology.repository import OntologyRepository
        from sales_agent.ontology.neo4j_client import Neo4jClient
        from sales_agent.core.config import get_settings
        from sales_agent.llm.openai_compatible import OpenAICompatibleEmbedding
        import os as _os

        settings = get_settings()
        neo4j_client = Neo4jClient(settings.neo4j)
        repo = OntologyRepository(neo4j_client)

        # 用 app 原生 embedding model（支持 DashScope text-embedding-v3）
        embedder = OpenAICompatibleEmbedding(
            model=_os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
            api_key=_os.getenv("EMBEDDING_API_KEY", ""),
            base_url=_os.getenv("EMBEDDING_BASE_URL", ""),
        )
        # chat_model=None → 跳过 LLM 实体提取，用原始问题直接检索
        retriever = OntologyRetrievalService(repo, embedder, chat_model=None)
        result = await retriever.retrieve(
            tenant_id=tenant_id,
            agent_id=agent_id,
            question=message,
        )
        raw_docs = result.source_documents if result.source_documents else result.facts_used
        texts = []
        for doc in raw_docs[:5]:
            text = (
                doc.get("content") or doc.get("text") or doc.get("value")
                or doc.get("snippet") or doc.get("title") or doc.get("name", "")
            )
            if text:
                texts.append(str(text)[:_MAX_SOURCE_CHARS])
        return texts
    except Exception as e:
        logger.warning("Failed to fetch ontology sources for eval: %s", e)
        return []


async def call_agent_pipeline(
    question: QuestionItem,
    tenant_id: str = "taishan",
    model: str | None = None,
    agent_id: str | None = None,
) -> AgentResponse:
    """直接调用 ChatPipeline.execute()，模拟钉钉用户的消息处理路径。

    与 handle_dingtalk_event() 走完全相同的代码路径：
    ChatPipeline → routing → retrieval → generation → risk check →
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
    from sales_agent.services.chat_pipeline import ChatPipeline

    settings = get_settings()
    renderer = DingTalkMessageRenderer(DingTalkConfig(max_reply_chars=20000))

    # 捕获 reply_fn 调用（"处理中"提示、错误等）
    captured_replies: list[str] = []
    async def reply_fn(text: str) -> None:
        captured_replies.append(text)

    t_start = time.monotonic()
    response = AgentResponse()

    try:
        factory = get_session_factory()
        async with factory() as db:
            pipeline = ChatPipeline(db, settings)
            result = await pipeline.execute(
                tenant_id=tenant_id,
                user_id="deepeval_eval",
                message=question.text,
                conversation_id=f"eval_{generate_id()}",
                context=None,
                channel="dingtalk_single",
                reply_fn=reply_fn,
                agent_id=agent_id,
                model=model,
            )

            response.latency_ms = int(result.timings.total_ms)

            if result.fast_reply:
                # 快速命令（帮助/reset）直接返回
                response.answer_text = result.fast_reply
                response.rendered_output = result.fast_reply
                response.task_type = "fast_command"
            else:
                answer_dict = result.answer_dict
                if isinstance(answer_dict, dict):
                    response.answer_text = _extract_answer_text(answer_dict)
                    response.summary = answer_dict.get("summary", "")
                    response.sections = answer_dict.get("sections", [])
                else:
                    response.answer_text = str(answer_dict)

                response.task_type = (
                    result.route_result.task_type if result.route_result else ""
                )

                # 用 DingTalkMessageRenderer 渲染（模拟钉钉用户看到的文本）
                render_answer = answer_dict if isinstance(answer_dict, dict) else {
                    "summary": str(answer_dict), "sections": [],
                }
                response.rendered_output = renderer.render(
                    render_answer,
                    result.sources or [],
                    result.risk_result,
                )

                # 检索来源
                response.sources = _extract_sources(result.sources or [])

                # ontology_neo4j 兜底：路由未触发检索时直接查图谱获取事实做 retrieval_context
                if not response.sources and _is_ontology_engine():
                    response.sources = await _fetch_ontology_sources(
                        tenant_id, agent_id, question.text, db,
                    )

                # 风险
                response.risk_level = result.risk_result.level
                response.risk_flags = result.risk_result.flags

                # Token 用量
                response.prompt_tokens = result.usage.get("prompt_tokens", 0)
                response.completion_tokens = result.usage.get("completion_tokens", 0)
                response.total_tokens = result.usage.get("total_tokens", 0)

                # TTFT 近似：generation 之前的各阶段耗时之和
                stages = result.timings.stages
                response.ttft_ms = int(
                    stages.get("validation", 0)
                    + stages.get("tenant_resolve", 0)
                    + stages.get("context_load", 0)
                    + stages.get("routing", 0)
                    + stages.get("retrieval", 0)
                    + stages.get("ontology_answer", 0)
                )

            # 如果 reply_fn 被调用了（处理中提示），记录
            if captured_replies:
                logger.debug("Pipeline sent %d reply_fn calls: %s",
                             len(captured_replies),
                             [r[:80] for r in captured_replies])

    except Exception as e:
        logger.error("ChatPipeline failed for %s: %s", question.id, e)
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
        agent_response: 从 ChatPipeline 获取的响应

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
