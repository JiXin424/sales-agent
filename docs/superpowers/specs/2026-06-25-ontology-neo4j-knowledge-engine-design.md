# Neo4j Ontology Knowledge Engine Design

Date: 2026-06-25
Status: Approved design for planning
Project: sales-agent
Related reference project: /root/code/OmniAgent/Ontology-minimal

## Summary

第一版将 `sales-agent` 的旧 Markdown chunk RAG 替换为内部 Neo4j Ontology 知识引擎。目标不是先做 Agentic planner，而是先建立稳定、可审计、可入库、可问答的图谱知识库底座，后续再在此基础上升级 Agentic Knowledge Graph。

核心决策：

- 采用方案 1：Ontology 能力作为 `sales-agent` 内部知识引擎模块接入，而不是独立微服务。
- Neo4j 是知识库主存储，保存实体、事实、证据、来源、向量和冲突关系。
- PostgreSQL 只保存业务元数据，例如 tenant、agent、prompt、会话、导入任务、上传文件记录和 Neo4j 配置状态。
- 第一版迁移 Ontology-minimal 的自动抽取链路，但不迁移其现有数据。
- 先用小样本/测试租户验证，不直接迁移 `taishan` 全量文档。
- 现有 `sales-agent` 逻辑里触发 RAG 的路径，第一版全部改为触发 Ontology 知识库。
- Ontology 引擎使用图谱检索为主，保守实体向量兜底；不回退旧 chunk RAG。
- 知识回答生成迁移 Ontology-minimal 的 `generate_response` 思路，但输出仍转换为 `sales-agent` 现有 `summary/sections` 格式。
- `console` 只做入库链路可视化和审计摘要，复杂图探索交给 Neo4j Bloom/Workspace。

## Goals

1. 用 Neo4j Ontology 知识库替代现有 chunk RAG 运行路径。
2. 保留 `sales-agent` 既有产品壳：HTTP API、钉钉、Agent 作用域、Prompt 管理、风控、日志、trace 和 console。
3. 让知识入库过程可观察：上传、解析、实体抽取、事实抽取、embedding、写入 Neo4j、冲突检测、完成或失败。
4. 建立可审计知识模型，让每条回答能追溯到事实、证据和来源。
5. 保持销售用户侧输出简洁，后台保留完整图谱证据。

## Non-Goals

1. 第一版不做 Agentic planner，不让模型自由规划多工具链路。
2. 第一版不迁移 `Ontology-minimal` 现有 `jiufeng` 或 `fuduoduo` 数据。
3. 第一版不全量导入 `taishan` 文档。
4. 第一版不自研大型图谱可视化编辑器。
5. 第一版不使用旧 Markdown chunk RAG 作为 fallback。

## Current Context

`sales-agent` 当前核心链路位于 `ChatPipeline`：请求校验、tenant/agent 解析、任务路由、路径选择、RAG 检索、生成、风险检查、日志和 trace。旧知识库是 Markdown 文档切块后用 pgvector 检索。

`Ontology-minimal` 已具备一套可参考的图谱知识能力：

- 文档解析和 LLM 抽取实体/关系。
- PostgreSQL + pgvector 存储实体、关系和实体 embedding。
- NetworkX 多跳图遍历。
- 意图分类、图谱上下文收集、回答生成。
- 租户配置驱动实体类型、关系类型、关键词、意图映射和竞品映射。
- 冲突记录和人工确认概念。

本设计迁移其核心思想，但不原样搬运运行时和存储。第一版以 Neo4j 为知识库主存储，并将知识引擎封装为 `sales-agent` 内部模块。

## Architecture

第一版新增三个内部服务边界：

- `OntologyIngestionService`：负责文档到 Neo4j 的入库流水线。
- `OntologyRetrievalService`：负责 Neo4j 图谱检索和保守实体向量兜底。
- `OntologyAnswerService`：负责图谱专用回答生成，并转为 `summary/sections`。

运行时主链路：

1. `ChatPipeline` 完成 validation、tenant/agent resolve、task routing、path routing。
2. 当 `path_result.needs_retrieval == true` 时，不再调用旧 `Retriever.retrieve_for_task()`。
3. 调用 `OntologyAnswerService.answer_for_task(...)`。
4. Ontology 引擎完成意图分类、核心实体查找、事实扩展、证据收集、保守向量兜底和回答生成。
5. 返回 `summary/sections`、简洁 sources 和后台 `graph_evidence`。
6. `ChatPipeline` 继续执行风险检查、日志、trace 和响应包装。

现有 `needs_retrieval` 机制是接入边界。第一版不重新定义任务范围，只替换现有 RAG 触发点背后的知识实现。

## Storage Boundaries

Neo4j 保存知识主数据：

- 实体节点。
- 事实节点。
- 证据节点。
- 来源文档节点。
- 实体向量。
- 冲突关系。
- 事实状态、置信度、版本和风险等级。

PostgreSQL 保存业务元数据：

- Tenant、Agent、Prompt、会话、feedback、run trace。
- 上传文件记录和导入任务。
- Neo4j 配置状态和知识引擎状态。
- 入库摘要、错误摘要和 console 列表所需轻量元数据。

文件系统或后续对象存储保存原始文档和规范化 Markdown。

## Neo4j Data Model

为满足知识审计要求，第一版采用 Fact 节点模型，而不是把业务关系直接建为 Neo4j 边属性。

### Entity

`(:Entity)` 表示可被问答引用的实体。

核心属性：

- `id`
- `tenant_id`
- `agent_id`
- `type`
- `name`
- `canonical_key`
- `aliases`
- `properties`
- `embedding`
- `status`
- `created_at`
- `updated_at`

### Fact

`(:Fact)` 表示可审计事实。它可以是关系事实、属性声明或普通 claim。

核心属性：

- `id`
- `tenant_id`
- `agent_id`
- `predicate`
- `fact_type`: `relation | attribute | claim`
- `value`
- `confidence`
- `status`: `active | pending_review | rejected | archived`
- `risk_level`: `low | medium | high`
- `version`
- `version_date`
- `created_at`
- `updated_at`

核心结构：

```cypher
(:Entity)-[:SUBJECT_OF]->(:Fact)-[:OBJECT_OF]->(:Entity)
```

属性型事实可以没有 object entity，用 `Fact.value` 表达字面值。

### Evidence And SourceDocument

`(:Evidence)` 表示抽取证据片段。

核心属性：

- `id`
- `excerpt`
- `locator`
- `confidence`
- `extraction_method`
- `created_at`

`(:SourceDocument)` 表示来源文档。

核心属性：

- `id`
- `tenant_id`
- `agent_id`
- `title`
- `source_file_id`
- `source_path`
- `content_hash`
- `status`
- `created_at`

证据结构：

```cypher
(:Fact)-[:SUPPORTED_BY]->(:Evidence)-[:FROM]->(:SourceDocument)
```

### Conflicts

冲突围绕 Fact 表达：

```cypher
(:Fact)-[:CONFLICTS_WITH {
  conflict_type,
  severity,
  status,
  resolution
}]->(:Fact)
```

这样后台可以直接审计一条事实：它说了什么、来自哪里、谁支持它、是否与其他事实冲突、是否已确认。

### Indexes And Constraints

建议索引：

- Entity 唯一约束：`tenant_id + canonical_key + type`。
- Entity 全文索引：`name`、`aliases` 和常用文本属性。
- Entity 向量索引：`embedding`。
- 普通索引：`tenant_id`、`agent_id`、`type`、`status`。
- Fact 普通索引：`tenant_id`、`agent_id`、`predicate`、`status`、`risk_level`。

所有 Neo4j 查询必须带 `tenant_id`。启用 Agent 知识范围时，也要带 `agent_id` 或对应 scope 过滤。

## Ingestion Pipeline

第一版迁移 Ontology-minimal 的自动抽取思路，写入 Neo4j Fact 模型。

流程：

1. 用户在 `console` 上传或选择小样本文档。
2. PostgreSQL 创建 `IngestionJob`，记录 tenant、agent、文件、状态和统计。
3. 文档解析为 Markdown，保存 `SourceFile` 和对应 `SourceDocument` 元数据。
4. LLM 抽取实体候选：类型、名称、别名、属性、置信度和证据片段。
5. LLM 抽取事实候选：predicate、subject、object/value、证据和置信度。
6. 实体归一化：按 `tenant_id + canonical_key + type` 合并同一实体。
7. 生成实体 embedding，写入 Neo4j vector index。
8. 写入 Neo4j 的 Entity、Fact、Evidence 和 SourceDocument。
9. 执行冲突检测，按风险分级处理。
10. `console` 展示进度、统计、错误和冲突摘要。

入库不因冲突整体失败。冲突只影响相关 Fact 的 `status` 和检索可用性。

## Conflict Strategy

冲突按风险级别处理：

- 低风险：自动合并并标记 `active`。例如别名、新来源、补充说明、同一实体新增非关键属性。
- 中风险：入库为 `pending_review`，默认不进入回答生成或检索时降权。例子包括价格区间不同、案例数据口径不同、竞品描述不一致。
- 高风险：创建 `CONFLICTS_WITH`，新 Fact 保持 `pending_review`，默认不覆盖旧 active Fact。例子包括资质认证、政策条款、交付承诺、价格承诺、合规边界和关键技术指标冲突。

回答生成默认只使用 `status=active` 的 Fact。`pending_review` Fact 可在后台审计，不直接支撑销售回答。

## Runtime Retrieval And Answering

`OntologyAnswerService` 是运行时入口。

流程：

1. 使用 Ontology 意图分类得到 `ontology_intent` 和候选实体。
2. 通过 Neo4j 全文索引、名称和别名匹配核心实体。
3. 使用关键词拆分和类型推断补充实体。
4. 从核心实体扩展相关 Fact、对象实体、证据和来源。
5. 核心实体召回不足时，触发保守实体向量兜底。
6. 过滤 `tenant_id`、`agent_id/scope` 和 `status`。
7. 生成图谱上下文。
8. 调用 Ontology 专用回答 prompt 生成答案。
9. 转换为 `summary/sections`。
10. 返回简洁 sources 和后台 `graph_evidence`。

保守向量兜底触发条件：

- 名称、别名和全文匹配没有核心实体。
- 核心实体数量低于阈值。
- 图谱事实不足以回答，且意图不是普通闲聊。

向量兜底结果不能挤掉已命中的核心实体。它只补充召回，不重排主角。

## Answer Format And Evidence Visibility

外部回答保持 `sales-agent` 当前格式：

```json
{
  "summary": "...",
  "sections": [
    {"title": "...", "content": "..."}
  ]
}
```

用户侧只展示简洁来源。后台和 trace 保存完整 `graph_evidence`：

- ontology intent
- center entities
- matched entities
- facts used
- evidence excerpts
- source documents
- retrieval strategy
- vector fallback used
- confidence
- Neo4j query timings

## Console Experience

`console` 负责入库链路可视化和审计摘要，不自研大型图谱探索器。

推荐在 Agent 作用域知识页实现：

```text
/agents/:agentId/knowledge
```

页面模块：

1. 导入入口：上传 Markdown 或选择测试样本文档，显示当前知识引擎为 Neo4j Ontology。
2. 入库进度：`uploaded`、`parsed`、`extracting_entities`、`extracting_facts`、`embedding_entities`、`writing_neo4j`、`conflict_checking`、`completed`、`failed`。
3. 导入结果摘要：文档数、新增实体数、合并实体数、新增 Fact 数、active/pending/rejected 统计、冲突数量、错误摘要。
4. 审计队列摘要：高风险冲突、中风险待复核、最近处理状态。
5. Neo4j 可视化入口：配置 `NEO4J_VISUAL_URL`，显示“在 Neo4j Workspace/Bloom 中打开”。

复杂图探索交给 Neo4j Bloom/Workspace。`console` 可展示最近实体、最近 Fact、最近冲突和最近 Evidence 来源表格。

## Configuration

新增配置：

```text
KNOWLEDGE_ENGINE=ontology_neo4j
NEO4J_URI=bolt://...
NEO4J_USER=...
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
NEO4J_VISUAL_URL=https://...
ONTOLOGY_VECTOR_FALLBACK=conservative
```

Agent 或 tenant 层保存知识引擎状态：

```text
knowledge_engine: ontology_neo4j
ontology_status: not_configured | ready | degraded | failed
```

## Error Handling And Degradation

- Neo4j 未配置：`/ready` 显示 degraded；需要知识库的请求返回友好错误或无知识回答。
- Neo4j 查询失败：记录 trace step `ontology_retrieval=failed`，回答提示知识库暂时不可用。
- LLM 抽取失败：导入任务进入 `failed` 或 `completed_with_errors`。
- embedding 失败：实体可入库，但标记 `embedding_status=failed`，向量兜底不可用。
- 冲突检测失败：不阻断入库，job 标记 `completed_with_warnings`。
- 回答生成失败：不调用旧 RAG 兜底，返回知识库生成失败提示，并记录完整错误。

第一版不回退旧 RAG。旧 RAG 代码和表可以暂时保留，但不作为运行路径。

## Testing And Acceptance Criteria

后端测试：

- Neo4j 配置解析和 ready 检查。
- Ontology 入库任务状态流转。
- 文档抽取结果能写成 Entity、Fact、Evidence 和 SourceDocument。
- Entity 去重和 canonical key 合并。
- Fact 冲突分级：低风险 active，中风险 pending_review，高风险创建 `CONFLICTS_WITH`。
- 保守向量兜底：精确命中时不触发，核心实体缺失时触发，兜底实体不挤掉核心实体。
- `ChatPipeline` 中原 `needs_retrieval` 路径调用 Ontology 引擎。
- Ontology 回答转换为 `summary/sections`。
- 风控仍在回答生成后执行。
- 日志和 trace 记录 `graph_evidence`。

前端测试：

- Agent 知识页能启动导入任务。
- 任务状态和统计展示正确。
- 错误、告警和冲突摘要展示正确。
- Neo4j Bloom/Workspace 外链按配置显示。
- 没配置时显示清晰空状态。

验收场景：

1. 测试租户上传 2 到 3 份样本文档。
2. 入库完成后，Neo4j 中可看到 Entity、Fact、Evidence 和 SourceDocument。
3. `console` 能看到导入进度和摘要。
4. 触发 `knowledge_qa` 的问题走 Neo4j Ontology，而不是旧 RAG。
5. 回答仍是 `summary/sections`。
6. 后台 trace 能看到图谱证据。
7. 高风险冲突不进入回答生成。

## Future Upgrade Path

第一版稳定后，再升级到 Agentic Knowledge Graph。未来 planner 可以在当前底座上选择工具：实体检索、事实扩展、向量兜底、冲突检查、历史会话、风险策略和知识缺口检测。因为第一版已把 Entity、Fact、Evidence 和 retrieval trace 做清楚，Agentic 层无需推翻知识主模型。
