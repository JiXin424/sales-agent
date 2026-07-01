#!/usr/bin/env python3
"""模拟 Neo4j 本体检索全链路，验证修复效果。

用法: python3 scripts/simulate_neo4j_retrieval.py

模拟两种场景：
  A. "全品C" 实体存在 + 冷饮品牌 facts（理想场景）
  B. "全品C" 实体不存在，仅有 "品牌墙" 实体（修复前的问题场景）
  C. "全品C" 实体存在但 facts 里才有品牌信息（测试 fact_boost）

每个场景都会模拟 LLM 实体提取 → Cypher 查询 → 证据压缩 → 展示结果。
不需要真实 Neo4j 连接。
"""

from __future__ import annotations

import json
import re
import sys
import os
from dataclasses import dataclass, field
from typing import Any

# 确保可以 import sales_agent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── 模拟 LLM 实体提取（和真实代码逻辑一致） ──────────────────
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""


def simulate_llm_extract_terms(question: str) -> list[str]:
    """模拟 LLM 提取关键词（用正则模拟，不必真实调用 LLM）。"""
    # 虚拟：按中文虚词切分（模拟 LLM 的正常输出）
    terms = re.split(r'[的了呢吗啊吧是有哪些什么怎么在哪去]', question)
    terms = [t.strip() for t in terms if t.strip()]
    return list(dict.fromkeys(terms)) if terms else [question]


# ── 中文分词（和 answer_service.py 新增的 _extract_question_terms 一致）──
_SPLIT_FUNCTION_WORDS = re.compile(r'[的了呢吗啊吧是有哪些什么怎么在哪去]')


def extract_question_terms(question: str) -> list[str]:
    terms = [t.strip() for t in _SPLIT_FUNCTION_WORDS.split(question) if t.strip()]
    return terms or [question]


# ── Cypher 查询模拟（实现新的 relevance scoring 逻辑） ─────
def simulate_cypher_query(
    entities: list[dict],
    facts: list[dict],
    search_terms: list[str],
    entity_limit: int = 15,
) -> list[dict]:
    """模拟新的 retrieval_statement() 的 Cypher 查询逻辑。

    Phase 1: 按搜索词匹配数对实体打分
    Phase 2: fact_boost — 搜索词命中 facts 的 predicate/value
    Phase 3: 按 relevance 排序 + LIMIT + 取 facts
    """
    # Phase 1: entity name_score
    scored = []
    for e in entities:
        name = (e.get("name", "") or "").lower()
        aliases = (e.get("aliases_text", "") or "").lower()
        name_score = 0
        for term in search_terms:
            term_lower = term.lower()
            if term_lower in name or term_lower in aliases:
                name_score += 1
        scored.append({"entity": e, "name_score": name_score})

    # Phase 2: fact_boost
    for item in scored:
        e = item["entity"]
        entity_facts = [f for f in facts if f.get("subject_name") == e.get("name")]
        fact_boost = 0
        matched_fact_ids = set()
        for f in entity_facts:
            fv = ((f.get("value") or "") + (f.get("predicate") or "")).lower()
            for term in search_terms:
                if term.lower() in fv:
                    matched_fact_ids.add(f.get("id"))
            # 注意：真实查询是按 fact 数计 boost，所以去重
        fact_boost = len(matched_fact_ids)
        item["fact_boost"] = fact_boost
        item["relevance"] = item["name_score"] + fact_boost

    # Filter: relevance > 0
    scored = [s for s in scored if s["relevance"] > 0]
    # ORDER BY relevance DESC
    scored.sort(key=lambda s: s["relevance"], reverse=True)
    # LIMIT
    scored = scored[:entity_limit]

    # Phase 3: 取每个实体关联的 facts
    rows = []
    for item in scored:
        e = item["entity"]
        entity_facts = [f for f in facts if f.get("subject_name") == e.get("name")]
        # 按 created_at DESC 排序（模拟）
        entity_facts.sort(key=lambda f: f.get("created_at", ""), reverse=True)
        top_facts = entity_facts[:20]  # facts_per_entity=20
        for f in top_facts:
            rows.append({
                "e": e,
                "f": f,
                "o": None,  # 简化：不模拟 object entity
                "evidence": [],
                "documents": [{"title": f.get("source_title", "未知来源")}],
                "relevance": item["relevance"],
            })

    return rows


# ── 证据压缩（和 answer_service.py 一致） ─────────────────
def compact_evidence(rows: list[dict], question: str) -> dict:
    """模拟 _compact_evidence + 新的词级相关性排序。"""
    q_terms = extract_question_terms(question)

    entities_seen = {}
    facts_seen = {}
    documents_seen = {}

    for row in rows:
        e = row.get("e")
        f = row.get("f")
        if e:
            key = e.get("name", "")
            if key not in entities_seen:
                entities_seen[key] = e
        if f:
            key = f.get("id", "")
            if key not in facts_seen:
                facts_seen[key] = f
        for d in row.get("documents", []):
            if d:
                key = d.get("title", "")
                if key not in documents_seen:
                    documents_seen[key] = d

    entities = list(entities_seen.values())
    facts = list(facts_seen.values())
    documents = list(documents_seen.values())

    # 新的词级相关性排序
    def fact_relevance(f: dict) -> int:
        score = 0
        fv = str(f.get("value", "")) + str(f.get("predicate", ""))
        for term in q_terms:
            if term in fv:
                score += len(term)
        return score

    facts.sort(key=fact_relevance, reverse=True)

    return {
        "ontology_intent": "entity_info",
        "entities": [
            {"name": e.get("name", ""), "type": e.get("type", "")}
            for e in entities[:10]
        ],
        "facts": [
            {
                "subject": f.get("subject_name", f.get("name", "")),
                "predicate": f.get("predicate", ""),
                "object": f.get("object_name", ""),
                "value": str(f.get("value", ""))[:200],
            }
            for f in facts[:25]
        ],
        "source_documents": [d.get("title", "") for d in documents[:5]],
        "confidence": 0.8 if entities else 0.0,
    }


# ── 场景定义 ──────────────────────────────────────────────

# 场景 A: 理想场景 — "全品C" 实体存在且有冷饮品牌 facts
SCENARIO_A = {
    "name": "场景A: 全品C实体存在 + 冷饮品牌facts",
    "entities": [
        {
            "name": "全品C", "type": "Product",
            "aliases_text": "QuanpinC 全品c",
            "status": "active",
        },
        {
            "name": "品牌墙", "type": "Feature",
            "aliases_text": "品牌展示 品牌墙",
            "status": "active",
        },
        {
            "name": "福多多", "type": "Product",
            "aliases_text": "Fuduoduo",
            "status": "active",
        },
    ],
    "facts": [
        {
            "id": "f1", "subject_name": "全品C",
            "predicate": "冷饮品牌",
            "object_name": None,
            "value": "全品C的餐饮自提支持7个品牌：百事可乐、美年达、七喜、佳得乐、纯果乐、立顿、星巴克",
            "fact_type": "attribute",
            "source_title": "全品C产品知识手册.docx",
            "created_at": "2025-12-01",
        },
        {
            "id": "f2", "subject_name": "全品C",
            "predicate": "产品类型",
            "object_name": None,
            "value": "全品C包含常温饮品、冷饮、零食等产品线",
            "fact_type": "attribute",
            "source_title": "全品C产品知识手册.docx",
            "created_at": "2025-11-15",
        },
        {
            "id": "f3", "subject_name": "品牌墙",
            "predicate": "展示位置",
            "object_name": None,
            "value": None,
            "fact_type": "attribute",
            "source_title": "门店布局文档.docx",
            "created_at": "2025-10-01",
        },
    ],
}

# 场景 B: 修复前的问题场景 — "全品C" 实体不存在
SCENARIO_B = {
    "name": "场景B: 全品C实体不存在（修复前的问题）",
    "entities": [
        {
            "name": "品牌墙", "type": "Feature",
            "aliases_text": "品牌展示 品牌墙",
            "status": "active",
        },
        {
            "name": "福多多", "type": "Product",
            "aliases_text": "Fuduoduo",
            "status": "active",
        },
    ],
    "facts": [
        {
            "id": "f3", "subject_name": "品牌墙",
            "predicate": "展示位置",
            "object_name": None,
            "value": None,
            "fact_type": "attribute",
            "source_title": "门店布局文档.docx",
            "created_at": "2025-10-01",
        },
        {
            "id": "f4", "subject_name": "福多多",
            "predicate": "零风险承诺",
            "object_name": None,
            "value": "福多多提供差价赔付、退票补贴等服务承诺",
            "fact_type": "attribute",
            "source_title": "福多多产品文档.docx",
            "created_at": "2025-09-01",
        },
    ],
}

# 场景 C: "全品C" 实体存在但 facts 包含冷饮品牌关键词（fact_boost 生效）
SCENARIO_C = {
    "name": "场景C: 全品C实体名字匹配 + fact_boost 二次提权",
    "entities": [
        {
            "name": "品牌墙", "type": "Feature",
            "aliases_text": "品牌展示 品牌墙",
            "status": "active",
        },
        {
            "name": "全品C", "type": "Product",
            "aliases_text": "QuanpinC 全品c",
            "status": "active",
        },
    ],
    "facts": [
        {
            "id": "f3", "subject_name": "品牌墙",
            "predicate": "展示位置",
            "object_name": None,
            "value": None,
            "fact_type": "attribute",
            "source_title": "门店布局文档.docx",
            "created_at": "2025-10-01",
        },
        {
            "id": "f1", "subject_name": "全品C",
            "predicate": "餐饮自提品牌",
            "object_name": None,
            "value": "全品C冷饮品牌包括百事可乐、美年达、七喜、佳得乐、纯果乐、立顿、星巴克共7个品牌",
            "fact_type": "attribute",
            "source_title": "全品C产品知识手册.docx",
            "created_at": "2025-12-01",
        },
    ],
}


def run_scenario(scenario: dict, question: str):
    """运行完整模拟。"""
    print("=" * 72)
    print(f"📋 {scenario['name']}")
    print(f"❓ 问题: {question}")
    print()

    # Step 1: 模拟 LLM 提取关键词
    search_terms = simulate_llm_extract_terms(question)
    print(f"🔍 Step 1 — LLM 提取关键词: {search_terms}")

    # Step 2: 模拟 Cypher 查询（新逻辑）
    rows = simulate_cypher_query(
        entities=scenario["entities"],
        facts=scenario["facts"],
        search_terms=search_terms,
    )
    matched_entity_names = []
    for row in rows:
        e = row.get("e", {})
        name = f"{e.get('name', '?')}({e.get('type', '?')})"
        if name not in matched_entity_names:
            matched_entity_names.append(name)

    print(f"🗄️  Step 2 — 图谱查询匹配实体: {matched_entity_names if matched_entity_names else '❌ NONE'}")
    if rows:
        # 展示每条 entity 的 relevance
        seen = set()
        for row in rows:
            e = row.get("e", {})
            key = e.get("name", "")
            if key not in seen:
                seen.add(key)
                print(f"      • {key} — relevance={row.get('relevance', '?')}")

    # Step 3: 证据压缩
    compacted = compact_evidence(rows, question)
    print(f"\n📦 Step 3 — 证据压缩结果:")
    print(f"      实体数: {len(compacted['entities'])}")
    for e in compacted["entities"]:
        print(f"        - {e['name']} (type={e['type']})")
    print(f"      Facts数: {len(compacted['facts'])}")
    for f in compacted["facts"]:
        val_preview = (f["value"] or "")[:80]
        print(f"        - [{f['predicate']}] {val_preview}...")
    print(f"      来源文档: {compacted['source_documents']}")
    print(f"      置信度: {compacted['confidence']}")

    # Step 4: 评估结果
    print(f"\n🏁 评估:")
    has_relevant_entity = any(
        "全品" in e.get("name", "") or "全品" in e.get("type", "")
        for e in compacted["entities"]
    )
    has_brand_fact = any(
        ("品牌" in str(f.get("value", "")) or "品牌" in str(f.get("predicate", "")))
        and ("百事" in str(f.get("value", "")) or "7个" in str(f.get("value", "")) or "七喜" in str(f.get("value", "")))
        for f in compacted["facts"]
    )
    has_cold_drink = any(
        "冷饮" in str(f.get("value", "")) or "冷饮" in str(f.get("predicate", ""))
        for f in compacted["facts"]
    )

    if has_relevant_entity and has_brand_fact:
        print("   ✅ 成功！找到了全品C的冷饮品牌信息")
    elif has_relevant_entity and not has_brand_fact:
        print("   ⚠️  找到了全品C实体，但 facts 里没有品牌信息")
    elif not has_relevant_entity and has_brand_fact:
        print("   ⚠️  通过 fact_boost 找到了品牌信息，但实体匹配较弱")
    else:
        print("   ❌ 失败！未找到全品C冷饮品牌信息 — 需要检查 Neo4j 入库数据")
    print()


# ── 主入口 ───────────────────────────────────────────────
if __name__ == "__main__":
    question = "全品c的产品的冷饮有哪些品牌"

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║     Neo4j 本体检索模拟 — 验证修复效果                          ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print("║  修复内容:                                                      ║")
    print("║  1. Cypher: ORDER BY relevance DESC before LIMIT               ║")
    print("║  2. Cypher: aliases_text 大小写不敏感                          ║")
    print("║  3. Cypher: fact_boost — 独立事实级搜索                        ║")
    print("║  4. 证据压缩: 字符级 → 词级相关性排序                          ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    run_scenario(SCENARIO_A, question)
    run_scenario(SCENARIO_B, question)
    run_scenario(SCENARIO_C, question)

    print("─" * 72)
    print("📊 结论:")
    print("  场景A (全品C实体存在): 应该 ✅ — 修复后能正确排序并搜到")
    print("  场景B (全品C实体不存在): 仍然 ❌ — 需要检查 Neo4j 入库")
    print("  场景C (fact_boost提权): 应该 ✅ — 品牌墙被挤到后面，全品C优先")
    print()
    print("  ⚡ 关键: 如果实际 Neo4j 里没有「全品C」实体，需要先跑 ontology 入库任务。")
    print("     修复确保了「有数据时能搜到」，但不解决「数据不存在」的问题。")
