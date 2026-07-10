"""缺口检测：从 search_terms 中找出 KB 未命中的实体候选。

供 _retrieve_via_ontology 在 compact_evidence 之后使用：用抽取出的
search_terms（期望实体）减去 KB 实际命中的实体名，得到需要走 web 补全的
「未命中实体」清单。纯函数、无 IO，便于单测。
"""

from __future__ import annotations

# 非实体词（比较/疑问/通用词），命中即剔除，不作为 web 补全候选。
_NON_ENTITY_TERMS = {
    "区别", "对比", "比较", "怎么样", "如何", "介绍", "简介",
    "哪个好", "哪一个好", "哪个", "vs", "和", "与", "的", "吗",
    "是什么", "有什么", "区别是什么", "产品", "功能", "价格", "方案",
    "区别在哪", "差异",
}


def is_entity_like(term: str) -> bool:
    """判断 term 是否像品牌/产品实体（剔除停用词与过短词）。"""
    t = (term or "").strip()
    if len(t) < 2:
        return False
    return t.lower() not in _NON_ENTITY_TERMS


def is_covered(term: str, matched_names: list[str]) -> bool:
    """term 是否被某个已命中实体名覆盖（双向大小写不敏感子串匹配）。

    对齐 Cypher 的 CONTAINS 语义：term 含于 name、或 name 含于 term 都算命中。
    """
    t = (term or "").strip().lower()
    if not t:
        return False
    for name in matched_names or []:
        n = (name or "").strip().lower()
        if not n:
            continue
        if t in n or n in t:
            return True
    return False


def compute_missing(
    search_terms: list[str],
    matched_entity_names: list[str],
    *,
    max_n: int,
) -> list[str]:
    """返回未命中的实体候选，保持原顺序、去重，最多 max_n 个。"""
    if max_n <= 0:
        return []
    seen: set[str] = set()
    missing: list[str] = []
    for term in search_terms or []:
        t = (term or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        if is_entity_like(t) and not is_covered(t, matched_entity_names):
            missing.append(t)
            if len(missing) >= max_n:
                break
    return missing
