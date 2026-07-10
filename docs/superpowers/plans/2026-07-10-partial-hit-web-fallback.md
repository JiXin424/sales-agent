# 部分命中实体的 Web 缺口补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 ontology 只命中 query 中的部分实体时（如「全品C 和 X品牌 区别」，全品C 在 KB、X品牌 不在），对未命中实体定向走 Bocha web 搜索，与 KB 结果合并后交给生成。

**Architecture:** 在 `_retrieve_via_ontology` 内、`compact_evidence` 之后做缺口检测——用 `search_terms` 减去命中实体名得到「未命中实体」，对每个未命中实体调 `web_fallback_and_analyze`（定向 `search_query` + 保留原句意图的 `context_message`），把 web 的 context 块与 sources **追加合并**到 KB 结果（取代现有「全空才触发 + 整体替换」）。新增纯函数模块 `gap_fill.py`，给 `web_fallback_and_analyze` 加两个可选参数（旧调用点零改动），给 `WebSearchConfig` 加 `max_gap_entities`。不改 state schema / reducer / evidence_router_prompt。

**Tech Stack:** Python 3（async）、LangGraph、pytest + `@pytest.mark.asyncio` + monkeypatch、pydantic settings（`AppConfig`）、Bocha web search API。

## Global Constraints

- 后端 Python；本特性纯后端，无前端改动。
- **无数据库变更**——不涉及 Alembic migration。
- 实现在 worktree 中进行（CLAUDE.md 强制）：`EnterWorktree` → 完成 → 合回 main → `ExitWorktree`。主目录共享，禁用 `git reset --hard / git checkout -- . / git stash`。
- 每个任务结束 commit；commit message 用 `feat/fix/test/docs:` 前缀。
- 旧调用点行为兼容：`web_fallback_and_analyze` 新增参数全部可选且有默认值；`_retrieve_via_rag` 路径不动。
- 完成前必须更新 `README.md` 的「产品文档对照」节 + 新建 `changelog/2026-07-10.md`（CLAUDE.md 强制）。
- 部署为 hybrid（`KNOWLEDGE_ENGINE=ontology_neo4j` + `HYBRID_RETRIEVAL=true`），ontology 路必跑，故缺口补全覆盖所有相关 query。

---

## File Structure

- **Create** `src/sales_agent/graph/retrieval/gap_fill.py` — 纯函数缺口检测（`is_entity_like` / `is_covered` / `compute_missing`）。单一职责、无 IO、可独立单测。
- **Create** `tests/unit/graph/test_gap_fill.py` — 上述纯函数的表驱动单测。
- **Modify** `src/sales_agent/services/...` 无；**Modify** `src/sales_agent/prompts/web_analysis_prompt.py` — 加 `{user_question}` 占位符，让分析 LLM 能看到原句意图。
- **Modify** `src/sales_agent/graph/retrieval/web_fallback.py` — `web_fallback_and_analyze` 加可选 `search_query` / `context_message`，分析渲染传入 `user_question`。
- **Modify** `src/sales_agent/core/config.py` — `WebSearchConfig` 加 `max_gap_entities: int = 2`；env 覆盖 `BOCHA_MAX_GAP_ENTITIES`。
- **Modify** `src/sales_agent/graph/chat/nodes/retrieval.py` — `_retrieve_via_ontology` 重写尾部：缺口检测 + 定向 web + 合并，取代「全空 early-return 替换」。
- **Modify** `tests/unit/graph/test_retrieval_web_fallback.py` — 更新既有测试适配新契约 + 新增部分命中测试。
- **Modify** `tests/unit/test_web_fallback.py` — 新增 `search_query` / `context_message` 参数测试。
- **Modify** fuduoduo eval 套件 — 加一条部分命中 case（可选，见 Task 5）。
- **Modify** `README.md` + **Create** `changelog/2026-07-10.md` — 文档与升级日志（DoD）。

---

## Task 1: 纯函数缺口检测模块 `gap_fill.py`

**Files:**
- Create: `src/sales_agent/graph/retrieval/gap_fill.py`
- Test: `tests/unit/graph/test_gap_fill.py`

**Interfaces:**
- Produces:
  - `is_entity_like(term: str) -> bool`
  - `is_covered(term: str, matched_names: list[str]) -> bool`
  - `compute_missing(search_terms: list[str], matched_entity_names: list[str], *, max_n: int) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/graph/test_gap_fill.py`:

```python
"""gap_fill：从 search_terms 找出 KB 未命中的实体候选。"""

from sales_agent.graph.retrieval.gap_fill import (
    compute_missing,
    is_covered,
    is_entity_like,
)


def test_is_entity_like_drops_generic_words():
    assert is_entity_like("区别") is False
    assert is_entity_like("对比") is False
    assert is_entity_like("VS") is False  # 大小写不敏感
    assert is_entity_like("怎么样") is False
    assert is_entity_like("的") is False  # 太短 + 停用词
    assert is_entity_like("全品C") is True


def test_is_covered_substring_either_direction():
    assert is_covered("全品C", ["全品C旗舰版"]) is True   # term 是 name 子串
    assert is_covered("全品C旗舰版", ["全品C"]) is True   # name 是 term 子串
    assert is_covered("X品牌", ["全品C"]) is False
    assert is_covered("X品牌", []) is False


def test_compute_missing_basic_split():
    # 全品C 命中、X品牌 缺失
    missing = compute_missing(["全品C", "X品牌"], ["全品C"], max_n=2)
    assert missing == ["X品牌"]


def test_compute_missing_filters_generic_terms():
    # 「区别」被剔除，不进 missing
    missing = compute_missing(["全品C", "区别"], [], max_n=2)
    assert missing == ["全品C"]


def test_compute_missing_caps_at_max_n():
    missing = compute_missing(["A", "B", "C"], [], max_n=2)
    assert missing == ["A", "B"]


def test_compute_missing_preserves_order_and_dedupes():
    missing = compute_missing(["A", "A", "B"], [], max_n=5)
    assert missing == ["A", "B"]


def test_compute_missing_all_covered_returns_empty():
    assert compute_missing(["全品C"], ["全品C"], max_n=2) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/graph/test_gap_fill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sales_agent.graph.retrieval.gap_fill'`

- [ ] **Step 3: Write minimal implementation**

Create `src/sales_agent/graph/retrieval/gap_fill.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/graph/test_gap_fill.py -v`
Expected: PASS（7 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/retrieval/gap_fill.py tests/unit/graph/test_gap_fill.py
git commit -m "feat(retrieval): add gap_fill 纯函数缺口检测模块"
```

---

## Task 2: `WebSearchConfig.max_gap_entities` + env 覆盖

**Files:**
- Modify: `src/sales_agent/core/config.py:139-144`（WebSearchConfig）和 `:394-396`（env 覆盖段）
- Test: `tests/unit/test_web_search_config.py`（新建）

**Interfaces:**
- Produces: `settings.web_search.max_gap_entities: int`（默认 2），env `BOCHA_MAX_GAP_ENTITIES` 可覆盖。

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_web_search_config.py`:

```python
"""WebSearchConfig.max_gap_entities 默认值与 env 覆盖。"""

from sales_agent.core.config import WebSearchConfig


def test_max_gap_entities_default():
    assert WebSearchConfig().max_gap_entities == 2


def test_max_gap_entities_override():
    cfg = WebSearchConfig(max_gap_entities=3)
    assert cfg.max_gap_entities == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_web_search_config.py -v`
Expected: FAIL with `AttributeError: 'WebSearchConfig' object has no attribute 'max_gap_entities'`（或默认值断言失败）

- [ ] **Step 3: Write minimal implementation**

Edit `src/sales_agent/core/config.py`，给 `WebSearchConfig` 加字段：

```python
class WebSearchConfig(BaseModel):
    """联网搜索兜底配置（Bocha API）。"""

    enabled: bool = True
    api_key: str = ""
    top_n: int = 5
    max_gap_entities: int = 2  # 部分命中时，每轮最多 web 补全的未命中实体数
```

同文件 env 覆盖段（`BOCHA_TOP_N` 处理之后，约 396 行）追加：

```python
        web_search_top_n = os.getenv("BOCHA_TOP_N", "")
        if web_search_top_n:
            raw.setdefault("web_search", {})["top_n"] = int(web_search_top_n)
        web_search_max_gap = os.getenv("BOCHA_MAX_GAP_ENTITIES", "")
        if web_search_max_gap:
            raw.setdefault("web_search", {})["max_gap_entities"] = int(web_search_max_gap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_web_search_config.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/core/config.py tests/unit/test_web_search_config.py
git commit -m "feat(config): WebSearchConfig 加 max_gap_entities + BOCHA_MAX_GAP_ENTITIES env"
```

---

## Task 3: `web_fallback_and_analyze` 支持定向 query + 原句上下文

**Files:**
- Modify: `src/sales_agent/graph/retrieval/web_fallback.py:19-89`
- Modify: `src/sales_agent/prompts/web_analysis_prompt.py`（加 `{user_question}`）
- Test: `tests/unit/test_web_fallback.py`（追加）

**Interfaces:**
- Consumes: 无前置依赖。
- Produces: `web_fallback_and_analyze(*, message, tenant_id, runtime, api_key, top_n=5, search_query=None, context_message=None)`。`search_query` 为 Bocha 查询词（缺省 `message`）；`context_message` 为分析 LLM 的用户问题（缺省 `message`）。旧调用点（`retrieval.py:106 / 244(before edit) / 340`）不传这两个参数 → 行为：Bocha 仍用 `message`，分析 prompt 现在额外带上 `message` 作为用户问题（顺带补全了原 prompt「与用户问题相关」却收不到问题的缺口，属正向改进）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/unit/test_web_fallback.py` 末尾：

```python
@pytest.mark.asyncio
async def test_web_fallback_uses_search_query_for_bocha(monkeypatch):
    """search_query 传给 Bocha，不传时退回 message。"""
    captured = {}

    async def fake_bocha(*, query, api_key, top_n):
        captured["query"] = query
        return WebSearchResult(
            query=query, success=True, raw_answer="",
            sources=[{"title": "T", "url": "u"}],
        )
    monkeypatch.setattr("sales_agent.graph.retrieval.web_fallback.bocha_search", fake_bocha)

    runtime = _FakeRuntime(_FakeChatModel('{"analysis":"a","has_relevant":true}'))
    await web_fallback_and_analyze(
        message="全品C和X品牌区别",
        tenant_id="t1", runtime=runtime, api_key="k",
        search_query="X品牌 产品 功能 介绍",
    )
    assert captured["query"] == "X品牌 产品 功能 介绍"


@pytest.mark.asyncio
async def test_web_fallback_defaults_search_query_to_message(monkeypatch):
    """不传 search_query → Bocha 用 message。"""
    captured = {}

    async def fake_bocha(*, query, api_key, top_n):
        captured["query"] = query
        return WebSearchResult(
            query=query, success=True, raw_answer="",
            sources=[{"title": "T", "url": "u"}],
        )
    monkeypatch.setattr("sales_agent.graph.retrieval.web_fallback.bocha_search", fake_bocha)

    runtime = _FakeRuntime(_FakeChatModel('{"analysis":"a","has_relevant":true}'))
    await web_fallback_and_analyze(
        message="某问题", tenant_id="t1", runtime=runtime, api_key="k",
    )
    assert captured["query"] == "某问题"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_web_fallback.py::test_web_fallback_uses_search_query_for_bocha -v`
Expected: FAIL with `TypeError: ... got an unexpected keyword argument 'search_query'`

- [ ] **Step 3: Write minimal implementation**

Edit `src/sales_agent/graph/retrieval/web_fallback.py` 签名与查询词：

```python
async def web_fallback_and_analyze(
    *,
    message: str,
    tenant_id: str,
    runtime: Runtime,
    api_key: str,
    top_n: int = 5,
    search_query: str | None = None,
    context_message: str | None = None,
) -> dict | None:
    """调 Bocha 搜索 + LLM 分析，返回拼好 analysis 的 context dict。

    Args:
        message: 用户问题（默认搜索词与分析上下文的兜底）。
        tenant_id: 租户 ID（用于解析 web_analysis prompt）。
        runtime: LangGraph runtime，需 context 含 chat_model。
        api_key: Bocha API key，为空则跳过。
        top_n: 搜索结果数。
        search_query: 传给 Bocha 的查询词；缺省退回 message。
            部分命中缺口补全用「{实体} 产品 功能 介绍」定向搜索。
        context_message: 喂给分析 LLM 的用户问题；缺省退回 message，
            让分析保留原句意图（如「哪个适合中小企业」）。

    Returns:
        {"ontology_context_text": str, "sources": list, "web_used": True} 或 None
        （未启用/搜索失败/无结果时返回 None）。
    """
    if not api_key:
        return None

    web_result = await bocha_search(query=search_query or message, api_key=api_key, top_n=top_n)
```

同文件，分析渲染处（`rendered = template.format(...)`）改为传入用户问题：

```python
    rendered = template.format(
        search_results=search_text,
        user_question=context_message or message,
    )
```

Edit `src/sales_agent/prompts/web_analysis_prompt.py`，在开头插入用户问题段（其余不变）：

```python
WEB_ANALYSIS_PROMPT = """你是网络信息分析专家。你处理的是互联网公开搜索结果。

## 用户问题

{user_question}

## 分析原则
```
（其余 `{search_results}` 占位符与 JSON 输出格式保持不变。）

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_web_fallback.py -v`
Expected: PASS（含原有 3 个 + 新增 2 个）。注意：`.format(search_results=..., user_question=...)` 对 DB 中无 `{user_question}` 占位符的自定义 prompt 安全——多余 kwarg 被忽略。

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/retrieval/web_fallback.py src/sales_agent/prompts/web_analysis_prompt.py tests/unit/test_web_fallback.py
git commit -m "feat(web-fallback): 支持定向 search_query + context_message 保留原句意图"
```

---

## Task 4: `_retrieve_via_ontology` 缺口检测 + 定向 web + 合并

**Files:**
- Modify: `src/sales_agent/graph/chat/nodes/retrieval.py:204-282`（KB 块构建 + 尾部 web 兜底 + return）
- Modify: `tests/unit/graph/test_retrieval_web_fallback.py`（更新既有 2 个 + 新增 3 个测试）

**Interfaces:**
- Consumes: Task 1 的 `compute_missing`；Task 2 的 `settings.web_search.max_gap_entities`；Task 3 的 `web_fallback_and_analyze(search_query=..., context_message=...)`。
- Produces: `_retrieve_via_ontology` 返回的 `ontology_context_text` 在部分命中时 = KB 块 + web 块；`sources` = KB sources（`source_type=ontology`）+ web sources（`source_type=web`）；`retrieval_info.web_search_used` 在缺口补全触发时为 True。

- [ ] **Step 1: Write the failing tests**

修改 `tests/unit/graph/test_retrieval_web_fallback.py`：

(a) `_fake_settings()` 增加 `max_gap_entities`：

```python
def _fake_settings():
    return SimpleNamespace(
        web_search=SimpleNamespace(enabled=True, api_key="sk-test", top_n=5, max_gap_entities=2),
        retrieval=SimpleNamespace(top_k=5, mode="hybrid"),
    )
```

(b) `test_ontology_empty_triggers_web_fallback`：把 `fake_web` 改为接受任意 kwargs（缺口补全会传 `search_query`/`context_message`）：

```python
    async def fake_web(**kwargs):
        return {"ontology_context_text": "## 联网搜索分析\n网搜结论", "sources": [{"title": "T", "source_type": "web"}], "web_used": True}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.web_fallback_and_analyze", fake_web)
```
（其余断言不变：`"网搜结论" in result["ontology_context_text"]` 与 `web_search_used is True`。）

(c) `test_ontology_nonempty_skips_web_fallback`：**契约变化**——「ontology 有事实」不再保证不调 web；保证不调 web 的是「所有 search_terms 都被命中实体覆盖」。把 `fake_extract` 的 search_terms 改成与命中实体一致：

```python
    async def fake_extract(local, runtime): return {"search_terms": ["E1"]}
```
（`fake_compact` 已返回 `entities=[{"name": "E1", ...}]`，故 "E1" 被 covered → missing 为空 → 不调 web。其余断言不变。）

(d) 新增 3 个测试，追加到文件末尾：

```python
@pytest.mark.asyncio
async def test_ontology_partial_hit_triggers_web_for_missing(monkeypatch):
    """全品C 命中、X品牌 缺失 → 对 X品牌 定向 web，KB+web 合并。"""
    async def fake_extract(local, runtime): return {"search_terms": ["全品C", "X品牌"]}
    async def fake_query(local, runtime): return {"graph_rows": [{"e": {"name": "全品C", "type": "Product"}}]}
    async def fake_vec(local, runtime): return {"graph_rows": [], "vector_fallback_used": False}
    def fake_compact(local):
        return {"compacted_evidence": {
            "entities": [{"name": "全品C", "type": "Product"}],
            "facts": [{"subject": "全品C", "predicate": "has", "object": "V"}],
            "source_documents": ["D1"], "confidence": 0.9,
        }}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.extract_terms_node", fake_extract)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.graph_query_node", fake_query)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.vector_fallback_node", fake_vec)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.compact_evidence_node", fake_compact)

    calls = []

    async def fake_web(**kwargs):
        calls.append(kwargs)
        return {"ontology_context_text": "## 联网搜索分析\nX品牌资料", "sources": [{"title": "X", "source_type": "web"}], "web_used": True}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.web_fallback_and_analyze", fake_web)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.get_settings", _fake_settings)

    runtime = _FakeRuntime()
    state = {"message": "全品C和X品牌区别", "tenant_id": "t1", "task_type": "knowledge_qa"}
    result = await _retrieve_via_ontology(state, runtime, "t1", None, "knowledge_qa", "全品C和X品牌区别")

    # 只对 X品牌 调一次，且 search_query 定向
    assert len(calls) == 1
    assert "X品牌" in calls[0]["search_query"]
    assert calls[0]["context_message"] == "全品C和X品牌区别"
    # KB 块（全品C）与 web 块（X品牌资料）并存
    assert "全品C" in result["ontology_context_text"]
    assert "X品牌资料" in result["ontology_context_text"]
    # sources 两种来源都有
    types = {s.get("source_type") for s in result["sources"]}
    assert "ontology" in types and "web" in types
    assert result["retrieval_info"]["web_search_used"] is True


@pytest.mark.asyncio
async def test_ontology_all_covered_skips_web(monkeypatch):
    """search_terms 全部被命中实体覆盖 → 不调 web。"""
    async def fake_extract(local, runtime): return {"search_terms": ["全品C"]}
    async def fake_query(local, runtime): return {"graph_rows": [{"e": {"name": "全品C", "type": "Product"}}]}
    async def fake_vec(local, runtime): return {"graph_rows": [], "vector_fallback_used": False}
    def fake_compact(local):
        return {"compacted_evidence": {
            "entities": [{"name": "全品C", "type": "Product"}],
            "facts": [], "source_documents": [], "confidence": 0.8,
        }}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.extract_terms_node", fake_extract)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.graph_query_node", fake_query)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.vector_fallback_node", fake_vec)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.compact_evidence_node", fake_compact)

    def fail_web(*a, **kw): raise AssertionError("web 不应在全部实体已覆盖时调用")
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.web_fallback_and_analyze", fail_web)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.get_settings", _fake_settings)

    runtime = _FakeRuntime()
    state = {"message": "全品C怎么样", "tenant_id": "t1", "task_type": "knowledge_qa"}
    result = await _retrieve_via_ontology(state, runtime, "t1", None, "knowledge_qa", "全品C怎么样")
    assert result["retrieval_info"]["web_search_used"] is False


@pytest.mark.asyncio
async def test_ontology_gap_fill_capped_by_max(monkeypatch):
    """3 个未命中实体，max_gap_entities=2 → web 只调 2 次。"""
    async def fake_extract(local, runtime): return {"search_terms": ["A", "B", "C"]}
    async def fake_query(local, runtime): return {"graph_rows": []}
    async def fake_vec(local, runtime): return {"graph_rows": [], "vector_fallback_used": True}
    def fake_compact(local):
        return {"compacted_evidence": {"entities": [], "facts": [], "source_documents": [], "confidence": 0.0}}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.extract_terms_node", fake_extract)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.graph_query_node", fake_query)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.vector_fallback_node", fake_vec)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.compact_evidence_node", fake_compact)

    calls = []

    async def fake_web(**kwargs):
        calls.append(kwargs)
        return {"ontology_context_text": "## 联网搜索分析\n资料", "sources": [{"title": "X", "source_type": "web"}], "web_used": True}
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.web_fallback_and_analyze", fake_web)
    monkeypatch.setattr("sales_agent.graph.chat.nodes.retrieval.get_settings", _fake_settings)

    runtime = _FakeRuntime()
    state = {"message": "A和B和C区别", "tenant_id": "t1", "task_type": "knowledge_qa"}
    await _retrieve_via_ontology(state, runtime, "t1", None, "knowledge_qa", "A和B和C区别")
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/graph/test_retrieval_web_fallback.py -v`
Expected: 新增 3 个测试 FAIL（`web_search_used` 仍为 False / web 未被调用 / 调用次数不符），且 (b)(c) 修改后既有 2 个仍可能因新代码未实现而 FAIL。

- [ ] **Step 3: Write minimal implementation**

Edit `src/sales_agent/graph/chat/nodes/retrieval.py`：

(1) 顶部 import 加 `compute_missing`：

```python
from sales_agent.graph.retrieval.ontology_graph import (
    extract_terms_node,
    graph_query_node,
    vector_fallback_node,
    compact_evidence_node,
)
from sales_agent.graph.retrieval.web_fallback import web_fallback_and_analyze
from sales_agent.graph.retrieval.gap_fill import compute_missing
```

(2) 重写 `_retrieve_via_ontology` 从「Step 5: Build ontology_context_text」到函数结尾（原 204-282 行），用如下内容替换：

```python
    # Step 5: Build KB context block from compacted evidence
    compacted = local.get("compacted_evidence", {})
    entities = compacted.get("entities", [])
    facts = compacted.get("facts", [])
    docs = compacted.get("source_documents", [])

    kb_lines: list[str] = []
    if entities or facts or docs:
        kb_lines.append("## 知识图谱（本体）检索结果")
        if entities:
            kb_lines.append(f"匹配实体 ({len(entities)}): " + ", ".join(
                f"{e.get('name', '')}({e.get('type', '')})" for e in entities[:20]
            ))
        if facts:
            kb_lines.append(f"相关事实 ({len(facts)}):")
            for f in facts[:15]:
                kb_lines.append(
                    f"  - [{f.get('subject', '')}] {f.get('predicate', '')} "
                    f"{f.get('object', '')} {f.get('value', '')}"[:200]
                )
        if docs:
            kb_lines.append(f"来源文档: {', '.join(docs[:10])}")
    kb_text = "\n".join(kb_lines)

    # Step 6: 缺口补全——对 KB 未命中的实体走定向 web 搜索。
    # 用 search_terms（期望实体）减去命中实体名，得到未命中实体；
    # 每个 web 结果的 context 块与 sources 追加合并到 KB 结果（非替换）。
    settings = get_settings()
    matched_names = [e.get("name", "") for e in entities]
    missing = compute_missing(
        local.get("search_terms", []),
        matched_names,
        max_n=settings.web_search.max_gap_entities,
    )

    web_text_parts: list[str] = []
    web_sources: list[dict] = []
    if missing and settings.web_search.enabled:
        writer({"phase": "web_gap_fill", "missing_entities": missing})
        for entity in missing:
            try:
                web_result = await web_fallback_and_analyze(
                    message=message,
                    search_query=f"{entity} 产品 功能 介绍",
                    context_message=message,
                    tenant_id=tenant_id,
                    runtime=runtime,
                    api_key=settings.web_search.api_key,
                    top_n=settings.web_search.top_n,
                )
            except Exception as e:
                logger.warning("Web gap-fill failed for entity=%r: %s", entity, e)
                web_result = None
            if web_result is not None:
                web_text_parts.append(web_result["ontology_context_text"])
                web_sources.extend(web_result["sources"])

    web_used = bool(web_text_parts)

    # 合并 context 文本：KB 块在前，web 块（## 联网搜索分析）在后。
    parts = [p for p in [kb_text, *web_text_parts] if p.strip()]
    ontology_context_text = "\n".join(parts)

    # Build KB sources from source_documents.
    # text 字段携带完整检索上下文（KB+web 合并），供 eval retrieval_context 使用；
    # 钉钉 renderer 仍取 title/display_title 做文末引用。
    sources = [
        {
            "document_id": "",
            "title": title,
            "display_title": title,
            "text": ontology_context_text,
            "score": compacted.get("confidence", 0.8),
            "source_type": "ontology",
        }
        for title in docs[:3]
    ]

    writer({
        "phase": "ontology_retrieval_complete",
        "entity_count": len(entities),
        "fact_count": len(facts),
        "web_search_used": web_used,
    })

    return {
        "retrieval_info": {
            "called": True,
            "provider": "ontology_neo4j",
            "vector_fallback_used": local.get("vector_fallback_used", False),
            "source_count": len(sources) + len(web_sources),
            "web_search_used": web_used,
        },
        "sources": sources + web_sources,
        "skip_generation": False,
        "ontology_context_text": ontology_context_text,
    }
```

注意：这删除了原「`if not entities and not facts and settings.web_search.enabled: ... return {...替换...}`」整块（原 240-263 行）。原「全空」语义由新路径自然覆盖——全空时 `kb_text=""`、所有 entity-like search_terms 进 missing → web 填全部 → 合并文本即 web 块。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/graph/test_retrieval_web_fallback.py -v`
Expected: PASS（更新后的 2 个 + 新增 3 个，共 5 个）。

再跑相邻检索测试，确认无回归：
Run: `pytest tests/unit/graph/test_retrieval_node.py tests/unit/test_web_fallback.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/chat/nodes/retrieval.py tests/unit/graph/test_retrieval_web_fallback.py
git commit -m "feat(retrieval): ontology 部分命中时对未命中实体定向 web 补全并合并"
```

---

## Task 5: eval 套件加部分命中 case（可选）

**Files:**
- Modify: fuduoduo eval 套件（先读现有 harness 确认文件与 fixture 格式）

**Interfaces:** 无新接口；复用 Task 4 的运行时行为。

> **可选**：单元/集成测试已确定性地覆盖了缺口补全契约，本任务为额外的端到端 eval 保险。已知约束：deepeval 在 fuduoduo 容器内运行，缺 OpenAI key 时走 deepseek 自评可能有偏差（见 memory `test-fuduoduo-eval`）。若当前迭代不便跑 eval，可记为后续跟进，不阻塞合回。

- [ ] **Step 1: 定位 eval harness**

Run: `find . -name "deepeval_eval.py" -o -name "*eval*.py" | grep -v __pycache__ | grep -iE "fuduoduo|deepeval"`
读现有 case 的 fixture 结构（输入消息、期望指标），照其格式新增一条。

- [ ] **Step 2: 加 case**

新增 case：输入「<KB 已知产品> 和 <未知品牌> 区别」（未知品牌用一个确定不在 ontology 中的名字），断言回答**同时提及**已知产品与未知品牌（未知品牌信息来自 web）。指标按既有 case 风格设（如 answerRelevance / 自定义断言）。

- [ ] **Step 3: 跑 eval（如环境允许）**

按 memory `test-fuduoduo-eval`：进 fuduoduo-api 容器直接调 `deepeval_eval.py`。若 deepseek 自评偏差大，仅确认 case 不报错、web 路径被触发即可，不强求指标全绿。

- [ ] **Step 4: Commit**

```bash
git add <eval 文件>
git commit -m "test(eval): 加部分命中实体 web 缺口补全 case"
```

---

## Task 6: 文档与升级日志（DoD）

**Files:**
- Modify: `README.md`（「产品文档对照」节 + 「更新日志」索引）
- Create: `changelog/2026-07-10.md`

**Interfaces:** 无。

- [ ] **Step 1: 写升级日志**

Create `changelog/2026-07-10.md`：

```markdown
# 2026-07-10 升级日志

## 联网搜索：部分命中实体缺口补全

- **改动对象**：ontology 检索路径（`graph/chat/nodes/retrieval.py`）
- **类型**：功能增强
- **影响范围**：hybrid 部署（ontology+RAG）下，当 query 提到的品牌/产品实体只有部分在知识图谱命中时
- **改动明细**：
  - 新增 `graph/retrieval/gap_fill.py`（`compute_missing`/`is_covered`/`is_entity_like` 纯函数缺口检测）
  - `_retrieve_via_ontology` 在 `compact_evidence` 之后做缺口检测，对未命中实体定向调 `web_fallback_and_analyze`（查询词 `"{实体} 产品 功能 介绍"`，分析保留原句意图），结果与 KB 结果**追加合并**（取代原「全空才触发 + 整体替换」）
  - `web_fallback_and_analyze` 新增可选 `search_query` / `context_message`（旧调用点零改动）
  - `web_analysis_prompt` 加 `{user_question}` 段
  - `WebSearchConfig` 新增 `max_gap_entities`（默认 2），env `BOCHA_MAX_GAP_ENTITIES` 可覆盖
- **原因**：用户问「全品C 和某品牌 区别」时，全品C 在 KB、某品牌 不在，原管线只拿全品C 素材硬答；现对缺失实体走 web 补资料，引用区按来源标「知识库 / 网络搜索」。web 失败/无结果时静默回退纯 KB，不阻断主流程。
```

- [ ] **Step 2: 更新 README**

按 `README.md` 现有「产品文档对照」与「更新日志」节的格式，追加/更新一条对应本特性的说明（状态、影响范围、日期），并在「更新日志」索引指向 `changelog/2026-07-10.md`。具体措辞参照该文件既有条目风格。

- [ ] **Step 3: 跑全量检索相关测试确认无回归**

Run: `pytest tests/unit/graph/ tests/unit/test_web_fallback.py tests/unit/test_web_search_config.py -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add README.md changelog/2026-07-10.md
git commit -m "docs: 部分命中实体 web 缺口补全的 README + 升级日志"
```

---

## 部署验证（合回 main 后）

- 本机 dev 部署只重建 prod2（taishan+taishankaifa2），不碰 test/prod3（见 memory `dev-deploy-verify-flow`）。
- **验证走生产主入口钉钉 Stream**：`docker logs <tenant>-stream` 确认容器连上、无 crash，且能看到 `web_gap_fill` phase 事件与 `ontology_retrieval_complete` 的 `web_search_used=true`。
- 构造一条「<已知产品> 和 <未知品牌> 区别」的真实消息，确认回复同时含两类素材、引用区分别标注。
- 回滚开关：设 `WEB_SEARCH_ENABLED=false` 或 `BOCHA_API_KEY` 清空即整体关闭缺口补全（退化回纯 KB）。

## DoD（全部勾选方可合回 main）

- [ ] Task 1-6 全部 commit，worktree 内测试全过
- [ ] `pytest tests/unit/graph/ tests/unit/test_web_fallback.py tests/unit/test_web_search_config.py` 全过
- [ ] README「产品文档对照」+ `changelog/2026-07-10.md` 已更新
- [ ] dev stream 容器跑通缺口补全，无 crash，日志可见 `web_gap_fill`
- [ ] 合回 main → `ExitWorktree`
```
