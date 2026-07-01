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
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.graph.chat_graph import DEBUG_GRAPH_REGISTRY
from sales_agent.graph.checkpoints import get_checkpointer
from sales_agent.core.tenant_runtime import get_tenant_runtime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/{agent_id}/graph-debug", tags=["graph-debug"])

# Thread id prefix that isolates debug runs from production conversations.
# History endpoints refuse any thread_id that does not start with this prefix.
DEBUG_THREAD_PREFIX = "debug:"


# ── Pydantic models ────────────────────────────────────────────────

class GraphInfo(BaseModel):
    id: str
    name: str
    mermaid: str
    node_count: int
    edge_count: int


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

def _count_nodes_edges(mermaid: str) -> tuple[int, int]:
    """Count nodes and edges from a Mermaid diagram string."""
    nodes = sum(1 for line in mermaid.split("\n") if "(" in line and "[" in line)
    edges = sum(1 for line in mermaid.split("\n") if "-->" in line or "-." in line)
    return nodes, edges


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
    if not DEBUG_GRAPH_REGISTRY:
        return GraphsResponse(graphs=[])

    results: list[GraphInfo] = []
    for graph_id, entry in DEBUG_GRAPH_REGISTRY.items():
        try:
            builder = entry["builder"]()
            compiled = builder.compile()
            mermaid = compiled.get_graph().draw_mermaid()
            nodes, edges = _count_nodes_edges(mermaid)
        except Exception as exc:
            logger.warning("Failed to compile graph %s: %s", graph_id, exc)
            mermaid = f"graph TD;\n  error[Graph '{graph_id}' failed to compile: {exc}]"
            nodes, edges = 0, 0

        results.append(GraphInfo(
            id=graph_id,
            name=entry["name"],
            mermaid=mermaid,
            node_count=nodes,
            edge_count=edges,
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
    if req.graph_id not in DEBUG_GRAPH_REGISTRY:
        raise HTTPException(404, f"Unknown graph: {req.graph_id}. Available: {list(DEBUG_GRAPH_REGISTRY)}")

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
    if graph_id not in DEBUG_GRAPH_REGISTRY:
        raise HTTPException(404, f"Unknown graph: {graph_id}. Available: {list(DEBUG_GRAPH_REGISTRY)}")
    entry = DEBUG_GRAPH_REGISTRY[graph_id]
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

