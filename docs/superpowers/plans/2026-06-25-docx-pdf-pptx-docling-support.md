# Ontology 入库支持 .docx / .pdf / .pptx（docling）— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 ontology 入库管线中增加 `.docx` / `.pdf` / `.pptx` 支持，用 docling 统一解析为 Markdown 后送入 LLM 抽取管道。

**Architecture:** 新增 `_read_content(path)` 函数，按文件扩展分流——纯文本（.md/.txt）走 `read_text`，docx/pdf/pptx 走 docling `DocumentConverter` → `export_to_markdown()`。白名单 `ALLOWED_EXTENSIONS` 和前端 `accept` 各扩一行。

**Tech Stack:** Python `docling>=2.0`，其余同现有 ontology 管线。

## Global Constraints

- **不改 LLM prompt**（docling 输出 MD，与现有 prompt 兼容）。
- **不改 extractor、repository、SSE、前端页面逻辑**——仅内容读取层扩展。
- `DocumentConverter` 每次新建（简单可靠；性能优化后续迭代）。
- `.venv/bin/pytest` 跑测试。
- **全量回归必须绿**。

## File Structure

- **Modify** `pyproject.toml` — 加 `"docling>=2.0"`
- **Modify** `src/sales_agent/ontology/ingestion_service.py` — 新增 `_read_content`；`_ingest_one` 改为调 `_read_content`（替换 `path.read_text`）
- **Modify** `src/sales_agent/api/routes/ontology.py` — `ALLOWED_EXTENSIONS` 加 `.docx`、`.pdf`、`.pptx`
- **Modify** `console/src/pages/Agents/AgentKnowledgePage.tsx` — `accept` 属性加 `.docx,.pdf,.pptx`
- **Create** `tests/unit/ontology/test_read_content.py`
- **Modify** `tests/integration/test_ontology_api.py` — 扩展现有 multipart 测试
- **Modify** `tests/integration/test_ontology_neo4j_live.py` — gated docling live test

---

### Task 1: 加 docling 依赖 + `_read_content` 实现

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/sales_agent/ontology/ingestion_service.py`

- [ ] **Step 1: Install docling + 验证可导入**

```bash
.venv/bin/pip install "docling>=2.0"
.venv/bin/python -c "from docling.document_converter import DocumentConverter; print('import OK')"
```

- [ ] **Step 2: 写 `_read_content` + 改 `_ingest_one`**

In `src/sales_agent/ontology/ingestion_service.py`:

(a) Add `_read_content` as a module-level function (before the class definitions):

```python
def _read_content(path: Path) -> str:
    """Read file content, using docling for binary office formats."""
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

(b) In `_ingest_one`, replace the line:
```python
content = path.read_text(encoding="utf-8")
```
with:
```python
content = _read_content(path)
```

- [ ] **Step 3: Run existing ingestion test to confirm no regression**

```bash
.venv/bin/pytest tests/unit/ontology/test_ingestion_service.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/sales_agent/ontology/ingestion_service.py
git commit -m "feat: add docling-based content reader for docx/pdf/pptx"
```

---

### Task 2: 扩展白名单 + 前端 accept

**Files:**
- Modify: `src/sales_agent/api/routes/ontology.py`
- Modify: `console/src/pages/Agents/AgentKnowledgePage.tsx`

- [ ] **Step 1: 改 ALLOWED_EXTENSIONS**

In `src/sales_agent/api/routes/ontology.py`, change:
```python
ALLOWED_EXTENSIONS = {".md", ".txt"}
```
to:
```python
ALLOWED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf", ".pptx"}
```

- [ ] **Step 2: 改前端 accept**

In `console/src/pages/Agents/AgentKnowledgePage.tsx`, change:
```tsx
accept=".md,.txt"
```
to:
```tsx
accept=".md,.txt,.docx,.pdf,.pptx"
```

- [ ] **Step 3: Verify frontend build**

```bash
cd console && npm run build
```
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add src/sales_agent/api/routes/ontology.py console/src/pages/Agents/AgentKnowledgePage.tsx
git commit -m "feat: allow docx/pdf/pptx upload for ontology ingest"
```

---

### Task 3: 单元测试 `test_read_content`

**Files:**
- Create: `tests/unit/ontology/test_read_content.py`

- [ ] **Step 1: Write tests**

Create `tests/unit/ontology/test_read_content.py`:

```python
from pathlib import Path
from unittest.mock import patch, MagicMock

from sales_agent.ontology.ingestion_service import _read_content


def test_read_content_md_txt_uses_read_text(tmp_path):
    path = tmp_path / "test.md"
    path.write_text("# hello", encoding="utf-8")
    assert _read_content(path) == "# hello"

    path2 = tmp_path / "test.txt"
    path2.write_text("plain text", encoding="utf-8")
    assert _read_content(path2) == "plain text"


def test_read_content_docx_calls_docling(tmp_path):
    path = tmp_path / "test.docx"
    path.write_text("fake binary", encoding="utf-8")  # 非真实 docx，但会被 docling mock

    with patch("sales_agent.ontology.ingestion_service.DocumentConverter") as MockConv:
        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.document.export_to_markdown.return_value = "# converted docx"
        mock_instance.convert.return_value = mock_result
        MockConv.return_value = mock_instance

        result = _read_content(path)
        assert result == "# converted docx"
        mock_instance.convert.assert_called_once_with(str(path))


def test_read_content_docx_failure_raises(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_text("corrupt", encoding="utf-8")

    with patch("sales_agent.ontology.ingestion_service.DocumentConverter") as MockConv:
        mock_instance = MagicMock()
        mock_instance.convert.side_effect = RuntimeError("parse error")
        MockConv.return_value = mock_instance

        try:
            _read_content(path)
            assert False, "should have raised"
        except RuntimeError as e:
            assert "docling 转换失败" in str(e)
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/unit/ontology/test_read_content.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/ontology/test_read_content.py
git commit -m "test: unit tests for _read_content with docling mock"
```

---

### Task 4: 集成测试 + docling live test

**Files:**
- Modify: `tests/integration/test_ontology_api.py`
- Modify: `tests/integration/test_ontology_neo4j_live.py`

- [ ] **Step 1: 扩展 multipart 上传测试，加 .docx 文件**

In `test_ontology_api.py`, update `test_ingest_multifile_returns_job_list` to include one `.docx`:

```python
# 在 files 参数中增加一个 .docx（最小有效 docx 字节较复杂，用有效 .docx 如果用不了就用 ptt/pptx 同样处理）
# 现在用 .txt + .md 即可覆盖新白名单；加一个新测试针对 .docx：
@pytest.mark.asyncio
async def test_ingest_docx_accepted(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    from sales_agent.main import app
    from httpx import AsyncClient, ASGITransport

    # 真正的 .docx 字节在测试环境不可用；验证白名单接受扩展名
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/agents/{agent.id}/ontology/ingest",
            files=[("files", ("report.docx", io.BytesIO(b"PK\x03\x04" + b"\x00" * 100), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        )
    assert resp.status_code == 202
```

- [ ] **Step 2: Run integration tests**

```bash
.venv/bin/pytest tests/integration/test_ontology_api.py -v
```
Expected: ALL PASS.

- [ ] **Step 3: 加 gated docling live test（可选）**

In `test_ontology_neo4j_live.py`, add at end:

```python
import os as _os
LIVE_DOCLING = _os.getenv("ONTOLOGY_LIVE_DOCLING")

@pytest.mark.skipif(not LIVE_DOCLING, reason="set ONTOLOGY_LIVE_DOCLING=1 to test real docling pipeline")
@pytest.mark.asyncio
async def test_live_docling_ingest(tmp_path, db_session):
    """真实 docling 转换：创建最小 .docx → docling 解析 → ingest → retrieve。"""
    tenant = f"livedoc_{uuid.uuid4().hex[:8]}"
    settings = __import__('sales_agent.core.config', fromlist=['get_settings']).get_settings()
    from sales_agent.ontology.neo4j_client import Neo4jClient

    # 创建最小 .docx（实际测试需要有效 .docx 文件；此测试 gated，仅在有真实文件时手动跑）
    path = tmp_path / "sample.docx"
    # 放一个简单的文本文件代替 .docx（docling 对 .docx 需要真实格式）
    # 正式跑时用 python-docx 创建一个最小文档
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument()
        doc.add_paragraph("福多多提供员工福利产品。")
        doc.save(str(path))
    except ImportError:
        pytest.skip("python-docx not installed for creating minimal docx")

    neo_client = Neo4jClient(settings.neo4j)
    try:
        service = OntologyIngestionService(db_session, OntologyRepository(neo_client), FakeEmbedding(), FakeExtractor())
        job, stats = await service.ingest_paths(tenant_id=tenant, agent_id="agent1", paths=[path])
        assert job.status in ("completed", "completed_with_errors")
        assert stats.entities_created >= 1
    finally:
        async with neo_client.session() as s:
            await s.run("MATCH (n) WHERE n.tenant_id=$t DETACH DELETE n", t=tenant)
        await neo_client.close()
```

- [ ] **Step 4: Run gated test collection**

```bash
.venv/bin/pytest tests/integration/test_ontology_neo4j_live.py --collect-only -q
```
Expected: new test appears, default skipped.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_ontology_api.py tests/integration/test_ontology_neo4j_live.py
git commit -m "test: integration + gated docling live test for docx/pdf/pptx"
```

---

### Task 5: 最终验证 + 文档

- [ ] **Step 1: 全量回归**

```bash
.venv/bin/pytest tests/unit/ontology tests/integration/test_ontology_api.py tests/integration/test_ontology_chat_pipeline.py tests/integration/test_ontology_end_to_end_fake.py tests/unit/test_task_router.py tests/unit/test_risk_checker.py tests/unit/test_processing_notice.py tests/integration/coach/test_coach_pipeline_integration.py -q
```
Expected: ALL PASS.

- [ ] **Step 2: NEO4J_LIVE_TEST live**

```bash
NEO4J_LIVE_TEST=1 .venv/bin/pytest tests/integration/test_ontology_neo4j_live.py -q
```
Expected: 3 passed + some skipped.

- [ ] **Step 3: 前端 build**

```bash
cd console && npm run build
```
Expected: succeeds.

- [ ] **Step 4: 更新 docs + changelog**

`docs/ontology-neo4j-ops.md` 的「上传入库」节更新接受格式说明。
`changelog/2026-06-25.md` 追加 docx/pdf/pptx 支持条目。

- [ ] **Step 5: Commit**

```bash
git add docs/ontology-neo4j-ops.md changelog/2026-06-25.md
git commit -m "docs: docx/pdf/pptx upload support in ops doc and changelog"
```
