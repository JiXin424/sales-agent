# 部分命中实体的 Web 缺口补全 — 设计文档

- **日期**：2026-07-10
- **状态**：已通过设计评审，待实现
- **作者**：gitea-admin（brainstorming 产出）
- **相关**：网络搜索（博查 Bocha）触发机制

> 行号为设计调研时（2026-07-10）的近似值，实现前请用当前代码核对。

## 1. 背景与问题

用户提问「全品C 和某品牌的区别」这类问题时：

- 全品C 在我们的知识图谱（ontology）里有数据；
- 某品牌（X品牌）在 KB 没有数据。

期望行为：对 X品牌 这种**未命中**的实体单独走文本 Web 搜索（博查 Bocha）补资料，与全品C 的 KB 数据**合并**后生成回答。

### 当前系统行为（为何现在做不到）

1. **缺口不可见**：ontology 路的 `extract_terms_node`（`graph/retrieval/ontology_graph.py:45`）会抽出 `search_terms`（如 `["全品C", "X品牌"]`），但 Cypher 匹配（`ontology/repository.py:82`）是「任一 term 命中即出结果、按命中数排序」，**不记录哪个 term 零命中**。命中的实体清单 `compacted_evidence.entities` 只在 `_retrieve_via_ontology` 的局部变量里，进 state 前就被拍平成字符串（`graph/chat/nodes/retrieval.py:204`）。所以「X品牌 没数据」在管线里谁都不知道。
2. **对比类被锁死在 KB**：`task_router.py:109` 的正则 `(竞品|竞争对手|对比|区别)` 强制 `knowledge_policy=required`；`evidence_router_prompt.py:75` 明确「对比不设 web」。于是这类 query 走 ontology/RAG，全品C 命中、X品牌 落空。
3. **现有 web 兜底是「全空才触发 + 整体替换」**：`web_fallback_and_analyze`（`graph/retrieval/web_fallback.py:19`）只在 `not entities and not facts` 时触发（`retrieval.py:242`），且用**整句**当查询词（`web_fallback.py:43`），触发后 `return` **整体替换** KB 结果（`retrieval.py:251`）。本 case 因为 `sources > 0`（全品C 在），根本不会进 web 分支；即便进了，也无法「KB 给全品C + web 补 X品牌」并存。
4. `evidence_gate`（`graph/chat/nodes/evidence_gate.py:39`）只做二元判断（`len(sources) == 0` 才拦截），看不到 per-entity 覆盖率。

部署实况（确认）：`.env` 为 `KNOWLEDGE_ENGINE=ontology_neo4j` + `HYBRID_RETRIEVAL=true`，所有 tenant 一致 → 实际跑 **hybrid（ontology + RAG 并行 fan-out）**。因此 ontology 路的实体抽取与命中实体清单本来就现成可用。

## 2. 目标与非目标

### 目标
- 任何 query，当 ontology 命中部分实体、但 `extract_terms` 抽出的另一些「品牌/产品类」实体在 KB 无命中时，对**未命中实体**定向走 Bocha Web 搜索，结果与 KB 结果**合并**后交给生成。
- 不限对比类（用户已确认范围 = 任何提到品牌/产品但缺失的查询）。
- 答案呈现：无缝合并，靠现有 `source_type` 引用区分「知识库 / 网络搜索」（用户已确认）。

### 非目标
- 不改 `policy=web`（整句域外）主路径、`policy_guard`、`evidence_router_prompt`、`ChatGraphState` schema。
- 不引入 per-entity 路由（放弃方案 C）。
- 不改 evidence_gate 为 per-entity 覆盖率判断（放弃方案 B）。
- 不新增 Web provider（继续只用 Bocha）。

### 成功标准
「全品C 和 X品牌 区别」→ 回答同时含全品C（KB 事实）与 X品牌（web 资料）；引用区分别标「知识库」「网络搜索」；全品C 的 KB 数据不被覆盖。全实体都在 KB 的 query 不会触发任何 web 调用。

## 3. 方案选型

三个候选（详见 brainstorming 记录）：

- **方案 A（采用）**：ontology 路内缺口检测 + 定向 web + 合并。最小改动，复用 `web_fallback_and_analyze`，不动 state schema。hybrid 下 ontology 必跑，全覆盖。
- 方案 B：改 state schema + reducer，在 evidence_gate 后置检测。改动面大。
- 方案 C：Evidence Router 输出 per-entity 策略 + Send 路由。过度工程。

**采用方案 A。**

## 4. 架构与数据流

缺口补全挂在 `_retrieve_via_ontology`（`graph/chat/nodes/retrieval.py:138`）内部，`compact_evidence` 子图跑完之后（约 `retrieval.py:189` 之后）。

```
_retrieve_via_ontology:
  ① ontology 子图 → search_terms + compacted_evidence{entities, facts, ...}
  ② 用 KB 事实构建 kb_text + kb_sources（即使为空也建，不再特殊判空）
  ③ missing = compute_missing(search_terms, matched_entity_names, max_n=N)
  ④ 若 missing 非空 且 settings.web_search.enabled：
        对 missing 中每个实体（compute_missing 已封顶到 N）→
            web_fallback_and_analyze(
                search_query="{实体} 产品 功能 介绍",
                context_message=<原 standalone_query>,
                ... )
        汇总 web_text + web_sources
  ⑤ ontology_context_text = kb_text + web_text
     sources = kb_sources + web_sources
  ⑥ 返回（不再 early-return 替换）
```

**关键变化**：当前的「全空才触发 + early-return 整体替换」（`retrieval.py:242-263`）被统一的「先建 KB 块，再按缺口追加 web 块」取代。原「全空」case 自然退化成「所有实体都 missing → web 填全部」，行为兼容。

数据流复用现有字段，**不改 state schema**：
- `ontology_context_text`（`state.py:109`，reducer `_reduce_coalesce`）：ontology 路是唯一写者，合并后的文本直接写入，无并行写冲突。
- `sources`（`state.py:108`，reducer `add`）：web 项自带 `source_type="web"`，KB 项 `source_type="ontology"`，并存于同一 list，引用渲染天然区分（`integrations/dingtalk/citation.py:12`）。

## 5. 组件与契约

### 5.1 新增模块 `graph/retrieval/gap_fill.py`

单一职责、可独立单测的纯函数集合：

- `is_entity_like(term: str) -> bool`
  停用词过滤 + 最小长度。停用词表包含：`区别 / 对比 / 比较 / 怎么样 / 介绍 / 哪个好 / vs / 和 / 的 / 区别是什么 / 产品 / 功能`（实现时再补全）。非实体词直接剔除，不进 missing 计算。
- `is_covered(term: str, matched_names: list[str]) -> bool`
  大小写不敏感的**双向** `in` 子串匹配（对齐 Cypher `CONTAINS` 语义）：`term in name or name in term`。
- `compute_missing(search_terms: list[str], matched_entity_names: list[str], *, max_n: int) -> list[str]`
  `[t for t in search_terms if is_entity_like(t) and not is_covered(t, matched_entity_names)][:max_n]`，保持原顺序、去重。

### 5.2 改 `web_fallback_and_analyze`（`graph/retrieval/web_fallback.py:19`）

新增两个**可选**参数，默认值保证现有 3 个调用点（`retrieval.py:106 / 244 / 340`）零改动：

```python
async def web_fallback_and_analyze(
    *,
    message: str,
    search_query: str | None = None,   # 新增：传给 Bocha 的查询词，缺省退回 message
    context_message: str | None = None, # 新增：喂给分析 LLM 的原始问题，缺省退回 message
    ...
) -> dict | None:
    bocha_q = search_query or message
    analysis_ctx = context_message or message
    web_result = await bocha_search(query=bocha_q, ...)
    # 分析 LLM 用 analysis_ctx 保留原始意图（如「哪个适合中小企业」）
```

这样缺口补全可传 `search_query="X品牌 产品 功能 介绍"`、`context_message=<原 standalone_query>`——搜索定向，分析保留对比意图。

### 5.3 改 `WebSearchConfig`（`core/config.py:139`）

新增：

```python
max_gap_entities: int = 2  # 每轮缺口补全最多补的实体数
```

经 env 可调（`config.py:389-396` 一带加 `BOCHA_MAX_GAP_ENTITIES` 覆盖，命名沿用现有约定）。`enabled` 复用现有开关。

### 5.4 改 `_retrieve_via_ontology`（`graph/chat/nodes/retrieval.py:138`）

按第 4 节数据流重写：移除「全空 early-return 替换」分支，改为统一「先建 KB 块 → 按缺口追加 web 块」。从 ontology 子图结果取 `search_terms` 与 `compacted_evidence["entities"]`（命中实体名）。

## 6. 边界与错误处理

- **Bocha 失败 / 无结果 / 未配 key**：`web_fallback_and_analyze` 返回 `None` → 跳过该实体，**静默回退纯 KB**，记 `logger.warning`，不阻断主流程。
- **封顶**：`max_gap_entities`（默认 2）防止一个 query 触发一堆 web 调用。
- **extract_terms 抽成一个整句 term（没拆开）**：该 term 不命中 → 退化成「整句 web」，与现状全空行为一致，可接受。
- **alias 命中风险**：`compacted_evidence` 只存 `name` 不存 aliases，少数靠 alias 命中的实体可能被 `is_covered` 误判为 missing → 顶多多一次冗余 web（KB 数据仍在，非正确性问题）。
  - **可选加固（非 MVP）**：让 `compact_evidence_node`（`ontology_graph.py:234`）也输出命中 aliases，`is_covered` 一并比对。
- **web 与 KB 共存**：靠 `source_type` 区分，无需改 state schema 或 reducer。
- **evidence_gate**：合并后 `sources > 0`，gate 放行；全品C 本就提供 KB source，行为不变。若 KB 全空但 web 填充，gate 亦放行——这正是期望（让 web 能撑起一个答案）。

## 7. 测试

### 单测（`graph/retrieval/test_gap_fill.py`）
`compute_missing` 表驱动：
- 正常拆分：`["全品C","X品牌"]` vs `["全品C"]` → `["X品牌"]`。
- 停用词过滤：含「区别/对比」被剔除，不进 missing。
- 双向子串匹配：term 与 name 互为子串都算 covered。
- `max_n` 封顶：3 个 missing + `max_n=2` → 返回 2 个，保持顺序。
- 全空 / 全 covered 边界。

### 集成测试（mock Bocha + 分析 LLM）
- ontology 只回全品C → 断言对 X品牌 触发 web、合并后 context 同时含 KB 块与 `## 联网搜索分析` 块、`sources` 同时含 `source_type=ontology` 与 `source_type=web`。
- `web_fallback_and_analyze` 新参数：验证 `search_query` 传给 Bocha、`context_message` 传给分析 LLM；不传时退回 `message`（兼容旧调用点）。

### 回归
- 全实体都在 KB → 断言 Bocha **未被调用**。
- 全空（无任何命中）→ 断言仍产出 web-only 答案（走统一新路径）。

### Eval
- fuduoduo eval 套件加一条「全品C 和 `<未知品牌>` 区别」case，断言回答提及该未知品牌（来自 web）。
  - 注意：deepeval 自评可能因无 OpenAI key 走 deepseek 自评有偏差（见 memory `test-fuduoduo-eval`）。

## 8. 上线与回滚

- **无数据库变更**：不涉及 Alembic migration。
- **配置**：`max_gap_entities` 默认 2，`enabled` 复用现有。回滚 = 关 `web_search.enabled` 即整体关闭缺口补全（退化回纯 KB）。
- **灰度**：可先在 test tenant（fuduoduo）验证 stream 容器日志（`docker logs <tenant>-stream`）确认无 crash、缺口补全日志正常，再推 dev/prod（遵循 `dev-deploy-verify-flow`）。
- **验证入口**：本项目生产主入口是钉钉 Stream，验证必须查 stream 容器日志，不能只看 HTTP 200（见 CLAUDE.md §4）。

## 9. 验收检查清单（DoD）

- [ ] `gap_fill.py` 单测全过。
- [ ] 集成测试：部分命中 → web 补 X品牌 → 合并 context + 双 source_type。
- [ ] 回归：全命中不触发 web；全空仍出 web-only。
- [ ] `web_fallback_and_analyze` 旧 3 调用点行为不变。
- [ ] test tenant stream 容器跑通「全品C vs 未知品牌」无 crash。
- [ ] 更新 `README.md` 产品文档对照节 + `changelog/2026-07-10.md`（CLAUDE.md 强制）。
- [ ] 实现走 worktree 隔离 → 合回 main（CLAUDE.md 强制）。

## 10. 未来加固（非本期）

- `compact_evidence_node` 输出命中 aliases，提高 `is_covered` 精度。
- 实体类型识别（只对 `type=brand/product` 的实体做缺口补全），进一步降低误触发。
- web 查询模板按原句意图动态构造（而非固定「产品 功能 介绍」）。
- 缺口补全命中缓存（同一未知品牌短时间复用 web 结果），降本。
