"""关键词检索器：基于倒排索引 + 同义词扩展 + IDF 加权的中文关键词检索。

提供向量检索之外的第二通道，专治口语化/模糊查询无法被纯向量召回的场景。

设计要点：
1. 查询 tokenize（中文整词 + 2/3-gram 兜底）+ 同义词扩展
2. 多字段加权匹配（text / section_title / search_keywords）
3. IDF 降权高频 noise token
4. 返回带分数的 chunk 列表，可与向量结果做 RRF 融合
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.document import DocumentChunk, Document

logger = logging.getLogger(__name__)

# ── 分词正则 ──────────────────────────────────────────────────────────
# 中文：单字 + 连续 2-gram + 连续 3-gram，同时保留英文/数字 token
_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")
# 常见停用词（高噪声、区分度低，计算 IDF 时有 penalty 但不会被完全排除）
_HIGH_FREQ_WORDS: set[str] = {
    "一个", "这个", "那个", "可以", "我们", "他们", "客户", "销售",
    "什么", "怎么", "如何", "为什么", "因为", "所以", "如果", "但是",
    "已经", "没有", "不是", "就是", "还是", "只是", "都能", "需要",
    "进行", "通过", "使用", "提供", "包括", "支持", "帮助", "了解",
    "知道", "告诉", "觉得", "认为", "应该", "可能", "能够", "一定",
    "吗", "呢", "吧", "啊", "的", "了", "在", "是", "有", "和",
    "与", "或", "及", "等", "其", "这", "那",
}

# 字段权重：
#   权重越高，命中该字段对分数的贡献越大。
#   search_keywords 最高是因为这是人工/LLM 标注的检索锚点；
#   section_title 次之，text 是最泛的匹配。
FIELD_WEIGHTS = {
    "text": 1.0,
    "section_title": 3.0,
    "search_keywords": 5.0,
}

# 同义词文件路径（相对于项目根目录）
_DEFAULT_SYNONYMS_PATH = Path(__file__).resolve().parents[4] / "data" / "synonyms.json"


@dataclass
class KeywordHit:
    """一次关键词命中。"""

    chunk_id: str
    document_id: str
    tenant_id: str
    title: str = ""
    section_title: str = ""
    text: str = ""
    score: float = 0.0
    hit_tokens: list[str] = field(default_factory=list)
    hit_fields: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Tokenize ────────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """将查询文本拆分为 token 列表（中文 n-gram + 英文/数字 token）。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        seg = match.group()
        if re.match(r"^[一-鿿]+$", seg):
            # 中文段：保留完整段 + 2-gram + 3-gram
            tokens.append(seg)  # 完整词
            if len(seg) >= 2:
                for i in range(len(seg) - 1):
                    tokens.append(seg[i:i + 2])
            if len(seg) >= 3:
                for i in range(len(seg) - 2):
                    tokens.append(seg[i:i + 3])
        else:
            # 英文/数字：保留原始 token + lowercase
            if len(seg) >= 2:
                tokens.append(seg)
    return tokens


# ── Synonym loading ─────────────────────────────────────────────────────


def _load_synonyms(synonyms_path: str | Path | None = None) -> dict[str, str]:
    """加载同义词文件，返回 {variant → standard} 的扁平映射。"""
    path = Path(synonyms_path) if synonyms_path else _DEFAULT_SYNONYMS_PATH
    if not path.exists():
        logger.warning("Synonyms file not found: %s, skipping synonym expansion", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load synonyms: %s", e)
        return {}

    mapping: dict[str, str] = {}
    for _category, entries in raw.items():
        if _category.startswith("_"):
            continue
        if not isinstance(entries, dict):
            continue
        for standard, variants in entries.items():
            if not isinstance(variants, list):
                continue
            for variant in variants:
                mapping[variant.lower()] = standard.lower()
    logger.debug("Loaded %d synonym mappings", len(mapping))
    return mapping


# ── IDF computation ──────────────────────────────────────────────────────


def _compute_idf(corpus_texts: list[str]) -> dict[str, float]:
    """计算 token 的 IDF 值 (log(N/df))。

    高频噪声 token 有天然的 low IDF，但如果 query 很短且全由
    high-freq token 组成，说明用户真的有口语化意图，不额外做 penalty。
    """
    N = len(corpus_texts)
    if N == 0:
        return {}

    df: dict[str, int] = defaultdict(int)
    for text in corpus_texts:
        tokens = set(_tokenize(text))
        for t in tokens:
            df[t] += 1

    # 确保 df 至少为 2（smoothing）
    idf: dict[str, float] = {}
    for t, d in df.items():
        idf[t] = math.log((N + 1) / max(d, 1))
    return idf


# ── In-memory inverted index ────────────────────────────────────────────


@dataclass
class _IndexedChunk:
    """索引中的一个 chunk（轻量结构）。"""

    chunk_id: str
    document_id: str
    title: str
    section_title: str
    text: str
    search_keywords: str  # 逗号/空格分隔
    metadata: dict[str, Any] = field(default_factory=dict)


def _build_inverted_index(
    chunks: list[_IndexedChunk],
) -> dict[str, list[tuple[int, str]]]:
    """构建 token → [(chunk_index, field), ...] 的倒排索引。

    Args:
        chunks: 所有 chunk 的列表（按 index 编号）

    Returns:
        {token: [(chunk_idx, field_name), ...]}
    """
    inv: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for idx, ch in enumerate(chunks):
        # text 字段
        for t in set(_tokenize(ch.text)):
            inv[t].append((idx, "text"))
        # section_title 字段
        if ch.section_title:
            for t in set(_tokenize(ch.section_title)):
                inv[t].append((idx, "section_title"))
        # search_keywords 字段（视为强信号）
        if ch.search_keywords:
            for kw in re.split(r"[,，\s]+", ch.search_keywords):
                kw = kw.strip().lower()
                if kw:
                    for t in set(_tokenize(kw)):
                        inv[t].append((idx, "search_keywords"))
    return inv


# ── Main Retriever ──────────────────────────────────────────────────────


class KeywordRetriever:
    """关键词检索器。

    使用方式：::

        kr = KeywordRetriever(db)
        await kr.build_index(tenant_id)      # 首次/重建索引
        hits = await kr.search(tenant_id, "客户嫌贵怎么办")
    """

    def __init__(
        self,
        db: AsyncSession,
        synonyms_path: str | Path | None = None,
    ) -> None:
        self.db = db
        self._synonyms = _load_synonyms(synonyms_path)
        # 按 tenant_id 缓存的索引
        self._index_cache: dict[str, dict] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def expand_tokens(self, tokens: list[str]) -> list[tuple[str, bool]]:
        """对 token 列表做同义词扩展。

        Returns:
            [(token, is_synonym_expansion), ...]
            原始 tokens 的 is_synonym_expansion=False，
            扩展出来的 synonyms 的 is_synonym_expansion=True。
        """
        result: list[tuple[str, bool]] = []
        seen = set(tokens)
        for t in tokens:
            result.append((t, False))
        # 检查每个 token 是否作为 variant 命中同义词表 → 扩展 standard
        for t in tokens:
            t_lower = t.lower()
            if t_lower in self._synonyms:
                std = self._synonyms[t_lower]
                if std not in seen:
                    seen.add(std)
                    result.append((std, True))
        return result

    async def build_index(self, tenant_id: str) -> None:
        """加载指定租户的所有 chunk 并构建倒排索引 + IDF 词典。

        索引缓存在 ``self._index_cache[tenant_id]`` 中。
        """
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.tenant_id,
                DocumentChunk.document_id,
                DocumentChunk.text,
                DocumentChunk.section_title,
                DocumentChunk.metadata_json,
                Document.title.label("doc_title"),
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.tenant_id == tenant_id)
            .order_by(DocumentChunk.id)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        indexed: list[_IndexedChunk] = []
        corpus_texts: list[str] = []

        for row in rows:
            meta = row.metadata_json or {}
            search_kws = ""
            if isinstance(meta, dict):
                search_kws = meta.get("search_keywords", "")
            elif isinstance(meta, str):
                try:
                    meta_dict = json.loads(meta)
                    search_kws = meta_dict.get("search_keywords", "")
                except json.JSONDecodeError:
                    pass

            # 确保 metadata 是指向 dict 或空 dict
            meta_safe = meta if isinstance(meta, dict) else {}

            idx_chunk = _IndexedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                title=row.doc_title or "",
                section_title=row.section_title or "",
                text=row.text or "",
                search_keywords=search_kws,
                metadata=meta_safe,
            )
            indexed.append(idx_chunk)
            corpus_texts.append(row.text or "")

        # 构建倒排索引
        inverted = _build_inverted_index(indexed)
        # 计算 IDF
        idf = _compute_idf(corpus_texts)

        self._index_cache[tenant_id] = {
            "chunks": indexed,
            "inverted": inverted,
            "idf": idf,
            "chunk_count": len(indexed),
        }
        logger.info(
            "Keyword index built for tenant=%s: %d chunks, %d unique tokens",
            tenant_id, len(indexed), len(inverted),
        )

    async def search(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 20,
    ) -> list[KeywordHit]:
        """执行关键词检索。

        Args:
            tenant_id: 租户 ID
            query: 检索查询文本
            top_k: 返回数量

        Returns:
            按分数降序排列的 :class:`KeywordHit` 列表
        """
        # 确保索引已构建
        if tenant_id not in self._index_cache:
            await self.build_index(tenant_id)
            if tenant_id not in self._index_cache:
                logger.error("Failed to build keyword index for tenant=%s", tenant_id)
                return []

        cache = self._index_cache[tenant_id]
        chunks: list[_IndexedChunk] = cache["chunks"]
        inverted: dict[str, list[tuple[int, str]]] = cache["inverted"]
        idf: dict[str, float] = cache["idf"]

        if not chunks:
            return []

        # 1. Tokenize + synonym expansion
        raw_tokens = _tokenize(query)
        expanded = self.expand_tokens(raw_tokens)
        query_tokens = [t for t, _ in expanded]

        # 2. 对每个 chunk 计算累积分
        #    score += field_weight * token_len^2 * IDF
        chunk_scores: dict[int, float] = defaultdict(float)
        chunk_hit_tokens: dict[int, set[str]] = defaultdict(set)
        chunk_hit_fields: dict[int, set[str]] = defaultdict(set)

        for token, is_syn in expanded:
            token_lower = token.lower()
            postings = inverted.get(token_lower)
            if not postings:
                # 尝试用更短的 n-gram 再做一次匹配
                if len(token_lower) >= 3:
                    for sub_len in (2,):
                        for i in range(len(token_lower) - sub_len + 1):
                            sub = token_lower[i:i + sub_len]
                            sub_postings = inverted.get(sub)
                            if sub_postings:
                                if postings is None:
                                    postings = []
                                postings.extend(sub_postings)

            if not postings:
                continue

            token_idf = idf.get(token_lower, 1.0)
            # synonym expansion 权重打 8 折（避免扩展 token 抢戏）
            syn_factor = 0.8 if is_syn else 1.0
            token_score = len(token) ** 2 * token_idf * syn_factor

            for chunk_idx, field in postings:
                weight = FIELD_WEIGHTS.get(field, 1.0)
                chunk_scores[chunk_idx] += token_score * weight
                chunk_hit_tokens[chunk_idx].add(token_lower)
                chunk_hit_fields[chunk_idx].add(field)

        # 3. 排序取 top_k
        ranked = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        # 4. 组装结果
        hits: list[KeywordHit] = []
        for chunk_idx, score in ranked:
            ch = chunks[chunk_idx]
            hits.append(KeywordHit(
                chunk_id=ch.chunk_id,
                document_id=ch.document_id,
                tenant_id=tenant_id,
                title=ch.title,
                section_title=ch.section_title,
                text=ch.text,
                score=round(score, 4),
                hit_tokens=sorted(chunk_hit_tokens.get(chunk_idx, set())),
                hit_fields=sorted(chunk_hit_fields.get(chunk_idx, set())),
                metadata=ch.metadata,
            ))

        return hits

    def clear_cache(self, tenant_id: str | None = None) -> None:
        """清除关键词索引缓存。

        Args:
            tenant_id: 清除指定租户的缓存；为 None 则清除全部。
        """
        if tenant_id is None:
            self._index_cache.clear()
        elif tenant_id in self._index_cache:
            del self._index_cache[tenant_id]
