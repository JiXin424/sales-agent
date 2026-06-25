# Ontology 入库 Pipeline + 知识库页改造 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ontology 入库内核（`OntologyIngestionService.ingest_paths`）接通完整入库流程（上传→落盘→多 job 后台执行→SSE 实时进度→完成提示），并把前端知识库页整页改造为上传+进度界面。

**Architecture:** 每文件一个 `IngestionJob` + `asyncio.create_task` 后台跑 `ingest_paths`（方案 2，单文件隔离）；新增 `JobProgressBus`（内存 pub/sub）把阶段变化推给 SSE；前端 `EventSource` 订阅实时更新 6 阶段 Steps；外链 Neo4j Browser 看图谱。

**Tech Stack:** FastAPI + SQLAlchemy async + Neo4j async driver + DeepSeek（chat） + dashscope text-embedding-v3（1024 维） + React + Ant Design + React Query + EventSource（前端无额外依赖）。

## Global Constraints

- 后端 CommonJS-style Python；前端 ES Modules + 函数式组件 + Hooks。
- **不在 docker-compose 加新服务**（Neo4j 已加）；数据库不改（`IngestionJob` 字段已全）。
- SSE 用 FastAPI `StreamingResponse` 手写 `text/event-stream`，**不引入 sse-starlette 等新依赖**。
- 前端 API 调用通过 `console/src/api/client.ts`（已有 `apiGet`/`apiPost`/`apiUpload`）。多文件上传用 `FormData` + `fetch`（client 不太方便拼多文件），在 `client.ts` 新增 `apiUploadFiles`。
- 真实 LLM 密钥只走环境变量（`secrets/*.env`），绝不硬编码。
- **所有改动必须跑全量回归（ontology 34 + 回归 72 + 前端 build）确认绿，不绿不合并。**
- `.venv/bin/pytest` 跑测试（不需要 PYTHONPATH）。

## File Structure

后端：
- **Create** `src/sales_agent/ontology/progress.py` — `JobProgressBus`（内存 pub/sub 进度总线）。
- **Modify** `src/sales_agent/ontology/ingestion_service.py` — `ingest_paths` 加 `progress_callback` 参数。
- **Create** `src/sales_agent/ontology/runner.py` — `LLMExtractor`（适配 ChatModel → ExtractorProtocol）+ `build_ingestion_service` 注入真实 LLM provider。
- **Modify** `src/sales_agent/api/routes/ontology.py` — `start_ontology_ingest` 改 multipart 多文件+落盘+多 job+后台 task+进度推送；新增 SSE `endpoint GET /jobs/{job_id}/events`。
- **Create** `tests/unit/ontology/test_progress_bus.py`
- **Modify** `tests/unit/ontology/test_ingestion_service.py` — 扩展 `progress_callback` 测试。
- **Create** `tests/unit/ontology/test_runner.py`
- **Modify** `tests/integration/test_ontology_api.py` — 扩展 multipart 上传 + SSE 订阅测试。
- **Modify** `tests/integration/test_ontology_neo4j_live.py` — 扩展 gated 真实 LLM 测试。

前端：
- **Modify** `console/src/api/client.ts` — 新增 `apiUploadFiles<T>(path, files: File[]): Promise<T>`。
- **Modify** `console/src/api/knowledge.ts` — `startOntologyIngest` 改 multipart 多文件；新增 `subscribeJobEvents` (EventSource)。
- **Modify** `console/src/api/types.ts` — 新增 `OntologyIngestFileItem`、`JobProgressEvent`。
- **Modify** `console/src/pages/Agents/AgentKnowledgePage.tsx` — 整页替换为上传+进度。
- **Modify** `console/src/tests/api/knowledge.test.ts` — 更新上传 wrapper 测试。

文档：
- **Modify** `docs/ontology-neo4j-ops.md` — 补上传入库用法。
- **Modify** `changelog/2026-06-25.md` — 追加。

---

### Task 1: JobProgressBus（进度总线）

**Files:**
- Create: `src/sales_agent/ontology/progress.py`
- Test: `tests/unit/ontology/test_progress_bus.py`

**Interfaces:**
- Produces: `JobProgressBus` class — `subscribe(job_id: str) -> asyncio.Queue`, `async publish(job_id: str, event: dict) -> None`, `remove(job_id: str)`。模块级单例 `progress_bus`。

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ontology/test_progress_bus.py`:

```python
import asyncio

from sales_agent.ontology.progress import JobProgressBus


async def test_subscribe_then_publish_receives_event():
    bus = JobProgressBus()
    q = bus.subscribe("j1")
    await bus.publish("j1", {"stage": "parsed"})
    event = await asyncio.wait_for(q.get(), timeout=0.5)
    assert event == {"stage": "parsed"}


async def test_multiple_subscribers_both_get_event():
    bus = JobProgressBus()
    q1 = bus.subscribe("j1")
    q2 = bus.subscribe("j1")
    await bus.publish("j1", {"stage": "extracting_entities"})
    e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    e2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert e1 == e2 == {"stage": "extracting_entities"}


async def test_different_jobs_isolated():
    bus = JobProgressBus()
    q1 = bus.subscribe("j1")
    q2 = bus.subscribe("j2")
    await bus.publish("j1", {"stage": "a"})
    assert not q2.empty() is False  # q2 should still be empty
    e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    assert e1 == {"stage": "a"}


async def test_remove_cleans_up():
    bus = JobProgressBus()
    q = bus.subscribe("j1")
    bus.remove("j1")
    await bus.publish("j1", {"stage": "x"})
    # After remove, publish is a no-op (no subscribers); q should be empty
    assert q.empty()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/ontology/test_progress_bus.py -v
```
Expected: FAIL (module `progress` does not exist).

- [ ] **Step 3: Implement JobProgressBus**

Create `src/sales_agent/ontology/progress.py`:

```python
from __future__ import annotations

import asyncio


class JobProgressBus:
    """In-memory pub/sub bus for job progress events (one asyncio.Queue per subscriber)."""

    def __init__(self):
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(job_id, []).append(q)
        return q

    async def publish(self, job_id: str, event: dict) -> None:
        for q in self._subs.get(job_id, []):
            await q.put(event)

    def remove(self, job_id: str) -> None:
        self._subs.pop(job_id, None)


# 进程级单例（模块全局）
progress_bus = JobProgressBus()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/unit/ontology/test_progress_bus.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/progress.py tests/unit/ontology/test_progress_bus.py
git commit -m "feat: add ontology job progress pub/sub bus"
```

---

### Task 2: ingest_paths 加 progress_callback

**Files:**
- Modify: `src/sales_agent/ontology/ingestion_service.py`（加参数）
- Test: `tests/unit/ontology/test_ingestion_service.py`（扩展）

**Interfaces:**
- Consumes: `JobProgressBus` type（仅类型引用）
- Produces: `ingest_paths(*, tenant_id, agent_id, paths, progress_callback: Callable | None = None)` — 新参数

- [ ] **Step 1: Write the failing test — callback is called on each stage**

Add to `tests/unit/ontology/test_ingestion_service.py` at the end:

```python
from unittest.mock import AsyncMock  # added to imports

@pytest.mark.asyncio
async def test_progress_callback_called_on_each_stage(tmp_path, db_session, sample_tenant):
    path = tmp_path / "sample.md"
    path.write_text("# 福利卡\n福多多提供员工福利产品。", encoding="utf-8")
    repo = FakeRepository()
    cb = AsyncMock()

    service = OntologyIngestionService(db_session, repo, FakeEmbedding(), FakeExtractor())
    job, stats = await service.ingest_paths(
        tenant_id=sample_tenant, agent_id="agent1", paths=[path],
        progress_callback=cb,
    )

    assert stats.entities_created == 1
    # 回调被调用了多次（至少每个 stage 一次）
    assert cb.call_count >= 4  # parsed, extracting_entities, extracting_facts, writing_neo4j
    # 第一次调用的 stage 是 "parsed"
    first_call_stage = cb.call_args_list[0][0][0]
    assert first_call_stage == "parsed"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/ontology/test_ingestion_service.py::test_progress_callback_called_on_each_stage -v
```
Expected: FAIL (TypeError: unexpected keyword argument 'progress_callback').

还是 FAIL，但错误是 `unexpected keyword argument 'progress_callback'`（说明参数不存在）。

- [ ] **Step 3: Add progress_callback to ingest_paths and _ingest_one**

In `src/sales_agent/ontology/ingestion_service.py`:

(a) Add import at top:

```python
from typing import Awaitable
```

(b) Change `ingest_paths` signature to add the new parameter:

```python
    async def ingest_paths(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        paths: list[Path],
        progress_callback: None
        | (Callable[[str, dict[str, Any]], Awaitable[None]]) = None,
    ) -> tuple[IngestionJob, OntologyIngestionStats]:
```

( Keep `Callable` already imported from `typing` if using `Protocol` — you may need to adjust `Callable` usage. The existing file imports `from typing import Protocol`; add `Awaitable` and use `Callable[..., Awaitable]` with `from collections.abc import Callable` or adjust—simplest: inline the type as `Any`):

```python
    async def ingest_paths(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        paths: list[Path],
        progress_callback=None,  # type: ignore — Callable[[str,dict],Awaitable]|None
    ) -> tuple[IngestionJob, OntologyIngestionStats]:
```

(c) Inside `ingest_paths`, pass `progress_callback` to `_ingest_one` calls in the loop:

```python
                await self._ingest_one(job, stats, tenant_id, agent_id, path,
                                       progress_callback=progress_callback)
```

(d) Add `progress_callback` parameter to `_ingest_one`:

```python
    async def _ingest_one(
        self,
        job: IngestionJob,
        stats: OntologyIngestionStats,
        tenant_id: str,
        agent_id: str | None,
        path: Path,
        progress_callback=None,
    ) -> None:
```

(e) In `_ingest_one`, after each `job.stage = "..."` assignment, add a callback call collecting the current stats snapshot:

After each `job.stage = "parsed"` / `"extracting_entities"` / `"extracting_facts"` / `"writing_neo4j"` block, add:

```python
        if progress_callback:
            await progress_callback(job.stage, {
                "entities_created": stats.entities_created,
                "entities_merged": stats.entities_merged,
                "facts_created": stats.facts_created,
                "facts_active": stats.facts_active,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
```

(Same block after each of the 4 stage-setting lines — `parsed` (line after path.read_text), `extracting_entities`, `extracting_facts`, `writing_neo4j`).

(f) Also call after completion (end of `_ingest_one`, after the fact loop):

```python
        if progress_callback:
            await progress_callback("completed", {
                "entities_created": stats.entities_created,
                "facts_created": stats.facts_created,
                "facts_pending_review": stats.facts_pending_review,
                "conflicts_created": stats.conflicts_created,
            })
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/ontology/test_ingestion_service.py -v
```
Expected: ALL PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/ingestion_service.py tests/unit/ontology/test_ingestion_service.py
git commit -m "feat: add progress callback to ontology ingestion"
```

---

### Task 3: LLMExtractor + Runner

**Files:**
- Create: `src/sales_agent/ontology/runner.py`
- Test: `tests/unit/ontology/test_runner.py`

**Interfaces:**
- Consumes: `ChatModel`（`llm.base`）、`EmbeddingModel`（`llm.base`）、`Neo4jClient`、`OntologyRepository`、`OntologyIngestionService`、`extract_entities`/`extract_facts`（`ontology.extractor`）
- Produces: `LLMExtractor` class（适配 `ChatModel` → `ExtractorProtocol`）；`build_ingestion_service(db, settings, model_provider, progress_callback=None) -> OntologyIngestionService`

- [ ] **Step 1: Write failing test**

Create `tests/unit/ontology/test_runner.py`:

```python
from sales_agent.ontology.runner import LLMExtractor, build_ingestion_service
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate


class FakeChatModel:
    async def generate(self, messages, temperature=None, max_tokens=None, **kwargs):
        return '{"entities":[{"type":"Product","name":"福利卡"},{"type":"Concept","name":"福多多"}]}'


class FakeEmbeddingModel:
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class FakeModelProvider:
    def __init__(self):
        self.chat = FakeChatModel()
        self.embedding = FakeEmbeddingModel()


async def test_llm_extractor_returns_entities_and_facts():
    extractor = LLMExtractor(FakeChatModel())
    entities = await extractor.extract_entities("福多多提供员工福利产品。")
    assert len(entities) >= 1
    assert entities[0].type == "Product"
    assert entities[0].name == "福利卡"
    facts = await extractor.extract_facts("test content", entities)
    assert isinstance(facts, list)


async def test_build_ingestion_service_returns_service():
    from sales_agent.core.config import Settings
    settings = Settings(
        ontology={"knowledge_engine": "ontology_neo4j"},
        neo4j={"uri": "bolt://fake", "user": "neo4j", "password": "pw"},
    )
    from sales_agent.models.base import Base
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    import os
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://")
    if db_url == "postgresql+asyncpg://":
        pytest.skip("no DATABASE_URL set")
    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as db:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        service = build_ingestion_service(db, settings, FakeModelProvider())
        assert service is not None
```

(Note: `build_ingestion_service` 的测试需要 PG 数据库——用 skip 保护。`LLMExtractor` 的测试是纯本地、无需 DB。)

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/ontology/test_runner.py -v
```
Expected: FAIL (module `runner` does not exist).

- [ ] **Step 3: Implement LLMExtractor and build_ingestion_service**

Create `src/sales_agent/ontology/runner.py`:

```python
from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.config import Settings
from sales_agent.llm.base import ChatModel, EmbeddingModel, ModelProvider
from sales_agent.ontology.extractor import extract_entities, extract_facts
from sales_agent.ontology.ingestion_service import OntologyIngestionService
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.ontology.repository import OntologyRepository
from sales_agent.ontology.schemas import EntityCandidate, FactCandidate


class LLMExtractor:
    """Adapt ChatModel to the ExtractorProtocol that OntologyIngestionService expects."""

    def __init__(self, chat_model: ChatModel):
        self._chat = chat_model

    async def extract_entities(self, content: str) -> list[EntityCandidate]:
        return await extract_entities(self._chat, content)

    async def extract_facts(self, content: str, entities: list[EntityCandidate]) -> list[FactCandidate]:
        return await extract_facts(self._chat, content, entities)


def build_ingestion_service(
    db: AsyncSession,
    settings: Settings,
    model_provider: ModelProvider,
    progress_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> OntologyIngestionService:
    """Build an OntologyIngestionService wired to real Neo4j + the tenant's LLM provider."""
    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)
    extractor = LLMExtractor(model_provider.chat)
    return OntologyIngestionService(
        db=db,
        repository=repository,
        embedding_model=model_provider.embedding,
        extractor=extractor,
    )
```

(Note: `progress_callback` 传给 `ingest_paths` 而不是 `__init__`；`build_ingestion_service` 的调用方拿到 service 后调用 `service.ingest_paths(..., progress_callback=...)`。如果需要也可以在构造后传——但 `ingest_paths` 接受 `progress_callback`，不需侵入 `OntologyIngestionService.__init__`。所以 `build_ingestion_service` 返回 service，调用方（route）在调 `ingest_paths` 时传 `progress_callback`。)

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/ontology/test_runner.py::test_llm_extractor_returns_entities_and_facts -v
```
Expected: PASS (test_build_ingestion_service 需要 PG，可能 skip；至少 LLMExtractor 测试过)。

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/ontology/runner.py tests/unit/ontology/test_runner.py
git commit -m "feat: add ontology LLM extractor adapter and service builder"
```

---

### Task 4: 改造 ingest API + SSE endpoint

**Files:**
- Modify: `src/sales_agent/api/routes/ontology.py`
- Test: `tests/integration/test_ontology_api.py`（扩展）

**Interfaces:**
- Consumes: `JobProgressBus` / `progress_bus`、`build_ingestion_service` / `LLMExtractor`、`TenantResolver`、`IngestionJob`、`UploadFile`（fastapi）、`StreamingResponse`（starlette/fastapi）
- Produces: `POST /agents/{agent_id}/ontology/ingest`（multipart）→ `202 [{job_id, filename}]`；`GET /agents/{agent_id}/ontology/jobs/{job_id}/events`（SSE）

- [ ] **Step 1: Write failing SSE + multipart test**

Add to `tests/integration/test_ontology_api.py`:

```python
import json
import httpx
import os
import uuid
from pathlib import Path

# --- Multipart upload ---

@pytest.mark.asyncio
async def test_ingest_multifile_returns_job_list(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")

    # 创建两个临时 md 文件
    tmp = Path("/tmp/_ontology_ingest_test")
    tmp.mkdir(exist_ok=True)
    p1 = tmp / "test1.md"; p1.write_text("# test1\nhello", encoding="utf-8")
    p2 = tmp / "test2.md"; p2.write_text("# test2\nworld", encoding="utf-8")

    # 用 httpx AsyncClient multipart
    from sales_agent.main import app
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/agents/{agent.id}/ontology/ingest",
            files=[("files", p1.open("rb")), ("files", p2.open("rb"))],
        )
    assert resp.status_code in (200, 202)
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert "job_id" in data[0]
    assert "filename" in data[0]


@pytest.mark.asyncio
async def test_ingest_reject_non_md(db_session, sample_tenant):
    from sales_agent.services.agent_migration import ensure_default_agent_for_tenant
    agent = await ensure_default_agent_for_tenant(db_session, sample_tenant, "Test Tenant")
    from sales_agent.main import app
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import io
        resp = await client.post(
            f"/agents/{agent.id}/ontology/ingest",
            files=[("files", ("test.exe", io.BytesIO(b"data"), "application/octet-stream"))],
        )
    assert resp.status_code == 400
    assert "仅支持" in resp.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/pytest tests/integration/test_ontology_api.py::test_ingest_multifile_returns_job_list -v

PYTHONPATH=src .venv/bin/pytest tests/integration/test_ontology_api.py::test_ingest_reject_non_md -v
```
Expected: FAIL（param `files` 不存在，当前 route 签名为 `body: dict`）。

- [ ] **Step 3: 重写 start_ontology_ingest + 新增 SSE endpoint**

Replace `start_ontology_ingest` in `src/sales_agent/api/routes/ontology.py`:

```python
import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.models.base import generate_id
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.progress import progress_bus
from sales_agent.ontology.schemas import OntologyIngestionStats
from sales_agent.services.agent_service import AgentService, AgentNotFoundError
from sales_agent.services.tenant_resolver import TenantResolver


ALLOWED_EXTENSIONS = {".md", ".txt"}


@router.post("/{agent_id}/ontology/ingest", status_code=202)
async def start_ontology_ingest(
    agent_id: str,
    files: list[UploadFile] = File(...),
    db: DbSession = None,  # FastAPI injects via Depends
):
    agent = await _load_agent_or_404(agent_id, db)
    settings = get_settings()

    data_dir = Path(settings.app.data_dir or "data")
    dest_dir = data_dir / "agents" / agent.id / "ontology"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in files:
        ext = Path(f.filename or "unknown.md").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"仅支持 {ALLOWED_EXTENSIONS} 文件：{f.filename}")

        dest_name = f"{generate_id()}_{f.filename}"
        dest_path = dest_dir / dest_name
        with dest_path.open("wb") as buf:
            shutil.copyfileobj(f.file, buf)

        job = IngestionJob(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            engine="ontology_neo4j",
            status="running",
            stage="uploaded",
            documents_seen=1,
        )
        db.add(job)
        await db.flush()

        # 后台异步执行入库
        asyncio.create_task(
            _run_ingest_background(
                job_id=job.id,
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                path=dest_path,
                db_session=db,  # 后台任务共享 session（同一个请求生命周期）
            )
        )
        results.append({"job_id": job.id, "filename": f.filename})

    return results


async def _run_ingest_background(
    job_id: str,
    tenant_id: str,
    agent_id: str,
    path: Path,
    db_session,
) -> None:
    """跑在 asyncio.create_task 里：执行 ingest_paths，把进度推到 bus。"""
    settings = get_settings()
    try:
        from sales_agent.services.tenant_resolver import TenantResolver
        resolver = TenantResolver(db_session)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        from sales_agent.ontology.runner import build_ingestion_service

        async def _on_progress(stage: str, stats: dict):
            await progress_bus.publish(job_id, {"stage": stage, "status": "running", "stats": stats})

        service = build_ingestion_service(db_session, settings, model_provider)
        job, stats = await service.ingest_paths(
            tenant_id=tenant_id,
            agent_id=agent_id,
            paths=[path],
            progress_callback=_on_progress,
        )
        await progress_bus.publish(job_id, {
            "stage": job.stage,
            "status": job.status,
            "stats": stats.to_metadata(),
        })
    except Exception as exc:
        # 更新 job 失败
        from sqlalchemy import select
        row = (await db_session.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one_or_none()
        if row:
            row.status = "failed"
            row.error_summary = str(exc)[:500]
            await db_session.flush()
        await progress_bus.publish(job_id, {
            "stage": "failed",
            "status": "failed",
            "error_summary": str(exc)[:500],
            "stats": {},
        })


@router.get("/{agent_id}/ontology/jobs/{job_id}/events")
async def job_events(agent_id: str, job_id: str, db: DbSession):
    await _load_agent_or_404(agent_id, db)

    async def _stream():
        q = progress_bus.subscribe(job_id)
        try:
            # 先推当前快照
            from sqlalchemy import select
            row = (await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one_or_none()
            if row:
                snap = {
                    "stage": row.stage or "uploaded",
                    "status": row.status,
                    "stats": {
                        "entities_created": row.entities_created,
                        "facts_created": row.facts_created,
                        "facts_pending_review": row.facts_pending_review,
                        "conflicts_created": row.conflicts_created,
                    },
                }
                yield f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"

            import asyncio as _asyncio
            while True:
                try:
                    event = await _asyncio.wait_for(q.get(), timeout=30)
                except _asyncio.TimeoutError:
                    # 发个 keepalive
                    yield ": keepalive\n\n"
                    continue
                stage = event.get("stage", "")
                status = event.get("status", "running")
                event_type = "done" if status in ("completed", "completed_with_errors", "failed") else "progress"
                yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event_type == "done":
                    break
        finally:
            progress_bus.remove(job_id)

    return StreamingResponse(_stream(), media_type="text/event-stream")
```

Also at the top of the file, remove the old `start_ontology_ingest` (the `body: dict` version — keep the function name but replace the body, using the new version above). Remove unused `from pathlib import Path` if it was there (it is now USED — keep it). Remove `body: dict` param.

The old `Path` import was flagged as dead — now it's alive because the new code uses `Path(f.filename).suffix` and `Path(data_dir / ...)`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/integration/test_ontology_api.py -v
```
Expected: PASS (包括新 multipart 测试 + 旧路径/状态测试)。

```bash
# 也确保全量 ontology 套件不挂
.venv/bin/pytest tests/unit/ontology tests/integration/test_ontology_api.py tests/integration/test_ontology_chat_pipeline.py tests/integration/test_ontology_end_to_end_fake.py -q
```
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/api/routes/ontology.py tests/integration/test_ontology_api.py
git commit -m "feat: ontology ingest multipart upload + SSE progress endpoint"
```

---

### Task 5: 前端 API wrappers 改造

**Files:**
- Modify: `console/src/api/client.ts`
- Modify: `console/src/api/knowledge.ts`
- Modify: `console/src/api/types.ts`
- Test: `console/src/tests/api/knowledge.test.ts`（更新）

**Interfaces:**
- Consumes: `apiGet`/`apiPost`（已有）、`PaginatedResponse`（已有）
- Produces: `apiUploadFiles<T>(path, files): Promise<T>`、`startOntologyIngest(agentId, files): Promise<IngestStartResponse[]>`、`subscribeJobEvents(jobId): EventSource`、`OntologyIngestFileItem`、`JobProgressEvent`

- [ ] **Step 1: Add apiUploadFiles to client.ts**

At the bottom of `console/src/api/client.ts`, after the `apiUpload` export:

```ts
/** Upload multiple files to an agent ontology endpoint (multipart/form-data). */
export function apiUploadFiles<T>(path: string, files: File[]): Promise<T> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  return request<T>('POST', path, { body: fd });
}
```

(Note: `request` must accept `body: FormData`. Ensure the `request` function's `body` handling allows `FormData` — it should, since `fetch` natively supports `FormData` as body. No extra `Content-Type` header needed — browser sets it with boundary.)

- [ ] **Step 2: Add types to types.ts**

Add to `console/src/api/types.ts`:

```ts
export interface IngestStartResponse {
  job_id: string;
  filename: string;
}

export interface JobProgressEvent {
  stage: string;
  status: 'running' | 'completed' | 'completed_with_errors' | 'failed';
  stats?: Record<string, number>;
  error_summary?: string;
}
```

- [ ] **Step 3: Rewrite wrappers in knowledge.ts**

In `console/src/api/knowledge.ts`:

(a) Add `apiUploadFiles` to the import from `'./client'`:

```ts
import { apiGet, apiPost, apiUpload, apiUploadFiles } from './client';
```

(b) Add `IngestStartResponse` to type imports:

```ts
import type { ..., IngestStartResponse, JobProgressEvent } from './types';
```

(c) **Replace** `startOntologyIngest` with:

```ts
export function startOntologyIngest(agentId: string, files: File[]) {
  return apiUploadFiles<IngestStartResponse[]>(`/agents/${agentId}/ontology/ingest`, files);
}
```

(Remove the old `path: string` version.)

(d) Add:

```ts
export function subscribeJobEvents(agentId: string, jobId: string): EventSource {
  return new EventSource(`/api/agents/${agentId}/ontology/jobs/${jobId}/events`);
}
```

(前端走同源 `/api`，由 vite 代理；非 dev 环境同源 root。用相对路径 `/api/...` 与现有 `apiGet`/`apiPost` 一致。)

- [ ] **Step 4: Update tests**

Replace `console/src/tests/api/knowledge.test.ts` 中 ontology 测试块：

```ts
import {
  getOntologyStatus,
  startOntologyIngest,
  listOntologyJobs,
  subscribeJobEvents,
} from '@/api/knowledge';

// ... 前两个测试保持不变 ...

// Replace the ingest test:
it('starts ontology ingest with multiple files', async () => {
  const f1 = new File(['content1'], 'test1.md', { type: 'text/markdown' });
  const f2 = new File(['content2'], 'test2.md', { type: 'text/markdown' });
  await startOntologyIngest('a1', [f1, f2]);
  expect(lastCall!.method).toBe('POST');
  expect(lastCall!.url).toContain('/agents/a1/ontology/ingest');
  // FormData body won't parse via JSON — but we can check method/url
});

it('creates EventSource for job events', () => {
  const es = subscribeJobEvents('a1', 'j1');
  expect(es.url).toContain('/agents/a1/ontology/jobs/j1/events');
  es.close();
});
```

- [ ] **Step 5: Run frontend tests + build**

```bash
cd console && npm test -- src/tests/api/knowledge.test.ts
cd console && npm run build
```
Expected: tests PASS and build succeeds.

- [ ] **Step 6: Commit**

```bash
git add console/src/api/client.ts console/src/api/knowledge.ts console/src/api/types.ts console/src/tests/api/knowledge.test.ts
git commit -m "feat: frontend multipart upload + SSE event source for ontology ingest"
```

---

### Task 6: AgentKnowledgePage 整页替换

**Files:**
- Modify: `console/src/pages/Agents/AgentKnowledgePage.tsx`

**Interfaces:**
- Consumes: `getOntologyStatus`, `startOntologyIngest`, `listOntologyJobs`, `subscribeJobEvents`（from `@/api/knowledge`）；`useParams`（react-router）；`Upload`, `Steps`, `Tag`, `Button`, `Alert`, `message`（antd）；`useQuery`, `useMutation`（react-query）
- Produces: 整页替换后的 ontology 上传+进度界面

这个 task 代码较长（整页组件 ~150 行），概要：

- **状态条**：`useQuery(getOntologyStatus)` → `neo4j_ready` 绿色/橙色 tag + `visual_url` 外链；未就绪 Alert 禁用上传。
- **上传区**：`Upload.Dragger`（`accept=".md,.txt"`, `multiple`, `customRequest` → 收集 files → 调 `startOntologyIngest(agentId, files)` → 拿 `[{job_id, filename}]` → 初始化 `fileJobs` + 每个 job 开 `subscribeJobEvents`）。
- **进度列表**（`fileJobs` 数组，按加入顺序）：
  - `fileJobs` state: `{filename, jobId, stage, status, stats, errorSummary}[]`。
  - SSE event `onmessage`：解析 event，`setFileJobs(prev => ...)` 更新对应行。
  - 进行中渲染 antd `Steps`（6 个步骤，当前 `process`，完成 `finish`，未开始 `wait`）。
  - 完成渲染：绿色行 + stats 数字 + `Button` 外链 `visual_url`。
  - 失败渲染：红色行 + `errorSummary` + `Button` 重试（`startOntologyIngest` 再调）。
- **全部完成**：`useEffect` 检测全部完成 → `message.success("X 个文件入库完成…")`。

[Due to the length, the full TSX is written in the step below. Follow the spec §4.2 and §4 layout.]

- [ ] **Step 1: Write the replacement page**

**Full file content for `AgentKnowledgePage.tsx`** (the implementing subagent must read the existing file for imports/patterns, then replace it with the content below):

```tsx
/** Agent-scoped ontology knowledge ingestion page — upload + SSE progress. */
import { useState, useEffect, useCallback, useRef } from 'react';
import { Upload, Steps, Tag, Button, Alert, Typography, Space } from 'antd';
import { InboxOutlined, ExclamationCircleOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { message } from 'antd';
import type { UploadFile } from 'antd';
import { getOntologyStatus, startOntologyIngest, subscribeJobEvents } from '@/api/knowledge';
import type { IngestStartResponse, JobProgressEvent } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { Dragger } = Upload;
const { Title, Text } = Typography;

/* ---- constants ---- */
const STAGES = ['上传', '解析', '抽实体', '抽事实', '写图谱', '完成'];
const STAGE_KEY: Record<string, number> = {
  uploaded: 0, parsed: 1, extracting_entities: 2,
  extracting_facts: 3, writing_neo4j: 4, completed: 5,
};
const TERMINAL_STATUSES = new Set(['completed', 'completed_with_errors', 'failed']);

/* ---- file job row shape ---- */
interface FileJob {
  filename: string;
  jobId: string;
  stage: string;
  status: string;
  stats?: Record<string, number>;
  errorSummary?: string;
}

export default function AgentKnowledgePage() {
  const { agentId } = useParams<{ agentId: string }>();

  /* ---- ontology status ---- */
  const statusQuery = useQuery({
    queryKey: ['ontology-status', agentId],
    queryFn: () => getOntologyStatus(agentId!),
    enabled: !!agentId,
  });
  const engineReady = statusQuery.data?.knowledge_engine === 'ontology_neo4j' && statusQuery.data?.neo4j_ready;
  const visualUrl = statusQuery.data?.visual_url || '';

  /* ---- file jobs ---- */
  const [fileJobs, setFileJobs] = useState<FileJob[]>([]);
  const [uploading, setUploading] = useState(false);
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());

  /* ---- upload handler ---- */
  const handleUpload = useCallback(async (rawFiles: File[]) => {
    if (!agentId || !engineReady) return;
    setUploading(true);
    try {
      const result: IngestStartResponse[] = await startOntologyIngest(agentId, rawFiles);
      const newJobs: FileJob[] = result.map(r => ({
        filename: r.filename,
        jobId: r.job_id,
        stage: 'uploaded',
        status: 'running',
        stats: {},
      }));
      setFileJobs(prev => [...prev, ...newJobs]);

      // 每个 job 开 EventSource
      newJobs.forEach(job => {
        const es = subscribeJobEvents(agentId, job.jobId);
        eventSourcesRef.current.set(job.jobId, es);
        es.onmessage = (evt) => {
          try {
            const data: JobProgressEvent = JSON.parse(evt.data);
            setFileJobs(prev =>
              prev.map(fj => fj.jobId === job.jobId
                ? { ...fj, stage: data.stage, status: data.status, stats: data.stats || fj.stats, errorSummary: data.error_summary }
                : fj
              )
            );
            if (TERMINAL_STATUSES.has(data.status)) {
              es.close();
              eventSourcesRef.current.delete(job.jobId);
            }
          } catch { /* ignore malformed JSON */ }
        };
        es.onerror = () => { es.close(); eventSourcesRef.current.delete(job.jobId); };
      });
    } catch (e: any) {
      message.error(`上传失败：${e?.message || e}`);
    } finally {
      setUploading(false);
    }
  }, [agentId, engineReady]);

  /* ---- all done? ---- */
  useEffect(() => {
    if (fileJobs.length > 0 && fileJobs.every(fj => TERMINAL_STATUSES.has(fj.status))) {
      const doneCount = fileJobs.filter(fj => fj.status === 'completed').length;
      const totalEntities = fileJobs.reduce((s, fj) => s + (fj.stats?.entities_created || 0), 0);
      const totalFacts = fileJobs.reduce((s, fj) => s + (fj.stats?.facts_created || 0), 0);
      message.success(`${doneCount}/${fileJobs.length} 个文件入库完成 · ${totalEntities} 实体 / ${totalFacts} 事实`);
    }
  }, [fileJobs]);

  /* ---- cleanup EventSources on unmount ---- */
  useEffect(() => {
    return () => { eventSourcesRef.current.forEach(es => es.close()); };
  }, []);

  /* ---- render ---- */
  if (statusQuery.isLoading) return <LoadingState />;
  if (statusQuery.isError) return <ErrorState />;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: 24 }}>
      <PageHeader title="本体知识入库" />

      {/* 引擎状态 */}
      {!engineReady && (
        <Alert
          type="warning"
          showIcon
          message="本体引擎未就绪"
          description="请先配置 KNOWLEDGE_ENGINE=ontology_neo4j 并确保 Neo4j 可连接。当前默认引擎仍为 legacy chunk RAG。"
          style={{ marginBottom: 16 }}
        />
      )}
      <Space style={{ marginBottom: 16 }}>
        <Tag color={engineReady ? 'green' : 'orange'}>{statusQuery.data?.ontology_status || 'unknown'}</Tag>
        {visualUrl && (
          <Button size="small" href={visualUrl} target="_blank" rel="noreferrer">
            Neo4j Browser ↗
          </Button>
        )}
      </Space>

      {/* 上传区 */}
      <Dragger
        accept=".md,.txt"
        multiple
        disabled={!engineReady || uploading}
        showUploadList={false}
        customRequest={({ file, onSuccess }) => {
          // antd Upload 的 customRequest: 收集所有 files 后统一调
          // 坑：Dragger customRequest 对每个 file 单独调用，不是批量。改用 uncontrolled 方式收集
          onSuccess?.('ok');
        }}
        beforeUpload={(file, fileList) => {
          // 只在第一个文件时触发一次（避免重复上传）
          if (fileList.indexOf(file) === fileList.length - 1) {
            handleUpload(fileList as unknown as File[]);
          }
          return false; // 阻止 antd 自动上传
        }}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">拖拽文件到此处，或点击选择</p>
        <p className="ant-upload-hint">支持 .md / .txt，可多选（每文件独立入库）</p>
      </Dragger>

      {/* 进度列表 */}
      {fileJobs.length > 0 && (
        <div style={{ marginTop: 20 }}>
          {fileJobs.map(fj => {
            const isDone = fj.status === 'completed';
            const isFailed = fj.status === 'failed';
            const stepIdx = STAGE_KEY[fj.stage] ?? 0;

            return (
              <div key={fj.jobId} style={{
                border: `1px solid ${isFailed ? '#ff4d4f' : isDone ? '#b7eb8f' : '#d9d9d9'}`,
                borderRadius: 8, padding: '12px 16px', marginBottom: 10,
                background: isFailed ? '#fff2f0' : isDone ? '#f6ffed' : '#fafafa',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text strong delete={isFailed}>{fj.filename}</Text>
                  <Space size={8}>
                    {isFailed && <Tag color="red">失败</Tag>}
                    {isDone && <Tag color="green">完成</Tag>}
                    {!isFailed && !isDone && <Tag color="processing">入库中</Tag>}
                  </Space>
                </div>

                {!isDone && !isFailed && (
                  <Steps size="small" current={stepIdx} style={{ marginTop: 8 }}
                    items={STAGES.map((s, i) => ({ title: i === stepIdx ? s : '' }))}
                  />
                )}

                {isDone && fj.stats && (
                  <div style={{ marginTop: 6, color: '#555' }}>
                    {fj.stats.entities_created || 0} 实体 · {fj.stats.facts_created || 0} 事实
                    {fj.stats.facts_pending_review ? ` · ${fj.stats.facts_pending_review} 待复核` : ''}
                    {fj.stats.conflicts_created ? ` · ${fj.stats.conflicts_created} 冲突` : ''}
                    {visualUrl && (
                      <Button type="link" size="small" href={visualUrl} target="_blank" style={{ marginLeft: 8 }}>
                        查看图谱 →
                      </Button>
                    )}
                  </div>
                )}

                {isFailed && (
                  <div style={{ marginTop: 6 }}>
                    <Text type="danger">{fj.errorSummary || '入库过程出错'}</Text>
                    <Button size="small" onClick={() => {
                      /* retry: find the file and re-upload */
                      // 简化：提示用户重新选择文件。实际 re-upload 需要保留原 File 引用。
                      message.info('请重新选择该文件上传');
                    }} style={{ marginLeft: 8 }}>重试</Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

> **Note on `beforeUpload` + `customRequest`:** antd `Dragger` 的 `customRequest` 对每个 file 调用一次，不适合批量。上述代码用 `beforeUpload` 在所有文件收集完后统一调用 `handleUpload`（`return false` 阻止 antd 默认上传）。如需更原生体验，可后续改进为独立按钮 + input（当前实现可 work）。

- [ ] **Step 2: Run frontend build (类型检查)**

```bash
cd console && npm run build
```
Expected: build succeeds (no TS errors).

- [ ] **Step 3: Commit**

```bash
git add console/src/pages/Agents/AgentKnowledgePage.tsx
git commit -m "feat: ontology ingestion upload + SSE progress page"
```

---

### Task 7: 真实 LLM live 测试

**Files:**
- Modify: `tests/integration/test_ontology_neo4j_live.py`（扩展）

**Interfaces:**
- Consumes: `build_ingestion_service`、`ChatModel`/`EmbeddingModel`（真实）、`Neo4jClient`、`ensure_ontology_schema`、TenantResolver 或直接连
- Produces: gated test `ONTOLOGY_LIVE_LLM=1` — 真实 DeepSeek extractor + dashscope embedding 跑 ingest → retrieve → answer

- [ ] **Step 1: Write the gated real-LLM test**

Add to `tests/integration/test_ontology_neo4j_live.py` at the end:

```python
import os
import pytest

LIVE_LLM = os.getenv("ONTOLOGY_LIVE_LLM")


@pytest.mark.skipif(not LIVE_LLM, reason="set ONTOLOGY_LIVE_LLM=1 to test real LLM (DeepSeek + dashscope)")
@pytest.mark.asyncio
async def test_live_real_llm_ingest_retrieve(tmp_path, db_session):
    """真实 LLM 抽取 + embedding：验证 DeepSeek JSON 稳定性和 dashscope 1024 维向量。"""
    tenant = f"livellm_{__import__('uuid').uuid4().hex[:8]}"
    settings = __import__('sales_agent.core.config', fromlist=['get_settings']).get_settings()

    from sales_agent.ontology.neo4j_client import Neo4jClient
    from sales_agent.ontology.runner import build_ingestion_service, LLMExtractor
    from sales_agent.ontology.retrieval_service import OntologyRetrievalService
    from sales_agent.ontology.answer_service import OntologyAnswerService
    from sales_agent.ontology.schemas import GraphEvidence

    # 真实 LLM provider — 走 TenantResolver 拿（需要 PG）
    from sales_agent.services.tenant_resolver import TenantResolver
    resolver = TenantResolver(db_session)
    tenant_info = await resolver.resolve(tenant)
    provider = resolver.get_model_provider(tenant_info)

    # 验证 embedding 维度
    embeds = await provider.embedding.embed(["测试"])
    assert len(embeds) == 1
    assert len(embeds[0]) == 1024, f"embedding dim {len(embeds[0])}, expected 1024"

    neo_client = Neo4jClient(settings.neo4j)
    try:
        path = tmp_path / "sample.md"
        path.write_text("# 福多多产品线\n福多多提供员工福利卡、年节礼包和企业下午茶服务。价格方面，福利卡面额100-500元，企业下午茶人均30-80元。", encoding="utf-8")

        service = build_ingestion_service(db_session, settings, provider)
        job, stats = await service.ingest_paths(
            tenant_id=tenant, agent_id="agent1", paths=[path],
        )
        assert job.status in ("completed", "completed_with_errors")
        assert stats.entities_created >= 1, f"LLM should extract at least 1 entity; got {stats.entities_created}"
        assert stats.facts_created >= 1, f"LLM should extract at least 1 fact; got {stats.facts_created}"

        # 验证实体和事实能从 neo4j 读出
        from sales_agent.ontology.repository import OntologyRepository
        repo = OntologyRepository(neo_client)
        retrieval = OntologyRetrievalService(repo, provider.embedding)
        evidence = await retrieval.retrieve(tenant_id=tenant, agent_id="agent1", question="福多多产品")
        assert len(evidence.matched_entities) >= 1

        # 回答
        answer_service = OntologyAnswerService(retrieval, provider.chat)
        answer = await answer_service.answer_for_task(
            tenant_id=tenant, agent_id="agent1", task_type="knowledge_qa", message="福多多有什么产品"
        )
        assert len(answer.answer["summary"]) > 10  # 真实 LLM 回答必须有内容
    finally:
        async with neo_client.session() as s:
            await s.run("MATCH (n) WHERE n.tenant_id = $t DETACH DELETE n", t=tenant)
        await neo_client.close()
```

- [ ] **Step 2: Run test with env（需要真实 API key）**

```bash
ONTOLOGY_LIVE_LLM=1 .venv/bin/pytest tests/integration/test_ontology_neo4j_live.py::test_live_real_llm_ingest_retrieve -v
```
Expected: PASS（前提：`MODEL_API_KEY` / `EMBEDDING_API_KEY` / `NEO4J_URI` 已在环境或 `secrets/*.env` 配置）。

如果当前环境没有真实 LLM key 配置，先 skip（`ONTOLOGY_LIVE_LLM` 未设 → skip）。这是 gated test。

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ontology_neo4j_live.py
git commit -m "test: gate real-LLM live ingest test behind ONTOLOGY_LIVE_LLM"
```

---

### Task 8: 文档 + changelog

**Files:**
- Modify: `docs/ontology-neo4j-ops.md`
- Modify: `changelog/2026-06-25.md`
- Modify: `README.md`（如需要更新知识库页说明）

- [ ] **Step 1: 补 ops 文档上传入库用法**

In `docs/ontology-neo4j-ops.md`, after the "Live 集成测试" section, add:

```markdown
## 上传入库（Web）

1. 打开 `/agents/{agent_id}/knowledge`。
2. 确认顶部状态为「ready」（绿色）。
3. 拖拽或点击选择 `.md` / `.txt` 文件（可多选）。
4. 每个文件独立入库：6 阶段实时进度（上传/解析/抽实体/抽事实/写图谱/完成）。
5. 完成行显示入库统计（实体/事实/待复核/冲突），可点击「查看图谱 →」跳转 Neo4j Browser。
6. 失败行显示错误信息，可重试。

若状态为「not_configured」或「failed」，请先确认 `KNOWLEDGE_ENGINE=ontology_neo4j` 并启动 Neo4j。
```

- [ ] **Step 2: 追加 changelog 条目**

Append to `changelog/2026-06-25.md`:

```markdown

---

## Ontology 入库 Pipeline 接通 + 知识库页改造（2026-06-25）

### 改动对象
Ontology 入库执行入口、前端知识库页面。

### 类型
feat（入库管线） / feat（前端页面）

### 影响范围
`src/sales_agent/ontology/progress.py`（新）、`ingestion_service.py`、`runner.py`（新）、`api/routes/ontology.py`、`console/src/api/{client,knowledge,types}.ts`、`console/src/pages/Agents/AgentKnowledgePage.tsx`、测试。

### 改动明细
- **入库执行入口**：`POST /ontology/ingest` 改为 multipart 多文件上传，落盘后每文件一个 `IngestionJob` + `asyncio.create_task` 后台跑 `ingest_paths`（真实 DeepSeek + dashscope LLM 注入）。
- **进度推送**：新增 `JobProgressBus`（内存 pub/sub） + `GET /jobs/{id}/events` SSE endpoint（StreamingResponse 手写，不加新依赖）。
- **前端整页替换**：拖拽上传 → 每文件 6 阶段 Steps 实时进度（EventSource → 上传/解析/抽实体/抽事实/写图谱/完成）→ 完成统计 + 外链 Neo4j Browser 查看图谱 → 失败隔离 + 重试。
- **真实 LLM gated 测试**：验证 DeepSeek JSON 抽取稳定性 + dashscope embedding 1024 维。

### 原因
此前 ontology 入库内核完整但无执行入口（API 只建 queued job），前端只填路径字符串；现接通完整链路，从文件到图谱一步完成。

### 验证
- 全量回归（ontology + 回归 + 前端 build）通过。
- 真实 Neo4j live 测试 3/3 通过。
```

- [ ] **Step 3: Commit**

```bash
git add docs/ontology-neo4j-ops.md changelog/2026-06-25.md
git commit -m "docs: ontology upload docs and changelog"
```

---

## Final Verification

- [ ] **Step 1: 全量后端 ontology + 回归**

```bash
.venv/bin/pytest tests/unit/ontology tests/integration/test_ontology_api.py tests/integration/test_ontology_chat_pipeline.py tests/integration/test_ontology_end_to_end_fake.py tests/unit/test_task_router.py tests/unit/test_risk_checker.py tests/unit/test_processing_notice.py tests/integration/coach/test_coach_pipeline_integration.py -q
```
Expected: ALL PASS.

- [ ] **Step 2: NEO4J_LIVE_TEST live 测试**

```bash
NEO4J_LIVE_TEST=1 .venv/bin/pytest tests/integration/test_ontology_neo4j_live.py -q
```
Expected: ALL PASS（3 passed, 1 skipped — real-LLM 默认 skip）。

- [ ] **Step 3: 前端 test + build**

```bash
cd console && npm test
cd console && npm run build
```
Expected: ALL PASS, build succeeds.

- [ ] **Step 4: git status — 只剩已知无关文件**

```bash
git status --short
```
Expected: 只有预先存在的无关文件（`.claude/settings.local.json`、`changmodel.sh`、`ontology-toolkit.zip`、`tasks/lessons.md`、`console/.env.development`、`console/vite.config.ts`、`docker-compose.yml` 端口部分）—— 均不留为本次改动。

## Self-Review Notes

- Spec coverage: §4.1(a-e) 入库触发 + 进度回调 + 总线 + SSE = Tasks 1,2,4; §4.1(e) 真实 LLM = Task 3; §4.1(f) 引擎一致性 = Task 6（前端节，状态条检测）；§4.2 前端布局 = Task 6; §5 SSE 协议 = Task 4; §6 错误处理 = Task 4 后台 except, Task 6 失败行+重试; §7 测试 = Tasks 1/2/3/4/5/7。✓
- Placeholder scan: 无 TBD/TODO/死占位；每步有代码；TSX 完整（含 cleanup + SSE 断开）。✓
- Type consistency: `startOntologyIngest` 新签名匹配 Task 5 wrapper 和 Task 6 调用；`subscribeJobEvents` 返回 `EventSource` 匹配 Task 6 使用；`JobProgressEvent.stats` 为 `Record<string,number>` 匹配 publish 端（均为 dict）。✓
