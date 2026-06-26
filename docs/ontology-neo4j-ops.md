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

1. 启动 Postgres 与 Neo4j：
   ```bash
   docker compose up -d postgres
   docker compose --profile ontology up -d neo4j   # 仅 ontology_neo4j 模式需要
   ```
   本地 compose 的 Neo4j 默认账密为 `neo4j / neo4jtest123`（见 `docker-compose.yml` 的 `NEO4J_AUTH`；生产请在 `secrets/*.env` 用 `NEO4J_PASSWORD` 覆盖）。浏览器可视化：<http://localhost:7474>，Bolt：`bolt://localhost:7687`。
2. 启动应用。
3. 打开 `/agents/{agent_id}/knowledge`。
4. 查看 ontology 状态面板。
5. 用一份小的 Markdown 样本启动入库任务。
6. 提一个 `knowledge_qa` 问题验证端到端链路。

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

## Live 集成测试（真实 Neo4j）

单元测试用 fake 仓库覆盖逻辑；要验证真实 Neo4j 的 Cypher、向量索引与 schema 启动初始化，启动上述 compose neo4j 后：

```bash
NEO4J_LIVE_TEST=1 .venv/bin/pytest tests/integration/test_ontology_neo4j_live.py -v
```

覆盖：schema 启动初始化（约束 + 向量索引）、真实 ingest → 检索 → 回答端到端、保守向量回退（图查不到时回退到向量并带回来源）。默认在 CI 跳过（无 Neo4j）。

## 上传入库（Web）

1. 打开 `/agents/{agent_id}/knowledge`。
2. 确认顶部状态为「ready」（绿色）。
3. 拖拽或点击选择文件（可多选）：`.md` / `.txt` / `.doc` / `.docx` / `.pdf` / `.pptx` / `.xlsx`。
   - `.md` / `.txt` 直接读取；`.docx` / `.pdf` / `.pptx` 由 `python-docx` / `pymupdf` / `python-pptx` 解析；旧版 `.doc` 经 LibreOffice 无头转 `.docx` 后解析；`.xlsx` 由 openpyxl 按 sheet 抽取。
4. 每个文件独立入库：6 阶段实时进度（上传/解析/抽实体/抽事实/写图谱/完成）。
5. 完成行显示入库统计（实体/事实/待复核/冲突），可点击「查看图谱 →」跳转 Neo4j Browser。
6. 失败行显示错误信息，可重试。

若状态为「not_configured」或「failed」，请先确认 `KNOWLEDGE_ENGINE=ontology_neo4j` 并启动 Neo4j。
