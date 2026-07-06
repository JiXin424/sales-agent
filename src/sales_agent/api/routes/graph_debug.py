"""Graph Debug API — graph visualization + test-run for the console debug page.

GET  /agents/{agent_id}/graph-debug/graphs                          → list available graphs + Mermaid data
POST /agents/{agent_id}/graph-debug/run                             → SSE stream of per-node execution trace
GET  /agents/{agent_id}/graph-debug/threads/{thread_id}/checkpoints → read-only checkpoint timeline
GET  /agents/{agent_id}/graph-debug/threads/{thread_id}/checkpoints/{checkpoint_id}/state → checkpoint state detail
POST /agents/{agent_id}/graph-debug/threads/{thread_id}/checkpoints/{checkpoint_id}/state → edit state (writes a new checkpoint, same thread)
POST /agents/{agent_id}/graph-debug/threads/{thread_id}/checkpoints/{checkpoint_id}/replay → SSE replay from checkpoint (resumes the ``next`` chain)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from langgraph.graph.state import CompiledStateGraph

from sales_agent.api.deps import DbSession
from sales_agent.graph.registry import GRAPH_REGISTRY
from sales_agent.graph.node_metadata import NODE_META_BY_GRAPH, get_node_meta, is_llm_node
from sales_agent.graph.checkpoints import get_checkpointer
from sales_agent.core.tenant_runtime import get_tenant_runtime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/{agent_id}/graph-debug", tags=["graph-debug"])

# Thread id prefix that isolates debug runs from production conversations.
# History endpoints refuse any thread_id that does not start with this prefix.
DEBUG_THREAD_PREFIX = "debug:"


# ── Pydantic models ────────────────────────────────────────────────

class NodeInfo(BaseModel):
    """单个图节点的结构化元数据。"""

    id: str
    name: str
    type: str  # "function" | "subgraph"
    calls_llm: bool
    desc: str = ""
    prompts: list[dict] = []  # [{name, source, note?}]


class EdgeInfo(BaseModel):
    """图的一条边（取自 compiled.get_graph().edges）。"""
    source: str
    target: str


class PromptMapping(BaseModel):
    """节点 → prompt 对应关系的一行（供前端「节点-Prompt 对照」表展示）。"""

    node: str
    calls_llm: bool
    prompt_name: str  # 无 prompt 的纯函数节点为 "—"
    prompt_source: str = ""
    note: str = ""


class GraphInfo(BaseModel):
    id: str
    name: str
    mermaid: str
    node_count: int
    edge_count: int
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []
    prompt_map: list[PromptMapping] = []


class GraphsResponse(BaseModel):
    graphs: list[GraphInfo]


class RunRequest(BaseModel):
    graph_id: str
    message: str
    tenant_id: str = "debug"


class UpdateStateRequest(BaseModel):
    """Body for the checkpoint state-edit endpoint (A2 fork).

    ``values`` is merged into the checkpoint's state via
    ``graph.aupdate_state`` (no ``as_node`` — verified by probe: the default
    behaviour updates the requested keys while preserving ``next``, so the
    fork point's scheduled node chain is intact for replay).
    """

    values: dict[str, Any]
    graph_id: str = "chat"


class ReplayRequest(BaseModel):
    """Body for the checkpoint replay endpoint (A2 fork).

    Replay resumes from a checkpoint by calling ``graph.astream(None, config)``
    with the checkpoint_id pinned in config. ``graph_id`` decides which builder
    is compiled; checkpoint data itself is plain dict state and lives on the
    checkpointer, not the graph instance, so the chat builder can read any
    debug thread.
    """

    graph_id: str = "chat"


# ── Helpers ────────────────────────────────────────────────────────

# Mermaid class + classDef appended to a graph so its subgraph-entry nodes
# stand out from ordinary nodes (orange, thick border). Applied only to the
# online graph, which is the only one that embeds other graphs as nodes.
_SUBGRAPH_CLASS_DEF = (
    "classDef subgraphNode fill:#fff3e0,stroke:#ff9800,"
    "stroke-width:3px,color:#e65100"
)

# LLM 节点视觉区分（蓝色填充），与橙色子图节点、灰色纯函数节点区分。
# 节点是否调 LLM 取自 ``node_metadata`` 集中映射表（LangGraph tags 不暴露）。
_LLM_CLASS_DEF = (
    "classDef llmNode fill:#e6f4ff,stroke:#1677ff,"
    "stroke-width:2px,color:#0958d9"
)


def _normalize_id(name: str) -> str:
    """Normalize a graph/node id for cross-reference (``guided-flow`` == ``guided_flow``)."""
    return name.replace("-", "_").lower()


def _identify_subgraph_nodes(graph) -> list[str]:
    """Return node ids in ``graph`` that are themselves subgraph entry-points.

    A node counts as a subgraph node if either signal fires:
      - its data is a compiled StateGraph — LangGraph-native subgraph wiring
        (e.g. ``guided_flow``, added via ``add_node(name, compiled_subgraph)``);
      - or its normalized id matches a key in ``GRAPH_REGISTRY`` — a wrapper
        node that delegates to a registered top-level graph (e.g. ``chat``,
        wrapped by ``chat_node()`` so LangGraph sees a plain callable, but it
        is still a registered graph invoked as a sub-step).

    ``__start__`` / ``__end__`` are excluded. Only the online graph has such
    nodes; chat and guided-flow diagrams return ``[]``.
    """
    registry_ids = {_normalize_id(k) for k in GRAPH_REGISTRY}
    out: list[str] = []
    for nid, nd in graph.nodes.items():
        if nid in ("__start__", "__end__"):
            continue
        if isinstance(nd.data, CompiledStateGraph) or _normalize_id(nid) in registry_ids:
            out.append(nid)
    return out


def _identify_llm_nodes(graph_id: str, graph) -> list[str]:
    """Return node ids in ``graph`` that call an LLM.

    LLM 判定取自 ``node_metadata`` 集中映射表（LangGraph tags 不从
    ``get_graph().nodes`` 暴露，无法运行时读回）。``__start__``/``__end__`` 排除。
    """
    out: list[str] = []
    for nid in graph.nodes:
        if nid in ("__start__", "__end__"):
            continue
        if is_llm_node(graph_id, nid):
            out.append(nid)
    return out


def _decorate_mermaid(
    mermaid: str,
    subgraph_nodes: list[str],
    llm_nodes: list[str] | None = None,
) -> str:
    """Append highlight classes so subgraph / LLM nodes render with distinct styles.

    - 子图节点：橙色加粗边框（``subgraphNode``）
    - LLM 节点：蓝色填充（``llmNode``）
    一个节点不会同时是子图节点和 LLM 节点（子图节点是流程编排，不直接调 LLM）。
    """
    extra = ""
    if subgraph_nodes:
        extra += f"\n\tclass {','.join(subgraph_nodes)} subgraphNode\n\t{_SUBGRAPH_CLASS_DEF}"
    if llm_nodes:
        extra += f"\n\tclass {','.join(llm_nodes)} llmNode\n\t{_LLM_CLASS_DEF}"
    if not extra:
        return mermaid
    return f"{mermaid}{extra}\n"


def _build_node_infos(graph_id: str, graph) -> tuple[list[NodeInfo], list[PromptMapping]]:
    """从 node_metadata 构建结构化节点列表 + 节点↔prompt 映射表。

    遍历真实图节点（含 __start__/__end__ 排除），元数据取自 ``node_metadata``
    集中表；未知节点按纯函数兜底（calls_llm=False）。prompt_map 每个节点一行：
    多 prompt 节点合并展示 prompt 名列表，无 prompt 节点标「—」。
    """
    meta_table = NODE_META_BY_GRAPH.get(graph_id, {})
    node_infos: list[NodeInfo] = []
    prompt_map: list[PromptMapping] = []
    for nid in graph.nodes:
        if nid in ("__start__", "__end__"):
            continue
        meta = meta_table.get(nid)
        calls_llm = bool(meta and meta.calls_llm)
        ntype = meta.type if meta else "function"
        desc = meta.desc if meta else ""
        prompts = meta.prompts if meta else []

        node_infos.append(NodeInfo(
            id=nid, name=nid, type=ntype,
            calls_llm=calls_llm, desc=desc, prompts=prompts,
        ))

        if prompts:
            # 多 prompt 节点：每个 prompt 一行，便于前端表格逐条展示
            for p in prompts:
                prompt_map.append(PromptMapping(
                    node=nid,
                    calls_llm=calls_llm,
                    prompt_name=p.get("name", ""),
                    prompt_source=p.get("source", ""),
                    note=p.get("note", ""),
                ))
        else:
            prompt_map.append(PromptMapping(
                node=nid, calls_llm=calls_llm, prompt_name="—",
            ))
    return node_infos, prompt_map


# 节点功能说明取自 ``graph.node_metadata`` 集中映射表（单一事实源，
# 同时服务「是否 LLM」「节点↔prompt」）。此处不再维护独立字典，避免漂移。

# 匹配普通节点定义行: ``\tnode(node)`` (id == label,无引号无方括号)。
# __start__/__end__ 行是 ``\t__start__([<p>…</p>]):::first`` 不会匹配;边行无此结构。
_NODE_LABEL_RE = re.compile(r"^(\t)([a-z_][a-z0-9_]*)\(\2\)(.*)$")


def _annotate_node_labels(mermaid: str, skip_nodes: set[str], graph_id: str) -> str:
    """给普通节点 label 追加中文小字功能说明。

    节点名下方加一行灰色小字注解功能: ``node("node<br/><font ...>说明</font>")``。
    跳过 ``__start__``/``__end__``(格式不同)、子图入口节点(``skip_nodes``)、
    以及在 ``node_metadata`` 里无 desc 的节点。HTML 标签需 ``securityLevel=loose``
    (前端 GraphDebugPage 已配置);label 用双引号包裹、font 属性用单引号避免冲突。
    """
    out: list[str] = []
    for line in mermaid.split("\n"):
        m = _NODE_LABEL_RE.match(line)
        if m:
            nid = m.group(2)
            if nid not in skip_nodes:
                meta = get_node_meta(graph_id, nid)
                if meta and meta.desc:
                    label = f"{nid}<br/><font size='2' color='#888'>{meta.desc}</font>"
                    out.append(f'{m.group(1)}{nid}("{label}"){m.group(3)}')
                    continue
        out.append(line)
    return "\n".join(out)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _named_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _safe_serialize(obj: Any) -> Any:
    """Make an object JSON-serializable for SSE output."""
    try:
        json.dumps(obj, ensure_ascii=False, default=str)
        return obj
    except (TypeError, ValueError):
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return str(obj)[:1000]


# ── Routes ─────────────────────────────────────────────────────────

@router.get("/graphs", response_model=GraphsResponse)
async def list_graphs(agent_id: str):
    """Return all registered graphs with Mermaid diagram data."""
    if not GRAPH_REGISTRY:
        return GraphsResponse(graphs=[])

    results: list[GraphInfo] = []
    for graph_id, entry in GRAPH_REGISTRY.items():
        try:
            builder = entry["builder"]()
            compiled = builder.compile()
            g = compiled.get_graph()
            mermaid = g.draw_mermaid()
            # 真实节点/边数取自 graph 对象。原先按 mermaid 文本解析(节点行需同时含
            # "(" 和 "["),但普通节点行是 `name(name)` 仅含 "(",只有 __start__/__end__
            # 的 `([<p>…</p>])` 同时含两者 → 全图 node_count 恒为 2。
            nodes = len(g.nodes)
            edges = len(g.edges)
            subgraph_nodes = _identify_subgraph_nodes(g)
            llm_nodes = _identify_llm_nodes(graph_id, g)
            # 普通节点 label 加中文小字功能说明(跳过 __start__/__end__/子图入口)
            mermaid = _annotate_node_labels(mermaid, set(subgraph_nodes), graph_id)
            # 子图节点(橙) + LLM 节点(蓝)视觉区分
            mermaid = _decorate_mermaid(mermaid, subgraph_nodes, llm_nodes)
            # 结构化节点元数据 + 节点↔prompt 映射（取自 node_metadata 单一事实源）
            node_infos, prompt_map = _build_node_infos(graph_id, g)
            edge_infos = [
                EdgeInfo(source=e.source, target=e.target) for e in g.edges
            ]
        except Exception as exc:
            logger.warning("Failed to compile graph %s: %s", graph_id, exc)
            mermaid = f"graph TD;\n  error[Graph '{graph_id}' failed to compile: {exc}]"
            nodes, edges = 0, 0
            node_infos, prompt_map, edge_infos = [], [], []

        results.append(GraphInfo(
            id=graph_id,
            name=entry["name"],
            mermaid=mermaid,
            node_count=nodes,
            edge_count=edges,
            nodes=node_infos,
            edges=edge_infos,
            prompt_map=prompt_map,
        ))

    return GraphsResponse(graphs=results)


@router.post("/run")
async def run_graph(agent_id: str, req: RunRequest, db: DbSession):
    """Execute a graph with test input, streaming per-node trace via SSE.

    Streams named SSE events:
      - ``started``     → run acknowledged, includes ``thread_id`` for history lookup
      - ``node_start``  → node began executing
      - ``node_output`` → node's return dict
      - ``node_end``    → node finished (with duration_ms)
      - ``token``       → LLM token stream (from messages mode)
      - ``custom``      → progress events from nodes
      - ``done``        → final answer
      - ``error``       → execution error
    """
    if req.graph_id not in GRAPH_REGISTRY:
        raise HTTPException(404, f"Unknown graph: {req.graph_id}. Available: {list(GRAPH_REGISTRY)}")

    # Compile the graph WITH a checkpointer so each node boundary writes a
    # checkpoint to Postgres. The thread_id is prefixed with ``debug:`` to
    # isolate debug runs from production conversations.
    try:
        graph = await _compile_with_checkpointer(req.graph_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to compile graph '{req.graph_id}': {exc}")

    thread_id = f"{DEBUG_THREAD_PREFIX}{uuid.uuid4()}"

    runtime = get_tenant_runtime()

    input_state = {
        "tenant_id": req.tenant_id,
        "user_id": "debug-user",
        "message": req.message,
        # Keep conversation_id in lockstep with thread_id so state inside the
        # graph matches the checkpointer's thread identifier.
        "conversation_id": thread_id,
        "channel": "debug",
        "agent_id": agent_id,
    }

    async def _stream():
        # First event: tell the frontend which thread_id this run lives
        # under, so it can later fetch the checkpoint timeline.
        yield _sse({
            "type": "step",
            "step": 0,
            "message": f"Running graph: {req.graph_id}",
            "status": "started",
            "thread_id": thread_id,
        })
        async for evt in _run_graph_sse(
            graph,
            {"configurable": {"thread_id": thread_id}},
            graph_id=req.graph_id,
            input_state=input_state,
        ):
            yield evt

    return StreamingResponse(_stream(), media_type="text/event-stream")


async def _run_graph_sse(
    graph,
    config: dict,
    *,
    graph_id: str,
    input_state: dict | None = None,
) -> AsyncIterator[str]:
    """Shared SSE streamer for ``/run`` and checkpoint replay.

    ``input_state`` is the fresh state passed to ``graph.astream`` for a normal
    ``/run``. For replay (A2), pass ``input_state=None`` AND include
    ``checkpoint_id`` (plus ``checkpoint_ns=""``) in ``config``: LangGraph then
    resumes from that checkpoint and re-executes its ``next`` node chain — the
    back-half of the graph.

    Emits the same event vocabulary as the original inline streamer:
    ``node_start`` / ``node_output`` / ``node_end`` / ``custom`` / ``done`` /
    ``error``. ``done`` includes ``total_duration_ms`` and ``graph_id`` so the
    frontend does not branch on the run kind.

    LangGraph yields ``tuple[mode, payload]`` when ``stream_mode`` is a list
    (verified against langgraph 1.2.x). A tuple chunk's first element is the
    mode label (``"updates"``/``"custom"``/``"debug"``) and the second is the
    raw payload for that mode — for ``debug`` it is a dict shaped like
    ``{"type": "task"|"task_result"|..., "payload": {...}}``.
    """
    start_time = time.time()
    node_timers: dict[str, float] = {}

    try:
        async for chunk in graph.astream(
            input_state,
            config,
            stream_mode=["updates", "custom", "debug"],
        ):
            # Normalise tuple (list stream_mode) vs dict (single-string
            # stream_mode) into a uniform (chunk_type, data) pair.
            if isinstance(chunk, tuple) and len(chunk) == 2:
                chunk_type, data = chunk
            elif isinstance(chunk, dict):
                chunk_type = chunk.get("type", "")
                data = chunk.get("data", {})
            else:
                # Unknown shape — surface raw so the frontend can show it
                # rather than silently dropping it.
                yield _named_sse("custom", {"data": _safe_serialize(chunk)})
                continue

            # ── Debug events: task start/completion ──
            if chunk_type == "debug":
                payload = data.get("payload", data)
                debug_type = data.get("type", "")
                if debug_type == "task":
                    node_name = payload.get("name", "unknown")
                    node_timers[node_name] = time.time()
                    yield _named_sse("node_start", {
                        "node": node_name,
                        "input": payload.get("input"),
                    })
                elif debug_type == "task_result":
                    node_name = payload.get("name", "unknown")
                    start = node_timers.pop(node_name, time.time())
                    duration_ms = int((time.time() - start) * 1000)
                    yield _named_sse("node_end", {
                        "node": node_name,
                        "duration_ms": duration_ms,
                        "result": payload.get("result"),
                    })

            # ── Updates: per-node output ──
            elif chunk_type == "updates":
                for node_name, node_output in data.items():
                    if isinstance(node_output, dict):
                        yield _named_sse("node_output", {
                            "node": node_name,
                            "output": _safe_serialize(node_output),
                        })

            # ── Custom: progress events from StreamWriter ──
            elif chunk_type == "custom":
                yield _named_sse("custom", {"data": data})

        total_ms = int((time.time() - start_time) * 1000)
        yield _named_sse("done", {
            "total_duration_ms": total_ms,
            "graph_id": graph_id,
        })

    except Exception as exc:
        logger.error("Graph debug stream failed: %s", exc, exc_info=True)
        yield _named_sse("error", {"message": str(exc)[:500]})


# ── Checkpoint history (read-only time-travel) ────────────────────

class CheckpointSummary(BaseModel):
    checkpoint_id: str | None = None
    step: int | None = None
    node: str | None = None
    ts: str | None = None
    next: list[str] | None = None
    # Parent lineage for the branch tree (A3). Populated from
    # ``snapshot.parent_config["configurable"]["checkpoint_id"]`` — the exact
    # parent checkpoint id (probe-verified). ``None`` for the root checkpoint
    # (``step == -1``). For a fork produced by ``aupdate_state`` from a
    # non-latest checkpoint, this points at the fork point.
    parent_checkpoint_id: str | None = None


class CheckpointListResponse(BaseModel):
    checkpoints: list[CheckpointSummary]


class CheckpointStateResponse(BaseModel):
    checkpoint_id: str | None = None
    values: dict[str, Any] | None = None
    next: list[str] | None = None


def _ensure_debug_thread(thread_id: str) -> None:
    """Reject any thread_id that is not a debug run.

    History endpoints must never expose production conversation state, so we
    hard-require the ``debug:`` prefix and 403 otherwise.
    """
    if not thread_id.startswith(DEBUG_THREAD_PREFIX):
        raise HTTPException(
            403,
            "History endpoints are restricted to debug threads "
            f"(must start with '{DEBUG_THREAD_PREFIX}').",
        )


async def _compile_with_checkpointer(graph_id: str):
    """Compile a graph with the shared Postgres checkpointer.

    ``get_checkpointer()`` is a module-level singleton (AsyncPostgresSaver when
    DATABASE_URL is configured, InMemorySaver otherwise), so every call shares
    the same backing store across workers. Shared by /run and the two history
    endpoints.
    """
    if graph_id not in GRAPH_REGISTRY:
        raise HTTPException(404, f"Unknown graph: {graph_id}. Available: {list(GRAPH_REGISTRY)}")
    entry = GRAPH_REGISTRY[graph_id]
    checkpointer = await get_checkpointer()
    builder = entry["builder"]()
    return builder.compile(checkpointer=checkpointer)


def _checkpoint_node_label(snapshot: Any) -> str | None:
    """Best-effort node label for a checkpoint.

    ``StateSnapshot.metadata.writes`` is empty in langgraph>=1.2 (verified at
    runtime: metadata only carries ``source``/``step``/``parents``). The
    reliable node identifier is ``snapshot.tasks[*].name`` — the node scheduled
    to execute from this checkpoint (= ``snapshot.next``). A checkpoint with
    ``next=[X]`` is the pause point right before node X, matching LangGraph's
    interrupt semantics and giving the most useful label for a debug timeline.
    Falls back to ``snapshot.next[0]`` if ``tasks`` is somehow empty.
    """
    tasks = getattr(snapshot, "tasks", None) or []
    for t in tasks:
        name = getattr(t, "name", None)
        if name:
            return str(name)
    nxt = getattr(snapshot, "next", None) or []
    return str(nxt[0]) if nxt else None


@router.get("/threads/{thread_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(
    agent_id: str,
    thread_id: str,
    graph_id: str = Query("chat", description="Graph builder to compile (defaults to chat)"),
):
    """Return the checkpoint timeline for a debug thread (read-only).

    Each item summarises one node boundary:
    ``{checkpoint_id, step, node, ts, next}``. Sorted by ``step`` ascending.
    All fields are best-effort (``.get()`` with ``None`` fallback) so a missing
    LangGraph metadata field never crashes the endpoint.
    """
    _ensure_debug_thread(thread_id)

    graph = await _compile_with_checkpointer(graph_id)

    snapshots: list[Any] = []
    async for snapshot in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
        snapshots.append(snapshot)

    summaries: list[CheckpointSummary] = []
    for snapshot in snapshots:
        metadata = snapshot.metadata or {}
        summaries.append(CheckpointSummary(
            checkpoint_id=(snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
            step=metadata.get("step") if isinstance(metadata, dict) else None,
            node=_checkpoint_node_label(snapshot),
            ts=snapshot.created_at,
            next=list(snapshot.next) if snapshot.next else None,
            parent_checkpoint_id=(snapshot.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
        ))

    # aget_state_history yields newest-first; sort by step ascending (None last).
    summaries.sort(key=lambda s: (s.step is None, s.step if s.step is not None else 0))
    return CheckpointListResponse(checkpoints=summaries)


@router.get("/threads/{thread_id}/checkpoints/{checkpoint_id}/state")
async def get_checkpoint_state(
    agent_id: str,
    thread_id: str,
    checkpoint_id: str,
    graph_id: str = Query("chat", description="Graph builder to compile (defaults to chat)"),
):
    """Return the full state values for a single checkpoint (read-only).

    Walks ``aget_state_history`` for the thread and matches by
    ``configurable.checkpoint_id``. ``values`` is run through
    ``_safe_serialize`` to guarantee JSON-serializable output.
    """
    _ensure_debug_thread(thread_id)

    graph = await _compile_with_checkpointer(graph_id)

    async for snapshot in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
        snap_cp_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
        if snap_cp_id == checkpoint_id:
            return CheckpointStateResponse(
                checkpoint_id=snap_cp_id,
                values=_safe_serialize(snapshot.values),
                next=list(snapshot.next) if snapshot.next else None,
            )

    raise HTTPException(404, f"Checkpoint '{checkpoint_id}' not found for thread '{thread_id}'.")


# ── Checkpoint fork (A2: edit state + replay) ──────────────────────

class UpdateStateResponse(BaseModel):
    """Result of the state-edit endpoint.

    ``checkpoint_id`` is the NEW checkpoint produced by ``aupdate_state``
    (``metadata.source == 'update'``, ``step`` incremented). The frontend
    passes this to ``/replay`` to run the back-half from the fork point.
    """

    checkpoint_id: str | None = None


@router.post(
    "/threads/{thread_id}/checkpoints/{checkpoint_id}/state",
    response_model=UpdateStateResponse,
)
async def update_checkpoint_state(
    agent_id: str,
    thread_id: str,
    checkpoint_id: str,
    req: UpdateStateRequest,
):
    """Edit a checkpoint's state values, writing a new checkpoint on the same thread.

    This is the "fork" entry point (A2). The new checkpoint shares the thread
    with its parent (same ``thread_id``), so it appears in the same
    ``aget_state_history`` timeline alongside the original.

    Lineage note (probe-discovered, important for A3): ``metadata.parents`` is
    ``{}`` for ``aupdate_state``-produced checkpoints in langgraph 1.2.x —
    LangGraph only populates ``parents`` for ``interrupt``-based forks, NOT for
    state updates. A3's branch tree cannot rely on ``metadata.parents``; it
    must reconstruct lineage from the checkpoint chain (``step`` ordering +
    ``source=='update'`` markers within a thread).

    Probe-verified behaviour (do not regress):
      - ``as_node`` is intentionally NOT passed. The default merges ``values``
        into state while preserving the fork point's ``next``, so the scheduled
        node chain stays intact for replay.
      - ``config`` MUST include ``checkpoint_ns=""``; otherwise
        ``aupdate_state`` raises ``KeyError: 'checkpoint_ns'``.
      - The resulting checkpoint has ``metadata.source == 'update'`` and
        ``step == parent.step + 1``, and appears in ``aget_state_history``.
    """
    _ensure_debug_thread(thread_id)

    graph = await _compile_with_checkpointer(req.graph_id)

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_ns": "",
        }
    }

    try:
        # NOTE: no `as_node` — see probe note in docstring.
        await graph.aupdate_state(config, req.values)
    except KeyError as exc:
        # Defensive: LangGraph raises KeyError if config is missing a required
        # key. Surface as 500 with the key name rather than a 422 stack trace.
        raise HTTPException(500, f"aupdate_state missing config key: {exc}")
    except Exception as exc:
        logger.error("update_checkpoint_state failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to update state: {exc}")

    # Read back the latest checkpoint id for this thread. ``aget_state`` with
    # only thread_id (+ checkpoint_ns) returns the most recent snapshot, whose
    # config carries the new checkpoint_id written by aupdate_state.
    latest = await graph.aget_state(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    )
    new_cp_id = (latest.config or {}).get("configurable", {}).get("checkpoint_id")

    return UpdateStateResponse(checkpoint_id=new_cp_id)


@router.post("/threads/{thread_id}/checkpoints/{checkpoint_id}/replay")
async def replay_checkpoint(
    agent_id: str,
    thread_id: str,
    checkpoint_id: str,
    req: ReplayRequest | None = None,
):
    """Re-run the back-half of a graph from a checkpoint, streaming SSE trace.

    Resumes execution by calling ``graph.astream(None, config)`` with
    ``checkpoint_id`` pinned in config: ``input=None`` tells LangGraph to
    restore the checkpoint's state and execute its ``next`` node chain.

    Reuses ``_run_graph_sse`` so the event vocabulary is identical to ``/run``
    (``node_start`` / ``node_output`` / ``node_end`` / ``custom`` / ``done`` /
    ``error``). After ``done``, the frontend refreshes ``getCheckpoints`` and
    the new branch checkpoint appears in the timeline.

    Probe-verified: ``config`` MUST include ``checkpoint_ns=""`` alongside
    ``checkpoint_id``; without it ``astream`` raises
    ``KeyError: 'checkpoint_ns'``.

    Body is optional (defaults to ``{graph_id: "chat"}``) so the endpoint can
    be invoked with an empty POST body.
    """
    _ensure_debug_thread(thread_id)

    body = req or ReplayRequest()
    graph = await _compile_with_checkpointer(body.graph_id)

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_ns": "",
        }
    }

    async def _stream():
        async for evt in _run_graph_sse(
            graph,
            config,
            graph_id=body.graph_id,
            input_state=None,
        ):
            yield evt

    return StreamingResponse(_stream(), media_type="text/event-stream")

