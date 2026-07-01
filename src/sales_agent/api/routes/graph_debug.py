"""Graph Debug API — graph visualization + test-run for the console debug page.

GET  /agents/{agent_id}/graph-debug/graphs   → list available graphs + Mermaid data
POST /agents/{agent_id}/graph-debug/run      → SSE stream of per-node execution trace
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.graph.chat_graph import DEBUG_GRAPH_REGISTRY
from sales_agent.core.tenant_runtime import get_tenant_runtime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/{agent_id}/graph-debug", tags=["graph-debug"])


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

    entry = DEBUG_GRAPH_REGISTRY[req.graph_id]

    # Compile the graph
    try:
        builder = entry["builder"]()
        graph = builder.compile()
    except Exception as exc:
        raise HTTPException(500, f"Failed to compile graph '{req.graph_id}': {exc}")

    runtime = get_tenant_runtime()

    input_state = {
        "tenant_id": req.tenant_id,
        "user_id": "debug-user",
        "message": req.message,
        "conversation_id": str(uuid.uuid4()),
        "channel": "debug",
        "agent_id": agent_id,
    }

    async def _stream():
        start_time = time.time()
        node_timers: dict[str, float] = {}
        token_buffer: list[str] = []
        last_flush = time.time()

        try:
            # Validate the graph compiles before streaming
            yield _sse({"type": "step", "step": 0, "message": f"Running graph: {req.graph_id}", "status": "started"})

            async for chunk in graph.astream(
                input_state,
                {"configurable": {"thread_id": str(uuid.uuid4())}},
                stream_mode=["updates", "custom", "debug"],
            ):
                chunk_type = chunk.get("type", "")
                data = chunk.get("data", {})

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

            # Extract final answer from the last state
            yield _named_sse("done", {
                "total_duration_ms": total_ms,
                "graph_id": req.graph_id,
            })

        except Exception as exc:
            logger.error("Graph debug run failed: %s", exc, exc_info=True)
            yield _named_sse("error", {"message": str(exc)[:500]})

    return StreamingResponse(_stream(), media_type="text/event-stream")


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
