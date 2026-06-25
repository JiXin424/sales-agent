# Neo4j Ontology Knowledge Engine Operations

本手册说明如何启用和验证 Neo4j 本体知识引擎（`KNOWLEDGE_ENGINE=ontology_neo4j`）。
该引擎用可审计的 Entity / Fact / Evidence 知识图谱替代传统 Markdown chunk RAG，
支持入库可视化、保守向量回退、高风险事实人工复核。

## Enable

在 `.env` 中配置：

```dotenv
KNOWLEDGE_ENGINE=ontology_neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=neo4j
NEO4J_VISUAL_URL=http://localhost:7474
ONTOLOGY_VECTOR_FALLBACK=conservative
```

| 变量 | 说明 |
|------|------|
| `KNOWLEDGE_ENGINE` | 知识引擎类型，`legacy_rag`（默认 chunk RAG）或 `ontology_neo4j` |
| `NEO4J_URI` | Neo4j Bolt 连接地址 |
| `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j 认证 |
| `NEO4J_DATABASE` | Neo4j 数据库名（默认 `neo4j`） |
| `NEO4J_VISUAL_URL` | Neo4j Browser 可视化地址（前端展示用） |
| `ONTOLOGY_VECTOR_FALLBACK` | 向量回退策略，`conservative`（保守）或 `off` |

## First Validation

1. 启动 Postgres（与原流程一致）。
2. 单独启动 Neo4j，或通过本地 compose 启动（如已配置）。
3. 启动应用。
4. 打开 `/agents/{agent_id}/knowledge`。
5. 查看 ontology 状态面板。
6. 用一份小的 Markdown 样本启动入库任务。
7. 提一个 `knowledge_qa` 问题验证端到端链路。

## Expected Behavior

- Neo4j 存储 Entity、Fact、Evidence、SourceDocument 节点。
- PostgreSQL 存储 ingestion jobs 和 chat 日志。
- Chat 响应保持 `summary` / `sections` 兼容格式。
- Trace 元数据中包含 `graph_evidence`。
- 处于 pending review 的高风险事实不会进入用户可见的回答。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agents/{id}/ontology/status` | 本体状态（实体/事实/证据计数） |
| POST | `/agents/{id}/ontology/ingest` | 启动入库任务 |
| GET | `/agents/{id}/ontology/jobs` | 入库任务列表与冲突统计 |

`/ready` 在 `ontology_neo4j` 模式下会附带 Neo4j 就绪信息（`legacy_rag` 模式行为不变）。
