# Ontology 入库支持 .docx / .pdf / .pptx（基于 docling）— 设计

> 日期：2026-06-25
> 状态：设计稿（待 review）
> 关联：`docs/superpowers/specs/2026-06-25-ontology-ingest-pipeline-design.md`

## 1. 目标

在现有 ontology 入库管线（.md / .txt 纯文本）基础上，增加对 **`.docx` / `.pdf` / `.pptx`** 三种办公文档格式的支持，使用 IBM [docling](https://github.com/DS4SD/docling) 统一解析为 Markdown 后送入现有的 LLM 抽取管道。

## 2. 范围

**做：**
- 后端 `_read_content(path)` 按扩展分流：纯文本走 `read_text`，docx/pdf/pptx 走 docling `export_to_markdown()`。
- 依赖 `docling>=2.0`。
- 白名单 `ALLOWED_EXTENSIONS` 扩展。
- 前端 `<Dragger accept>` 扩展。
- 单元测试（mock docling）+ 集成测试（最小有效二进制文件）。
- 错误处理：docling 转换失败 → 该文件 job 标 `failed`（已有机制）。

**不做（non-goals）：**
- 不做其他格式（HTML、XLSX、图片等）；范围仅 docx/pdf/pptx。
- 不调整 LLM prompt（docling 输出 MD，与现有 prompt 兼容）。
- 不做 docling 模型/参数调优（默认 pipeline 即可）。

## 3. 设计

### 3.1 依赖

`pyproject.toml` 加 `"docling>=2.0"`，`pip install` 到 `.venv`。

> 注意：首次安装时确认实际拉取依赖体积。如过重（含 torch 等），可降级到轻量方案（python-docx + pymupdf），但优先 docling（统一 API）。

### 3.2 核心改动：`_read_content`

在 `src/sales_agent/ontology/ingestion_service.py` 新增模块级函数，替换 `_ingest_one` 中原有的 `content = path.read_text(encoding="utf-8")`：

```python
def _read_content(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".docx", ".pdf", ".pptx"):
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(str(path))
            return result.document.export_to_markdown()
        except Exception:
            raise RuntimeError(f"docling 转换失败：{path.name}")
    else:
        return path.read_text(encoding="utf-8")
```

- `DocumentConverter` 每次新建，简单可靠。后续如需性能优化再改为模块级懒加载单例。
- docling 输出 Markdown，与现有 `ENTITY_EXTRACTION_PROMPT` / `FACT_EXTRACTION_PROMPT`（期望文档文本）兼容，无需改 prompt。
- 异常由上层 `_ingest_one` 的 try/except 捕获 → `stats.errors` + job `failed` → 前端红色行。

### 3.3 白名单 + 前端

- `src/sales_agent/api/routes/ontology.py`：`ALLOWED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf", ".pptx"}`
- `console/src/pages/Agents/AgentKnowledgePage.tsx`：`accept=".md,.txt,.docx,.pdf,.pptx"`

### 3.4 错误处理

docling 转换失败路径：`_read_content` 抛 `RuntimeError` → `_ingest_one` catch → `stats.errors.append({"file": str(path), "error": ...})` → `job.status = "failed"` → `progress_bus.publish` error → 前端红色行 + 错误信息 + 重试。

## 4. 测试

| 层 | 测试 | 内容 |
|---|---|---|
| 单元 | `tests/unit/ontology/test_read_content.py` | `.txt`/`.md` 原样返回；`.docx`/`.pdf`/`.pptx` mock docling 返回 MD |
| 集成 | 扩展 `test_ontology_api.py::test_ingest_multifile_returns_job_list` | 文件列表加 `.docx`（最小有效字节），断言 202 |
| 真实 live（gated） | 扩展 `test_ontology_neo4j_live.py` | `ONTOLOGY_LIVE_DOCLING=1`，最小 `.docx` → docling → ingest → retrieve |

## 5. 文件改动清单

- **Modify** `pyproject.toml` — 加 `docling>=2.0`
- **Modify** `src/sales_agent/ontology/ingestion_service.py` — 加 `_read_content`；`_ingest_one` 改为调 `_read_content`
- **Modify** `src/sales_agent/api/routes/ontology.py` — `ALLOWED_EXTENSIONS` 扩展
- **Modify** `console/src/pages/Agents/AgentKnowledgePage.tsx` — `accept` 属性扩展
- **Create** `tests/unit/ontology/test_read_content.py`
- **Modify** `tests/integration/test_ontology_api.py` — 扩展现有 multipart 测试加 docx
- **Modify** `tests/integration/test_ontology_neo4j_live.py` — gated docling live test
