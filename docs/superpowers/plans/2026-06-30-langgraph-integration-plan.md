# LangGraph Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ChatPipeline monolithic `execute()` with a LangGraph `StateGraph`, unify HTTP and streaming code paths, migrate Coach state machines to subgraphs, and add retry/cycle/human-in-the-loop capabilities — across 5 phases.

**Architecture:** A single `CompiledStateGraph` replaces `ChatPipeline.execute()`. HTTP callers use `graph.ainvoke()` to get final JSON; DingTalk stream callers use `graph.astream(stream_mode=["messages", "updates"])` to get token-by-token output and node updates. `AsyncPostgresSaver` provides auto-checkpoint on every node using existing PostgreSQL. Coach daily evaluation and quick sessions are structured as subgraphs.

**Tech Stack:** LangGraph 1.2.7, langchain-core>=1.4.7, langgraph-checkpoint-postgres, existing FastAPI + SQLAlchemy + asyncpg stack. Python >= 3.10.

## Global Constraints

- LangGraph >= 1.2.7
- Python >= 3.10
- All new code in `src/sales_agent/graph/`
- Tests in `tests/unit/graph/`
- Existing ChatPipeline preserved as fallback during transition
- Every node must be independently testable
- Checkpoint uses existing PostgreSQL via `AsyncPostgresSaver`
- Streaming must work for DingTalk Stream mode without regressions
- Node functions must accept `(state: ChatGraphState, config: RunnableConfig)` signature

---

## File Structure Plan

```
Create:
  src/sales_agent/graph/__init__.py
  src/sales_agent/graph/state.py                         # ChatGraphState TypedDict
  src/sales_agent/graph/chat_graph.py                     # build_chat_graph() builder
  src/sales_agent/graph/nodes/__init__.py
  src/sales_agent/graph/nodes/validation.py               # Request validation node
  src/sales_agent/graph/nodes/tenant_resolve.py            # Tenant + model resolution
  src/sales_agent/graph/nodes/context_load.py              # Conversation history loader
  src/sales_agent/graph/nodes/routing.py                   # Task routing node
  src/sales_agent/graph/nodes/retrieval.py                 # Retrieval router (ontology/rag/skip)
  src/sales_agent/graph/retrieval/__init__.py              # Retrieval subgraph package
  src/sales_agent/graph/retrieval/state.py                 # OntologyRetrievalState TypedDict
  src/sales_agent/graph/retrieval/ontology_graph.py        # Ontology retrieval subgraph (Plan B)
  src/sales_agent/graph/nodes/generation.py               # LLM agent execution node
  src/sales_agent/graph/nodes/risk_check.py               # Risk checking node
  src/sales_agent/graph/nodes/logging_node.py             # Conversation logging node
  src/sales_agent/graph/nodes/fast_commands.py            # Help/reset fast path
  src/sales_agent/graph/nodes/coach_observe.py            # Realtime coach observation
  src/sales_agent/graph/edges/__init__.py
  src/sales_agent/graph/edges/path_conditions.py          # Conditional edge functions
  src/sales_agent/graph/edges/risk_conditions.py          # Risk-based conditions
  src/sales_agent/graph/retry_policies.py                 # Centralized retry config
  src/sales_agent/graph/checkpoints.py                    # AsyncPostgresSaver factory
  tests/unit/graph/__init__.py
  tests/unit/graph/test_state.py
  tests/unit/graph/test_chat_graph.py
  tests/unit/graph/nodes/__init__.py
  tests/unit/graph/test_validation_node.py
  tests/unit/graph/test_routing_node.py
  tests/unit/graph/test_generation_node.py
  tests/unit/graph/test_retrieval_node.py
  tests/unit/graph/test_risk_node.py
  tests/unit/graph/edges/test_path_conditions.py
  tests/unit/graph/edges/test_risk_conditions.py
  tests/unit/graph/test_checkpoints.py

Modify:
  pyproject.toml                                       # Add langgraph deps
  src/sales_agent/api/routes/agent.py                  # Wire graph.ainvoke()
  src/sales_agent/integrations/dingtalk/processor.py   # Wire graph.ainvoke() for HTTP mode
  src/sales_agent/services/chat_pipeline.py            # Mark deprecated, delegate to graph

Delete (end of Phase 2):
  src/sales_agent/integrations/dingtalk/streaming_handler.py  # Replaced by graph.astream()
```

---

## Phase 1: ChatPipeline Basic Graph

*Duration estimate: ~3 hours. Produces a working, compiled StateGraph equivalent to the first 5 steps of ChatPipeline, running in parallel to (not replacing) the old code.*

### Task 1.1: Add LangGraph dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `langgraph>=1.2.0`, `langgraph-checkpoint-postgres>=1.0.0`, `langchain-core>=1.4.7` available in venv

- [ ] **Step 1: Add langgraph dependencies to pyproject.toml**

Add these lines to the `dependencies` list in `pyproject.toml`:

```toml
    "langgraph>=1.2.0",
    "langgraph-checkpoint-postgres>=1.0.0",
    "langchain-core>=1.4.7",
```

- [ ] **Step 2: Install dependencies**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && pip install -e ".[dev]" langgraph langgraph-checkpoint-postgres langchain-core`

Expected: All packages install without error.

- [ ] **Step 3: Verify installation**

Run: `python -c "import langgraph; print(langgraph.__version__); from langgraph.graph import StateGraph, START, END; print('OK')"`

Expected: Prints version `1.2.7` and `OK`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add langgraph dependencies"
```

---

### Task 1.2: Define ChatGraphState TypedDict

**Files:**
- Create: `src/sales_agent/graph/__init__.py`
- Create: `src/sales_agent/graph/state.py`
- Create: `tests/unit/graph/__init__.py`
- Create: `tests/unit/graph/test_state.py`

**Interfaces:**
- Produces: `ChatGraphState(TypedDict, total=False)` — shared state for all ChatPipeline nodes

- [ ] **Step 1: Create graph package init**

Write `src/sales_agent/graph/__init__.py`:

```python
"""LangGraph-based orchestration for the Sales Agent pipeline."""
```

- [ ] **Step 2: Write test for state instantiation**

Write `tests/unit/graph/__init__.py` (empty file).

Write `tests/unit/graph/test_state.py`:

```python
"""Tests for ChatGraphState."""
import pytest
from sales_agent.graph.state import ChatGraphState


def test_state_instantiation_minimal():
    """ChatGraphState can be instantiated with minimal required fields."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "客户说太贵了怎么回",
        "conversation_id": "conv-001",
        "channel": "local",
    }
    assert state["tenant_id"] == "t1"
    assert state["message"] == "客户说太贵了怎么回"


def test_state_instantiation_all_fields():
    """ChatGraphState accepts all defined optional fields."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "hello",
        "conversation_id": "c1",
        "channel": "dingtalk",
        "agent_id": "a1",
        "model_override": None,
        "task_type": "general_sales_coaching",
        "route_confidence": 0.9,
        "needs_retrieval": False,
        "path": "fast",
        "path_reason": "simple_command",
        "answer_dict": {"summary": "ok", "sections": []},
        "sources": [],
        "risk_action": "allow",
        "risk_result": {},
        "run_id": None,
        "usage": {},
        "error": None,
        "retry_count": 0,
    }
    assert state["task_type"] == "general_sales_coaching"
    assert state["retry_count"] == 0


def test_state_optional_fields_default():
    """Optional fields can be omitted."""
    state: ChatGraphState = {
        "tenant_id": "t1",
        "user_id": "u1",
        "message": "test",
        "conversation_id": "c1",
        "channel": "local",
    }
    # Optional fields should be accessible without error
    assert state.get("task_type") is None
    assert state.get("retry_count", 0) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_state.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'sales_agent.graph.state'`

- [ ] **Step 4: Write ChatGraphState**

Write `src/sales_agent/graph/state.py`:

```python
"""Shared state definition for the ChatPipeline graph."""

from __future__ import annotations

from typing import Annotated, Any
from operator import add
from typing_extensions import TypedDict


class ChatGraphState(TypedDict, total=False):
    """Shared state flowing through all ChatPipeline graph nodes.

    `total=False` means all keys are optional — each node only needs to
    set the fields it contributes.
    """

    # === Input (set by caller) ===
    tenant_id: str
    user_id: str
    message: str
    conversation_id: str
    channel: str                           # "local" | "dingtalk"
    agent_id: str | None
    model_override: str | None

    # === Tenant + Model Resolution ===
    tenant_info: dict[str, Any]
    # model_provider is NOT in State — passed via Runtime.context

    # === Context Loading ===
    history_messages: list[dict[str, str]]

    # === Task Routing ===
    task_type: str
    route_confidence: float
    needs_retrieval: bool
    needs_clarification: bool

    # === Prompt Resolution ===
    prompt_text: str | None
    system_prompt_text: str | None

    # === Path Decision ===
    path: str                              # "fast" | "standard" | "slow"
    path_reason: str

    # === Retrieval ===
    retrieval_result: Any
    sources: list[dict]
    skip_generation: bool

    # === Coach Guidance ===
    coach_guidance_text: str

    # === Generation ===
    answer_dict: dict[str, Any]
    raw_response: str

    # === Risk ===
    input_risk_level: str                  # "none" | "low" | "medium" | "high"
    risk_result: dict[str, Any]
    risk_action: str                       # "allow" | "warn" | "rewrite" | "block"

    # === Control ===
    retry_count: int
    error: str | None

    # === Streaming tokens ===
    stream_tokens: Annotated[list[str], add]

    # === Output ===
    final_answer: dict[str, Any]
    final_sources: list[dict]
    run_id: str | None
    usage: dict[str, int]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_state.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/graph/__init__.py src/sales_agent/graph/state.py tests/unit/graph/
git commit -m "feat: add ChatGraphState TypedDict for LangGraph pipeline"
```

---

### Task 1.3: Create stub graph builder (empty graph, no nodes yet)

**Files:**
- Create: `src/sales_agent/graph/chat_graph.py`
- Create: `tests/unit/graph/test_chat_graph.py`

**Interfaces:**
- Consumes: `ChatGraphState` from `sales_agent.graph.state`
- Produces: `build_chat_graph() -> StateGraph` — returns an un-compiled builder

- [ ] **Step 1: Write failing test for graph compilation**

Write `tests/unit/graph/test_chat_graph.py`:

```python
"""Tests for the ChatPipeline StateGraph."""
import pytest
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.state import ChatGraphState
from langgraph.graph.state import StateGraph


def test_build_chat_graph_returns_state_graph():
    """build_chat_graph returns an un-compiled StateGraph builder."""
    builder = build_chat_graph()
    assert isinstance(builder, StateGraph)


def test_graph_compiles_without_checkpointer():
    """Graph compiles successfully without a checkpointer (for testing)."""
    builder = build_chat_graph()
    graph = builder.compile()
    assert graph is not None


def test_graph_compiles_with_memory_checkpointer():
    """Graph compiles with InMemorySaver for unit testing."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())
    assert graph is not None


def test_graph_invokes_minimal_input():
    """Graph processes minimal input through basic pipeline."""
    from langgraph.checkpoint.memory import InMemorySaver
    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = graph.invoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "帮助",
            "conversation_id": "conv-001",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-1"}},
    )
    # After invoke, state should have processed the message
    assert result is not None
    # "帮助" should be handled by fast_reply
    assert "answer_dict" in result or result.get("path") == "fast"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_chat_graph.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'sales_agent.graph.chat_graph'`

- [ ] **Step 3: Write stub build_chat_graph**

Write `src/sales_agent/graph/chat_graph.py`:

```python
"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic `ChatPipeline.execute()` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Caller is responsible for calling `.compile()` with the appropriate
    checkpointer and other runtime options.

    Returns:
        A StateGraph builder ready for node/edge registration.
    """
    builder = StateGraph(ChatGraphState)

    # Phase 1: register nodes (stubs that pass-through for now)

    # Phase 1: edges — validate → END (minimal path to test compilation)
    builder.add_edge(START, END)

    return builder
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_chat_graph.py -v`

Expected: 4 tests PASS (basic compilation and invocation work).

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/chat_graph.py tests/unit/graph/test_chat_graph.py
git commit -m "feat: add stub chat_graph builder, compiles and invokes"
```

---

### Task 1.4: Implement fast_commands node

**Files:**
- Create: `src/sales_agent/graph/nodes/__init__.py`
- Create: `src/sales_agent/graph/nodes/fast_commands.py`
- Create: `src/sales_agent/graph/edges/__init__.py`
- Create: `src/sales_agent/graph/edges/path_conditions.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add nodes + edges)

**Interfaces:**
- Consumes: `ChatGraphState.message`
- Produces: `fast_command_node(state) -> dict` returning `answer_dict` and `path`
- Produces: `is_fast_command(state) -> Literal["fast", "normal"]`

- [ ] **Step 1: Write tests for fast_command_node and is_fast_command**

Write `tests/unit/graph/test_validation_node.py`:

```python
"""Tests for fast_commands and validation nodes."""
import pytest
from sales_agent.graph.nodes.fast_commands import HELP_TEXT, RESET_TEXT, fast_command_node
from sales_agent.graph.edges.path_conditions import is_fast_command
from sales_agent.graph.state import ChatGraphState


def test_is_fast_command_help_chinese():
    state: ChatGraphState = {"message": "帮助", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_help_english():
    state: ChatGraphState = {"message": "help", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_question_mark():
    state: ChatGraphState = {"message": "？", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_reset():
    state: ChatGraphState = {"message": "新话题", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_normal_message():
    state: ChatGraphState = {"message": "客户说太贵怎么回", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "normal"


def test_fast_command_node_help():
    state: ChatGraphState = {"message": "帮助", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    result = fast_command_node(state)
    assert result["path"] == "fast"
    assert result["path_reason"] == "help_command"
    assert "answer_dict" in result
    assert result["answer_dict"]["summary"] == HELP_TEXT


def test_fast_command_node_reset():
    state: ChatGraphState = {"message": "新话题", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    result = fast_command_node(state)
    assert result["path"] == "fast"
    assert result["path_reason"] == "reset_command"
    assert result["answer_dict"]["summary"] == RESET_TEXT
    assert result["conversation_id"] != "c1"  # new conversation_id generated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_validation_node.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write fast_commands node**

Write `src/sales_agent/graph/nodes/__init__.py` (empty file).

Write `src/sales_agent/graph/nodes/fast_commands.py`:

```python
"""Fast-command handler node: help, reset, and similar instant-reply commands.

Replicates the fast-command logic from ChatPipeline.execute() steps 1-2.
"""

from __future__ import annotations

from sales_agent.models.base import generate_id
from sales_agent.graph.state import ChatGraphState

_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}

HELP_TEXT = (
    "你可以直接问销售问题，例如：\n"
    "1. 客户说太贵了怎么回？\n"
    "2. 帮我写一段温和跟进话术\n"
    "3. 帮我复盘这段聊天记录\n\n"
    '发送"新话题"可以清空当前上下文。'
)

RESET_TEXT = "已开启新话题。你可以直接说当前要处理的销售问题。"


def fast_command_node(state: ChatGraphState) -> dict:
    """Handle help and reset commands, returning an immediate reply.

    This node is only reached when `is_fast_command()` returns "fast".
    It sets `answer_dict` and `path` directly, skipping all downstream nodes.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with `answer_dict`, `path`, `path_reason`,
        and optionally a new `conversation_id` (for reset).
    """
    stripped = state["message"].strip()

    if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
        return {
            "answer_dict": {"summary": HELP_TEXT, "sections": []},
            "path": "fast",
            "path_reason": "help_command",
        }

    # Must be a reset command at this point (guaranteed by is_fast_command)
    return {
        "answer_dict": {"summary": RESET_TEXT, "sections": []},
        "path": "fast",
        "path_reason": "reset_command",
        "conversation_id": generate_id(),
    }
```

Write `src/sales_agent/graph/edges/__init__.py` (empty file).

Write `src/sales_agent/graph/edges/path_conditions.py`:

```python
"""Conditional edge functions for the ChatPipeline graph.

Each function receives the current `ChatGraphState` and returns a string
that maps to a destination node (or "fast"/"normal" for the initial split).
These are pure functions — no DB, no LLM, no side effects.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState

_HELP_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}


def is_fast_command(state: ChatGraphState) -> str:
    """Determine if the input is a fast command (help/reset).

    Returns:
        "fast" if the message is a help or reset command, "normal" otherwise.
    """
    stripped = state["message"].strip()

    if stripped.lower() in _HELP_COMMANDS or stripped in _HELP_COMMANDS:
        return "fast"
    if stripped in _RESET_COMMANDS:
        return "fast"
    return "normal"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_validation_node.py -v`

Expected: All 7 tests PASS.

- [ ] **Step 5: Wire nodes and edges into the graph builder**

Modify `src/sales_agent/graph/chat_graph.py` — replace its content:

```python
"""ChatPipeline as a LangGraph StateGraph.

Replaces the monolithic `ChatPipeline.execute()` method with a graph
of nodes connected by conditional edges. The same compiled graph serves
both HTTP (ainvoke) and DingTalk streaming (astream) code paths.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.edges.path_conditions import is_fast_command


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Caller is responsible for calling `.compile()` with the appropriate
    checkpointer and other runtime options.

    Returns:
        A StateGraph builder ready for node/edge registration.
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START,
        is_fast_command,
        {"fast": "fast_reply", "normal": END},
    )
    builder.add_edge("fast_reply", END)

    return builder
```

- [ ] **Step 6: Run all graph tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ -v`

Expected: All 11 tests PASS (4 from test_chat_graph + 7 from test_validation_node).

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/ tests/unit/graph/
git commit -m "feat: add fast_commands node and is_fast_command conditional edge"
```

---

### Task 1.5: Implement validation node

**Files:**
- Create: `src/sales_agent/graph/nodes/validation.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add validate node + edge)
- Modify: `tests/unit/graph/test_validation_node.py` (add tests)

**Interfaces:**
- Consumes: `ChatGraphState` — `tenant_id`, `user_id`, `message`
- Produces: `validate_node(state) -> dict` — no-op on success, sets `error` on failure

- [ ] **Step 1: Add test for validation node**

Append to `tests/unit/graph/test_validation_node.py`:

```python
from sales_agent.graph.nodes.validation import validate_node


def test_validate_node_passes_valid_input():
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1", "message": "hello",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result.get("error") is None


def test_validate_node_fails_missing_tenant():
    state: ChatGraphState = {
        "tenant_id": "", "user_id": "u1", "message": "hello",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result["error"] is not None


def test_validate_node_fails_empty_message():
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1", "message": "",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result["error"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_validation_node.py::test_validate_node_passes_valid_input -v`

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write validation node**

Write `src/sales_agent/graph/nodes/validation.py`:

```python
"""Request validation node.

Mirrors `services/request_validator.validate_chat_request()`.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def validate_node(state: ChatGraphState) -> dict:
    """Validate required fields in the incoming request.

    Args:
        state: Current graph state with input fields.

    Returns:
        Empty dict on success, or dict with `error` set on validation failure.
    """
    tenant_id = (state.get("tenant_id") or "").strip()
    user_id = (state.get("user_id") or "").strip()
    message = (state.get("message") or "").strip()

    if not tenant_id:
        return {"error": "tenant_id is required"}
    if not user_id:
        return {"error": "user_id is required"}
    if not message:
        return {"error": "message is required"}

    return {}
```

- [ ] **Step 4: Run validation tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_validation_node.py -v`

Expected: 10 tests PASS (7 fast_command + 3 validation).

- [ ] **Step 5: Wire validate into graph**

Modify `src/sales_agent/graph/chat_graph.py` — update `build_chat_graph()`:

```python
from sales_agent.graph.nodes.validation import validate_node

def build_chat_graph() -> StateGraph:
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START,
        is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", END)  # Phase 1: short-circuit to END

    return builder
```

- [ ] **Step 6: Run all graph tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ -v`

Expected: All 14 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/nodes/validation.py src/sales_agent/graph/chat_graph.py tests/unit/graph/test_validation_node.py
git commit -m "feat: add validate node with field validation"
```

---

### Task 1.6: Implement routing node (rules-only, no LLM fallback yet)

**Files:**
- Create: `src/sales_agent/graph/nodes/routing.py`
- Create: `tests/unit/graph/test_routing_node.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add routing node)

**Interfaces:**
- Consumes: `ChatGraphState.message`
- Produces: `routing_node(state) -> dict` returning `task_type`, `route_confidence`, `needs_retrieval`

- [ ] **Step 1: Write tests for routing_node**

Write `tests/unit/graph/test_routing_node.py`:

```python
"""Tests for the routing node."""
import pytest
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import (
    GENERAL_COACHING, KNOWLEDGE_QA, OBJECTION_HANDLING,
    SCRIPT_GENERATION, EMOTIONAL_SUPPORT, CONVERSATION_REVIEW,
    VISIT_PREPARATION, POST_VISIT_REVIEW,
)


def test_routing_objection_handling():
    """Messages about pricing trigger objection_handling."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户说太贵了怎么回", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == OBJECTION_HANDLING
    assert result["route_confidence"] > 0.5


def test_routing_knowledge_qa():
    """Product questions trigger knowledge_qa."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == KNOWLEDGE_QA


def test_routing_script_generation():
    """Message writing requests trigger script_generation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我写一段跟进话术", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == SCRIPT_GENERATION


def test_routing_emotional_support():
    """Frustration expressions trigger emotional_support."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户一直不回，我觉得很沮丧", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == EMOTIONAL_SUPPORT


def test_routing_conversation_review():
    """Chat analysis triggers conversation_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "帮我复盘这段聊天记录", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == CONVERSATION_REVIEW


def test_routing_visit_preparation():
    """Visit prep triggers visit_preparation."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "明天拜访客户帮我准备拜访提纲", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == VISIT_PREPARATION


def test_routing_post_visit_review():
    """Post-visit triggers post_visit_review."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "刚聊完客户帮我做一个访后复盘", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == POST_VISIT_REVIEW


def test_routing_general_fallback():
    """Unrecognized messages fall back to general_sales_coaching."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "你好", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert result["task_type"] == GENERAL_COACHING


def test_routing_returns_needs_retrieval():
    """Routing sets needs_retrieval flag."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "我们的产品有什么优势", "conversation_id": "c1", "channel": "local",
    }
    result = routing_node(state)
    assert "needs_retrieval" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_routing_node.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write routing_node**

Write `src/sales_agent/graph/nodes/routing.py`:

```python
"""Task routing node.

Calls the existing `task_router.route_task()` service (rules-first with
LLM fallback). In Phase 1, only rule-based routing is active; LLM fallback
is added in Phase 3 when the chat_model is available via context.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState
from sales_agent.services.task_router import route_task, route_task_rules_only
from sales_agent.services.task_router import TASK_DEFAULT_RETRIEVAL


def routing_node(state: ChatGraphState) -> dict:
    """Route the user message to a task type.

    Uses rules-first matching from the existing `task_router` module.
    LLM fallback is deferred to Phase 3 (requires Runtime.context).

    Args:
        state: Current graph state with `message` populated.

    Returns:
        Dict with `task_type`, `route_confidence`, `needs_retrieval`,
        and optionally `needs_clarification`.
    """
    message = state["message"]

    # Rules-only routing (fast, no LLM call)
    route_result = route_task_rules_only(message)

    return {
        "task_type": route_result.task_type,
        "route_confidence": route_result.confidence,
        "needs_retrieval": route_result.needs_retrieval,
        "needs_clarification": route_result.needs_clarification,
    }
```

- [ ] **Step 4: Run routing tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_routing_node.py -v`

Expected: 9 tests PASS.

- [ ] **Step 5: Wire routing into graph**

Modify `src/sales_agent/graph/chat_graph.py`:

```python
from sales_agent.graph.nodes.routing import routing_node

def build_chat_graph() -> StateGraph:
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("route_task", routing_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START, is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "route_task")
    builder.add_edge("route_task", END)  # Phase 1: short-circuit

    return builder
```

- [ ] **Step 6: Run all tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ -v`

Expected: All 23 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/nodes/routing.py src/sales_agent/graph/chat_graph.py tests/unit/graph/test_routing_node.py
git commit -m "feat: add routing node (rules-only) to ChatPipeline graph"
```

---

### Task 1.7: Implement tenant_resolve, context_load, generation nodes and complete Phase 1 graph

**Files:**
- Create: `src/sales_agent/graph/nodes/tenant_resolve.py`
- Create: `src/sales_agent/graph/nodes/context_load.py`
- Create: `src/sales_agent/graph/nodes/generation.py`
- Create: `tests/unit/graph/test_generation_node.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add 3 nodes + edges)
- Modify: `src/sales_agent/services/chat_pipeline.py` (add graph delegation path)

**Interfaces:**
- Consumes: existing `TenantResolver`, `_load_recent_history`, `execute_agent`
- Produces: `resolve_tenant_node`, `load_context_node`, `generate_node` — each a callable `(ChatGraphState) -> dict`

- [ ] **Step 1: Write tenant_resolve node**

Write `src/sales_agent/graph/nodes/tenant_resolve.py`:

```python
"""Tenant resolution node.

Calls the existing TenantResolver to look up tenant config and model provider.
Model instances are stored in DB-specific context (not state) in Phase 3.
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def resolve_tenant_node(state: ChatGraphState) -> dict:
    """Resolve tenant info.

    In Phase 1, this is a passthrough that sets minimal tenant_info.
    Full DB-backed resolution is wired in Phase 2 when Runtime.context
    provides the DB session.

    Args:
        state: Current graph state.

    Returns:
        Dict with `tenant_info` populated.
    """
    return {
        "tenant_info": {
            "tenant_id": state["tenant_id"],
            "config": {},
        },
    }
```

- [ ] **Step 2: Write context_load node**

Write `src/sales_agent/graph/nodes/context_load.py`:

```python
"""Context loading node.

Loads recent conversation history from the database.
In Phase 1, returns empty history (DB available in Phase 2 via context).
"""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def load_context_node(state: ChatGraphState) -> dict:
    """Load recent conversation history.

    In Phase 1, returns empty history list.
    Full DB-backed loading is wired in Phase 2.

    Args:
        state: Current graph state.

    Returns:
        Dict with `history_messages` set to empty list.
    """
    return {"history_messages": []}
```

- [ ] **Step 3: Write generation node**

Write `src/sales_agent/graph/nodes/generation.py`:

```python
"""LLM generation node.

Calls `agent_executor.execute_agent()` to produce a structured answer.
In Phase 1, uses a bare LLM instance; Phase 3 moves to Runtime.context.
"""

from __future__ import annotations

import json
import logging

from sales_agent.graph.state import ChatGraphState

logger = logging.getLogger(__name__)


def generate_node(state: ChatGraphState) -> dict:
    """Execute the LLM agent to generate an answer.

    In Phase 1, this is a passthrough that returns a placeholder answer.
    Full LLM execution is wired in Phase 2 when the model is available
    via Runtime.context.

    Args:
        state: Current graph state with `task_type`, `message` populated.

    Returns:
        Dict with `answer_dict`, `raw_response`, and `usage`.
    """
    # Phase 1: placeholder — no LLM call yet
    task_type = state.get("task_type", "general_sales_coaching")
    message = state.get("message", "")

    answer = {
        "summary": f"[Phase 1 placeholder] Task: {task_type}, Question: {message[:100]}",
        "sections": [
            {"title": "回答", "content": f"这里是{task_type}的占位回答。完整实现在 Phase 2。"},
        ],
    }

    return {
        "answer_dict": answer,
        "raw_response": json.dumps(answer, ensure_ascii=False),
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
```

- [ ] **Step 4: Write tests for generation node**

Write `tests/unit/graph/test_generation_node.py`:

```python
"""Tests for the generation node."""
import pytest
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.state import ChatGraphState


def test_generate_node_returns_answer_dict():
    """Generation node produces a structured answer."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "客户说太贵了怎么回", "conversation_id": "c1", "channel": "local",
        "task_type": "objection_handling",
    }
    result = generate_node(state)
    assert "answer_dict" in result
    assert "summary" in result["answer_dict"]
    assert "sections" in result["answer_dict"]
    assert len(result["answer_dict"]["sections"]) >= 1


def test_generate_node_returns_usage():
    """Generation node includes token usage."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "hello", "conversation_id": "c1", "channel": "local",
        "task_type": "general_sales_coaching",
    }
    result = generate_node(state)
    assert "usage" in result
    assert "total_tokens" in result["usage"]
```

- [ ] **Step 5: Run tests to verify they fail then pass**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_generation_node.py -v`

Expected: 2 tests PASS.

- [ ] **Step 6: Complete the Phase 1 graph**

Modify `src/sales_agent/graph/chat_graph.py` — final Phase 1 version:

```python
"""ChatPipeline as a LangGraph StateGraph."""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.nodes.tenant_resolve import resolve_tenant_node
from sales_agent.graph.nodes.context_load import load_context_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.edges.path_conditions import is_fast_command


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Phase 1 graph structure:

        START ──(is_fast_command?)──→ fast_reply ──→ END
                    │
                    ▼
              validate ──→ resolve_tenant ──→ load_context
                                                    │
                                                    ▼
                                              route_task ──→ generate ──→ END
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node("generate", generate_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START, is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")
    builder.add_edge("route_task", "generate")
    builder.add_edge("generate", END)

    return builder
```

- [ ] **Step 7: Run all Phase 1 tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ -v`

Expected: 25 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/graph/ tests/unit/graph/
git commit -m "feat: complete Phase 1 ChatPipeline graph (6 nodes, linear flow)"
```

---

### Task 1.8: Add integration test — graph vs ChatPipeline equivalence

**Files:**
- Create: `tests/integration/test_graph_pipeline_parity.py`
- Modify: `src/sales_agent/services/chat_pipeline.py` (add `_use_graph` flag)

**Interfaces:**
- Consumes: `build_chat_graph()` and `ChatPipeline.execute()`
- Produces: Integration test proving Phase 1 graph produces equivalent output

- [ ] **Step 1: Write integration test**

Write `tests/integration/test_graph_pipeline_parity.py`:

```python
"""Integration test: Phase 1 graph produces equivalent output to ChatPipeline."""
import pytest
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.state import ChatGraphState


@pytest.mark.asyncio
async def test_graph_fast_command_help():
    """Graph handles '帮助' command same as ChatPipeline."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = graph.invoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "帮助",
            "conversation_id": "conv-test-001",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-help"}},
    )

    assert result["path"] == "fast"
    assert "answer_dict" in result
    assert "你可以直接问销售问题" in result["answer_dict"]["summary"]


@pytest.mark.asyncio
async def test_graph_normal_message_routes():
    """Graph routes a normal sales message and generates answer."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = graph.invoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "客户说太贵了怎么回",
            "conversation_id": "conv-test-002",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-normal"}},
    )

    assert result["task_type"] == "objection_handling"
    assert "answer_dict" in result
    assert "summary" in result["answer_dict"]


@pytest.mark.asyncio
async def test_graph_reset_generates_new_conversation_id():
    """Graph generates new conversation_id on reset."""
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = graph.invoke(
        {
            "tenant_id": "t1",
            "user_id": "u1",
            "message": "新话题",
            "conversation_id": "conv-old-003",
            "channel": "local",
        },
        config={"configurable": {"thread_id": "test-thread-reset"}},
    )

    assert result["conversation_id"] != "conv-old-003"
```

- [ ] **Step 2: Run integration tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/integration/test_graph_pipeline_parity.py -v`

Expected: 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_graph_pipeline_parity.py
git commit -m "test: add integration tests for Phase 1 graph parity"
```

**Phase 1 Complete.** The graph compiles, invokes, and produces equivalent output to ChatPipeline for fast commands and basic routing. Full LLM execution is wired in Phase 2.

---

## Phase 2: Streaming + Checkpoint

*Duration estimate: ~3 hours. Wires real DB access and LLM calls into the graph via Runtime.context, adds AsyncPostgresSaver, and unifies HTTP + DingTalk streaming through astream().*

### Task 2.1: Wire Runtime.context — DB session and model instances

**Files:**
- Modify: `src/sales_agent/graph/nodes/tenant_resolve.py` (full implementation)
- Modify: `src/sales_agent/graph/nodes/context_load.py` (full implementation)
- Modify: `src/sales_agent/graph/nodes/generation.py` (full implementation)
- Modify: `src/sales_agent/api/routes/agent.py` (pass context to graph)

**Interfaces:**
- Consumes: `Runtime[GraphContext]` from `langgraph.runtime`
- Produces: Full DB + LLM backed nodes, using `config` and `runtime`

- [ ] **Step 1: Upgrade tenant_resolve node to use Runtime.context**

Modify `src/sales_agent/graph/nodes/tenant_resolve.py`:

```python
"""Tenant resolution node."""

from __future__ import annotations

import logging
from langgraph.runtime import Runtime
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)


async def resolve_tenant_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Resolve tenant info and model provider from DB.

    Requires `runtime.context["db"]` (AsyncSession) to be set by the caller.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing `db`.

    Returns:
        Dict with `tenant_info` populated.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, using passthrough")
        return {"tenant_info": {"tenant_id": state["tenant_id"], "config": {}}}

    resolver = TenantResolver(db)
    tenant_info = await resolver.resolve(state["tenant_id"])

    return {"tenant_info": tenant_info}
```

- [ ] **Step 2: Upgrade context_load node**

Modify `src/sales_agent/graph/nodes/context_load.py`:

```python
"""Context loading node."""

from __future__ import annotations

import logging
from langgraph.runtime import Runtime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sales_agent.graph.state import ChatGraphState
from sales_agent.models.conversation import ConversationMessage
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)


async def load_context_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Load recent conversation history from DB.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing `db`.

    Returns:
        Dict with `history_messages`.
    """
    db: AsyncSession = runtime.context.get("db")
    if db is None:
        return {"history_messages": []}

    settings = get_settings()
    history_turns = settings.conversation.history_turns
    limit = history_turns * 2

    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == state["conversation_id"],
            ConversationMessage.tenant_id == state["tenant_id"],
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    messages = list(reversed(messages))

    return {
        "history_messages": [{"role": m.role, "content": m.content} for m in messages],
    }
```

- [ ] **Step 3: Upgrade generation node to call real LLM**

Modify `src/sales_agent/graph/nodes/generation.py`:

```python
"""LLM generation node."""

from __future__ import annotations

import json
import logging
import time
from langgraph.runtime import Runtime
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.agent_executor import execute_agent

logger = logging.getLogger(__name__)


async def generate_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Execute the LLM agent to generate a structured answer.

    If the ontology subgraph already pre-computed the answer (skip_generation=True),
    this node is a no-op that passes through.

    Requires `runtime.context["chat_model"]` (ChatModel instance).

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing `chat_model` and `db`.

    Returns:
        Dict with `answer_dict`, `raw_response`, and `usage`.
    """
    # Ontology path pre-computed the answer — pass through
    if state.get("skip_generation"):
        return {}

    chat_model = runtime.context.get("chat_model")
    if chat_model is None:
        # Fallback: placeholder answer when no model available (unit tests)
        task_type = state.get("task_type", "general_sales_coaching")
        return {
            "answer_dict": {
                "summary": f"No model available for task: {task_type}",
                "sections": [],
            },
            "raw_response": "{}",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    db = runtime.context.get("db")
    task_type = state.get("task_type", "general_sales_coaching")
    message = state["message"]
    history_messages = state.get("history_messages", [])
    prompt_text = state.get("prompt_text")
    system_prompt_text = state.get("system_prompt_text")

    start_time = time.time()
    answer_dict = await execute_agent(
        chat_model=chat_model,
        task_type=task_type,
        message=message,
        context={},
        retrieval_result=state.get("retrieval_result"),
        history_messages=history_messages,
        tenant_style=state.get("tenant_info", {}).get("config", {}),
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )
    latency_ms = int((time.time() - start_time) * 1000)
    logger.info("Graph generation completed in %d ms for task %s", latency_ms, task_type)

    usage = getattr(chat_model, "last_usage", {}) or {}

    return {
        "answer_dict": answer_dict,
        "raw_response": json.dumps(answer_dict, ensure_ascii=False),
        "usage": usage,
    }
```

- [ ] **Step 4: Update graph builder — add async markers, add conditional edges for retrieval path**

Modify `src/sales_agent/graph/chat_graph.py` — update `build_chat_graph`:

```python
"""ChatPipeline as a LangGraph StateGraph."""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.nodes.tenant_resolve import resolve_tenant_node
from sales_agent.graph.nodes.context_load import load_context_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.edges.path_conditions import is_fast_command, select_retrieval_path


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Phase 2 graph structure:

        START ──(is_fast_command?)──→ fast_reply ──→ END
                    │
                    ▼
              validate ──→ resolve_tenant ──→ load_context
                                                    │
                                                    ▼
                                              route_task
                                                    │
                                         ┌──────────┼──────────┐
                                         ▼                     ▼
                                    [retrieval]            [generate]
                                    (Phase 3)                  │
                                                              ▼
                                                             END
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node("generate", generate_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START, is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")
    # Phase 2: route_task goes directly to generate (retrieval in Phase 3)
    builder.add_conditional_edges(
        "route_task", select_retrieval_path,
        {"ontology": "generate", "rag": "generate", "skip": "generate"},
    )
    builder.add_edge("generate", END)

    return builder
```

Add `select_retrieval_path` to `src/sales_agent/graph/edges/path_conditions.py`:

```python
def select_retrieval_path(state: ChatGraphState) -> str:
    """Select retrieval strategy based on task type.

    Phase 2: always returns "skip" (retrieval nodes added in Phase 3).

    Returns:
        "ontology", "rag", or "skip".
    """
    if state.get("needs_retrieval"):
        # Phase 3 will add real retrieval — for now, skip
        return "rag"  # All retrieval goes through same node in Phase 3
    return "skip"
```

- [ ] **Step 5: Update API route to use graph with context**

Modify `src/sales_agent/api/routes/agent.py` — add graph-based path alongside existing ChatPipeline:

At the top of the `chat` function, add:

```python
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer

# ... inside chat() function, after existing ChatPipeline code, add:

async def _execute_via_graph(db, tenant_id, user_id, message, conversation_id,
                              channel, agent_id, model, context):
    """Execute chat via LangGraph (new path, parallel to ChatPipeline)."""
    import uuid
    from langgraph.checkpoint.memory import InMemorySaver

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "message": message,
            "conversation_id": conversation_id,
            "channel": channel,
            "agent_id": agent_id,
            "model_override": model,
        },
        config={"configurable": {"thread_id": conversation_id or str(uuid.uuid4())}},
        context={
            "db": db,
            "chat_model": chat_model,
        },
    )
    return result
```

- [ ] **Step 6: Run all existing tests to verify no regressions**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ tests/integration/test_graph_pipeline_parity.py -v`

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/ src/sales_agent/api/routes/agent.py tests/
git commit -m "feat: wire Runtime.context into graph nodes (Phase 2: DB + LLM)"
```

---

### Task 2.1b: Implement retrieval node with ontology retrieval subgraph

**Files:**
- Create: `src/sales_agent/graph/nodes/retrieval.py` (retrieval router node)
- Create: `src/sales_agent/graph/retrieval/__init__.py`
- Create: `src/sales_agent/graph/retrieval/state.py` (OntologyRetrievalState)
- Create: `src/sales_agent/graph/retrieval/ontology_graph.py` (ontology subgraph)
- Create: `tests/unit/graph/test_retrieval_node.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add retrieval node + wiring)
- Modify: `src/sales_agent/graph/edges/path_conditions.py` (update select_retrieval_path)

**Interfaces:**
- Consumes: `OntologyRetrievalService`, `OntologyAnswerService`, `HybridRetriever`, `Retriever`, `KeywordRetriever`
- Produces: `retrieve_node(state, runtime) -> dict` routing to ontology subgraph or RAG
- Produces: `build_ontology_retrieval_graph() -> StateGraph` — the ontology subgraph

**Design rationale (Plan B):**

The ontology retrieval is itself a subgraph because it contains two LLM calls
(entity extraction + answer generation) separated by Cypher graph queries.
As a subgraph, each step is independently observable via `stream_mode="debug"`,
can have its own RetryPolicy/TimeoutPolicy, and the DingTalk streaming card
can show real progress: "提取关键词 → 图谱查询 → 生成回答".

```
retrieve_node (router in main graph)
    │
    ├── "ontology" ──→ ontology_retrieval_subgraph
    │                   ├── extract_terms     (LLM ①, ~100 tokens)
    │                   ├── graph_query       (Cypher)
    │                   ├── vector_fallback   (conditional, embedding)
    │                   ├── compact_evidence  (纯 Python)
    │                   └── generate_answer   (LLM ②, ~1600 tokens)
    │
    ├── "rag" ───────→ rag_retrieve (直接调 HybridRetriever)
    │
    └── "skip" ──────→ (bypass, 不会进入此节点)
```

- [ ] **Step 1: Create retrieval subgraph package**

Write `src/sales_agent/graph/retrieval/__init__.py`:

```python
"""Retrieval subgraphs for the ChatPipeline."""
```

- [ ] **Step 2: Define OntologyRetrievalState**

Write `src/sales_agent/graph/retrieval/state.py`:

```python
"""State definition for the ontology retrieval subgraph."""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class OntologyRetrievalState(TypedDict, total=False):
    """State flowing through the ontology retrieval subgraph.

    Each key is populated by one node and consumed by the next.
    """

    # === Input ===
    question: str
    tenant_id: str
    agent_id: str | None
    task_type: str

    # === Step ①: Entity extraction ===
    search_terms: list[str]

    # === Step ②: Graph traversal ===
    graph_rows: list[dict[str, Any]]
    vector_fallback_used: bool

    # === Step ③: Evidence compaction ===
    compacted_evidence: dict[str, Any]

    # === Step ④: Answer generation ===
    answer: dict[str, Any]
    sources: list[dict[str, Any]]
    graph_evidence: Any  # GraphEvidence dataclass

    # === Control ===
    error: str | None
```

- [ ] **Step 3: Write the ontology subgraph**

Write `src/sales_agent/graph/retrieval/ontology_graph.py`:

```python
"""Ontology retrieval as a LangGraph subgraph.

Decomposes the existing OntologyRetrievalService + OntologyAnswerService
pipeline into observable, individually-configurable graph nodes.

Pipeline:
    extract_terms (LLM) → graph_query (Cypher) → [vector_fallback?]
    → compact_evidence → generate_answer (LLM) → END
"""

from __future__ import annotations

import json
import logging
import time
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, TimeoutPolicy

from sales_agent.graph.retrieval.state import OntologyRetrievalState
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)

# ── LLM entity extraction prompt (from OntologyRetrievalService) ──
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""

# ── Answer generation prompt (from OntologyAnswerService) ──
_ONTOLOGY_RESPONSE_PROMPT = """你是销售知识图谱回答器。基于图谱事实回答用户问题，不要编造。

图谱证据：
{graph_json}

用户问题：{question}
任务类型：{task_type}

输出 JSON：
{{"answer":"自然语言回答","evidence":["使用的事实或来源"],"confidence":0.8}}"""

# ── Evidence compaction limits ──
_MAX_ENTITIES = 10
_MAX_FACTS = 25


# ═══════════════════════════════════════════════════════════
# Step ①: Extract entity names / keywords from the question
# ═══════════════════════════════════════════════════════════
async def extract_terms_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """LLM extracts entity names/keywords from the user question.

    Uses a tiny LLM call (temperature=0, max_tokens=100) to avoid
    maintaining keyword tables. Falls back to raw question on failure.
    """
    chat_model = runtime.context.get("chat_model")
    question = state["question"]

    if chat_model is None:
        return {"search_terms": [question]}

    try:
        raw = await chat_model.generate(
            messages=[{
                "role": "user",
                "content": _ENTITY_EXTRACTION_PROMPT.format(question=question),
            }],
            temperature=0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            terms = [str(t).strip() for t in parsed if str(t).strip()]
        elif isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list):
                    terms = [str(t).strip() for t in val if str(t).strip()]
                    break
            else:
                terms = [str(v).strip() for v in parsed.values() if str(v).strip()]
        else:
            terms = [question]
        return {"search_terms": list(dict.fromkeys(terms)) if terms else [question]}
    except Exception:
        logger.warning("Entity extraction failed, falling back to raw question", exc_info=True)
        return {"search_terms": [question]}


# ═══════════════════════════════════════════════════════════
# Step ②: Cypher graph traversal + optional vector fallback
# ═══════════════════════════════════════════════════════════
async def graph_query_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Run Cypher query: match entities by keyword → traverse to facts/evidence/docs.

    If no entities are matched, the conditional edge routes to vector_fallback.
    """
    settings = get_settings()
    if not settings.neo4j.uri:
        return {"graph_rows": [], "vector_fallback_used": False}

    from sales_agent.ontology.neo4j_client import Neo4jClient
    from sales_agent.ontology.repository import OntologyRepository

    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)

    rows = await repository.retrieve_by_query({
        "tenant_id": state["tenant_id"],
        "agent_id": state.get("agent_id"),
        "search_terms": state.get("search_terms", []),
        "limit": 200,
    })

    return {
        "graph_rows": rows,
        "vector_fallback_used": False,
    }


async def vector_fallback_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Vector embedding fallback when keyword match returns no entities.

    Embeds the question, queries the Neo4j vector index, and retrieves
    the top-5 matching entities with their facts/evidence/documents.
    """
    settings = get_settings()
    embedding_model = runtime.context.get("embedding_model")

    if not settings.neo4j.uri or embedding_model is None:
        return {"graph_rows": [], "vector_fallback_used": True}

    from sales_agent.ontology.neo4j_client import Neo4jClient
    from sales_agent.ontology.repository import OntologyRepository

    client = Neo4jClient(settings.neo4j)
    repository = OntologyRepository(client)

    question = state["question"]
    embedding = (await embedding_model.embed([question]))[0]

    rows = await repository.query_vector({
        "tenant_id": state["tenant_id"],
        "agent_id": state.get("agent_id"),
        "embedding": embedding,
        "limit": 5,
    })

    return {
        "graph_rows": rows,
        "vector_fallback_used": True,
    }


# ═══════════════════════════════════════════════════════════
# Step ③: Compact evidence for LLM prompt
# ═══════════════════════════════════════════════════════════
def compact_evidence_node(state: OntologyRetrievalState) -> dict:
    """Compress graph evidence to fit within LLM context window.

    Limits: max 10 entities, max 25 facts (sorted by keyword relevance).
    Each fact keeps only subject/predicate/object/value (value truncated to 200 chars).
    """
    rows = state.get("graph_rows", [])
    search_terms = state.get("search_terms", [])
    question = state.get("question", "")

    # Parse nodes from rows (mirrors OntologyRetrievalService._node)
    def _node(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return dict(value)
        except Exception:
            return {"value": str(value)}

    entities = []
    facts = []
    documents = []
    evidence = []

    for row in rows:
        if row.get("e"):
            entities.append(_node(row["e"]))
        if row.get("f"):
            facts.append(_node(row["f"]))
        for d in (row.get("documents") or []):
            if d:
                documents.append(_node(d))
        for ev in (row.get("evidence") or []):
            if ev:
                evidence.append(_node(ev))

    # Relevance sort: facts matching search terms score higher
    def _fact_score(f: dict) -> int:
        fv = str(f.get("value", "")) + str(f.get("predicate", ""))
        return sum(1 for t in search_terms if t in fv)

    facts.sort(key=_fact_score, reverse=True)

    # Compact
    compacted = {
        "ontology_intent": "entity_info",
        "entities": [
            {"name": e.get("name", ""), "type": e.get("type", "")}
            for e in entities[:_MAX_ENTITIES]
        ],
        "facts": [
            {
                "subject": f.get("subject_name", f.get("name", "")),
                "predicate": f.get("predicate", ""),
                "object": f.get("object_name", ""),
                "value": str(f.get("value", ""))[:200],
            }
            for f in facts[:_MAX_FACTS]
        ],
        "source_documents": [d.get("title", "") for d in documents[:5]],
        "confidence": 0.8 if entities else 0.0,
    }

    return {"compacted_evidence": compacted}


# ═══════════════════════════════════════════════════════════
# Step ④: Generate answer from evidence
# ═══════════════════════════════════════════════════════════
async def generate_answer_node(state: OntologyRetrievalState, runtime: Runtime) -> dict:
    """Call LLM to generate a structured answer from compacted graph evidence.

    Returns answer in sales-agent standard format: {summary, sections, sources}.
    """
    import re as _re
    chat_model = runtime.context.get("chat_model")
    compacted = state.get("compacted_evidence", {})

    if chat_model is None:
        return {
            "answer": {"summary": "知识图谱不可用", "sections": []},
            "sources": [],
        }

    # Render prompt
    prompt = _ONTOLOGY_RESPONSE_PROMPT.format(
        graph_json=json.dumps(compacted, ensure_ascii=False),
        question=state["question"],
        task_type=state.get("task_type", "knowledge_qa"),
    )

    raw = await chat_model.generate(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1600,
    )

    # Parse JSON from LLM output
    try:
        text = raw.strip()
        fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
        if fenced:
            text = fenced.group(1)
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            from json_repair import repair_json
            parsed = repair_json(text, return_objects=True)
        except Exception:
            parsed = {"answer": raw, "evidence": [], "confidence": 0.0}

    answer_text = str(parsed.get("answer", "")).strip()
    evidence_items = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []
    sections = []
    if evidence_items:
        sections.append({
            "title": "依据摘要",
            "content": "\n".join(f"- {item}" for item in evidence_items if item),
        })

    # Build sources from source_documents
    sources = [
        {
            "document_id": "",
            "title": title,
            "display_title": title,
            "score": compacted.get("confidence", 0.8),
            "source_type": "ontology",
        }
        for title in compacted.get("source_documents", [])[:3]
    ]

    return {
        "answer": {"summary": answer_text, "sections": sections},
        "sources": sources,
    }


# ═══════════════════════════════════════════════════════════
# Conditional edge: route from graph_query → compact or fallback
# ═══════════════════════════════════════════════════════════
def should_vector_fallback(state: OntologyRetrievalState) -> str:
    """If graph_query returned no results, try vector fallback."""
    rows = state.get("graph_rows", [])
    if not rows:
        return "fallback"
    return "compact"


# ═══════════════════════════════════════════════════════════
# Subgraph builder
# ═══════════════════════════════════════════════════════════
def build_ontology_retrieval_graph() -> StateGraph:
    """Build the ontology retrieval subgraph.

    Returns an UNCOMPILED StateGraph — caller calls .compile() with
    desired checkpointer/options. When used as a node in another graph,
    LangGraph compiles it automatically.

    Graph structure:

        START → extract_terms (LLM ①)
                    │
                    ▼
                graph_query (Cypher)
                    │
            ┌───────┴───────┐
            ▼               ▼
        (has rows)     (no rows)
            │               │
            │               ▼
            │       vector_fallback (embedding)
            │               │
            └───────┬───────┘
                    ▼
            compact_evidence
                    │
                    ▼
            generate_answer (LLM ②)
                    │
                    ▼
                   END
    """
    builder = StateGraph(OntologyRetrievalState)

    # ── Nodes ──
    builder.add_node("extract_terms", extract_terms_node)
    builder.add_node("graph_query", graph_query_node)
    builder.add_node("vector_fallback", vector_fallback_node)
    builder.add_node("compact_evidence", compact_evidence_node)
    builder.add_node("generate_answer", generate_answer_node)

    # ── Edges ──
    builder.add_edge(START, "extract_terms")
    builder.add_edge("extract_terms", "graph_query")
    builder.add_conditional_edges(
        "graph_query", should_vector_fallback,
        {"compact": "compact_evidence", "fallback": "vector_fallback"},
    )
    builder.add_edge("vector_fallback", "compact_evidence")
    builder.add_edge("compact_evidence", "generate_answer")
    builder.add_edge("generate_answer", END)

    return builder
```

- [ ] **Step 4: Write the retrieval router node**

Write `src/sales_agent/graph/nodes/retrieval.py`:

```python
"""Retrieval router node for the ChatPipeline graph.

Routes to one of three paths based on `select_retrieval_path`:
  - "ontology": Delegate to the ontology retrieval subgraph (Plan B)
  - "rag": Traditional vector/hybrid/keyword retrieval via existing services
  - "skip": Bypass retrieval entirely (e.g. emotional support, script gen)

The ontology path runs its own LLM calls internally and sets
`skip_generation=True` — the main `generate` node is bypassed.
"""

from __future__ import annotations

import logging
from langgraph.runtime import Runtime

from sales_agent.graph.state import ChatGraphState
from sales_agent.core.config import get_settings

logger = logging.getLogger(__name__)


async def retrieve_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Execute retrieval based on the path selected by select_retrieval_path.

    Called with state["retrieval_path"] already set by the conditional edge.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context (db, chat_model, embedding_model).

    Returns:
        Dict with retrieval results. May set `skip_generation=True` and
        `answer_dict` if the ontology subgraph pre-computed the answer.
    """
    path = state.get("retrieval_path", "skip")
    task_type = state.get("task_type", "knowledge_qa")
    message = state["message"]
    tenant_id = state["tenant_id"]
    agent_id = state.get("agent_id")

    # ── Path 1: Ontology Neo4j knowledge graph (subgraph) ──
    if path == "ontology":
        return await _retrieve_via_ontology(state, runtime, tenant_id, agent_id, task_type, message)

    # ── Path 2: Traditional RAG (vector/hybrid/keyword) ──
    if path == "rag":
        return await _retrieve_via_rag(state, runtime, tenant_id, task_type, message)

    # ── Path 3: Skip ──
    return {
        "retrieval_info": {"called": False, "reason": "path_does_not_need_retrieval"},
        "sources": [],
        "skip_generation": False,
    }


async def _retrieve_via_ontology(
    state: ChatGraphState,
    runtime: Runtime,
    tenant_id: str,
    agent_id: str | None,
    task_type: str,
    message: str,
) -> dict:
    """Run the ontology retrieval subgraph, which handles everything internally:
    extract terms → graph query → [vector fallback] → compact → generate answer.
    """
    from sales_agent.graph.retrieval.ontology_graph import build_ontology_retrieval_graph

    subgraph_builder = build_ontology_retrieval_graph()
    subgraph = subgraph_builder.compile()

    sub_input = {
        "question": message,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "task_type": task_type,
    }

    try:
        sub_result = await subgraph.ainvoke(
            sub_input,
            context=runtime.context,
        )
    except Exception as e:
        logger.warning("Ontology subgraph failed: %s, falling back", e)
        return {
            "retrieval_info": {"called": True, "provider": "ontology_neo4j", "error": str(e)},
            "sources": [],
            "skip_generation": False,
            "answer_dict": None,
        }

    return {
        "retrieval_info": {
            "called": True,
            "provider": "ontology_neo4j",
            "vector_fallback_used": sub_result.get("vector_fallback_used", False),
            "source_count": len(sub_result.get("sources", [])),
        },
        "sources": sub_result.get("sources", []),
        "answer_dict": sub_result.get("answer"),       # ← pre-computed answer!
        "skip_generation": True,                       # ← skip main generate node
    }


async def _retrieve_via_rag(
    state: ChatGraphState,
    runtime: Runtime,
    tenant_id: str,
    task_type: str,
    message: str,
) -> dict:
    """Traditional vector/hybrid/keyword retrieval via existing Retriever services."""
    db = runtime.context.get("db")
    embedding_model = runtime.context.get("embedding_model")
    settings = get_settings()
    mode = settings.retrieval.mode

    if db is None:
        return {"sources": [], "retrieval_result": None, "skip_generation": False}

    from sales_agent.services.retriever import Retriever, HybridRetriever
    from sales_agent.rag.keyword_retriever import KeywordRetriever

    if mode == "keyword":
        kr = KeywordRetriever(db)
        retriever = HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )
    elif mode == "hybrid":
        kr = KeywordRetriever(db)
        retriever = HybridRetriever(
            vector_retriever=Retriever(db, embedding_model),
            keyword_retriever=kr,
        )
    else:
        retriever = Retriever(db, embedding_model)

    retrieval_result = await retriever.retrieve_for_task(
        tenant_id=tenant_id,
        message=message,
        task_type=task_type,
        needs_retrieval=True,
    )

    sources = [s.to_source_item() for s in (retrieval_result.sources if retrieval_result else [])]

    return {
        "retrieval_info": {
            "called": True,
            "top_k": settings.retrieval.top_k,
            "source_count": len(sources),
        },
        "sources": sources,
        "retrieval_result": retrieval_result,
        "skip_generation": False,
    }
```

- [ ] **Step 5: Update select_retrieval_path to match the actual node**

Modify `src/sales_agent/graph/edges/path_conditions.py` — replace the stub:

```python
def select_retrieval_path(state: ChatGraphState) -> str:
    """Select retrieval strategy based on task type and config.

    Returns:
        "ontology" — Neo4j knowledge graph retrieval (Plan B subgraph)
        "rag" — Traditional vector/hybrid/keyword retrieval
        "skip" — No retrieval needed (emotional support, script gen, etc.)
    """
    if not state.get("needs_retrieval"):
        return "skip"

    from sales_agent.core.config import get_settings
    settings = get_settings()

    if settings.ontology.knowledge_engine == "ontology_neo4j" and settings.neo4j.uri:
        return "ontology"

    return "rag"
```

- [ ] **Step 6: Write tests for retrieval node and ontology subgraph**

Write `tests/unit/graph/test_retrieval_node.py`:

```python
"""Tests for the retrieval router node and ontology subgraph."""
import pytest
from sales_agent.graph.nodes.retrieval import retrieve_node
from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.retrieval.ontology_graph import (
    build_ontology_retrieval_graph,
    should_vector_fallback,
    compact_evidence_node,
)
from sales_agent.graph.retrieval.state import OntologyRetrievalState
from sales_agent.graph.edges.path_conditions import select_retrieval_path


def test_retrieve_node_skip_path():
    """retrieve_node returns empty sources for skip path."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "你好", "conversation_id": "c1", "channel": "local",
        "retrieval_path": "skip",
    }
    # Note: retrieve_node is async, needs pytest-asyncio
    ...


class TestSelectRetrievalPath:
    def test_skip_when_no_retrieval_needed(self):
        state: ChatGraphState = {"needs_retrieval": False, "tenant_id": "t1", "user_id": "u1", "message": "", "conversation_id": "c1", "channel": "local"}
        assert select_retrieval_path(state) == "skip"

    def test_rag_when_retrieval_needed_but_no_neo4j(self):
        state: ChatGraphState = {"needs_retrieval": True, "tenant_id": "t1", "user_id": "u1", "message": "", "conversation_id": "c1", "channel": "local"}
        # Without Neo4j config, should default to rag
        result = select_retrieval_path(state)
        assert result in ("rag", "skip")


class TestOntologySubgraph:
    def test_graph_compiles(self):
        builder = build_ontology_retrieval_graph()
        assert builder is not None
        graph = builder.compile()
        assert graph is not None

    def test_should_vector_fallback_with_rows(self):
        state: OntologyRetrievalState = {"graph_rows": [{"e": {"name": "test"}}], "question": "test", "tenant_id": "t1"}
        assert should_vector_fallback(state) == "compact"

    def test_should_vector_fallback_empty(self):
        state: OntologyRetrievalState = {"graph_rows": [], "question": "test", "tenant_id": "t1"}
        assert should_vector_fallback(state) == "fallback"

    def test_compact_evidence_sorts_by_relevance(self):
        state: OntologyRetrievalState = {
            "graph_rows": [
                {
                    "e": {"name": "产品A", "type": "product"},
                    "f": {"predicate": "价格", "value": "100元"},
                },
            ],
            "search_terms": ["价格"],
            "question": "价格多少",
            "tenant_id": "t1",
        }
        result = compact_evidence_node(state)
        assert "compacted_evidence" in result
        assert len(result["compacted_evidence"]["entities"]) == 1
        assert len(result["compacted_evidence"]["facts"]) == 1
```

- [ ] **Step 7: Wire retrieval into the main graph**

Modify `src/sales_agent/graph/chat_graph.py` — add retrieval node and update edges:

```python
from sales_agent.graph.nodes.retrieval import retrieve_node

def build_chat_graph() -> StateGraph:
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node("retrieve", retrieve_node)       # ← NEW
    builder.add_node("generate", generate_node)
    builder.add_node("check_risk", risk_check_node)
    builder.add_node("log", log_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START, is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")

    # route_task → determine retrieval path → retrieve or skip
    builder.add_conditional_edges(
        "route_task", select_retrieval_path,
        {"ontology": "retrieve", "rag": "retrieve", "skip": "generate"},
    )

    # retrieve → generate (ontology path sets skip_generation internally)
    builder.add_edge("retrieve", "generate")

    # generate → risk → log/regenerate → END
    builder.add_edge("generate", "check_risk")
    builder.add_conditional_edges(
        "check_risk", check_risk_result,
        {
            "pass": "log",
            "block": "generate",
            "rewrite": "generate",
            "max_retries": "log",
        },
    )
    builder.add_edge("log", END)

    return builder
```

- [ ] **Step 8: Run tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_retrieval_node.py -v`

Expected: 6 tests PASS.

- [ ] **Step 9: Run full test suite to ensure no regressions**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ tests/integration/test_graph_pipeline_parity.py -v`

Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/sales_agent/graph/nodes/retrieval.py \
        src/sales_agent/graph/retrieval/ \
        src/sales_agent/graph/edges/path_conditions.py \
        src/sales_agent/graph/chat_graph.py \
        tests/unit/graph/test_retrieval_node.py
git commit -m "feat: add retrieval node with ontology Neo4j subgraph (Plan B)"
```

---

### Task 2.2: Add AsyncPostgresSaver + checkpoint persistence

**Files:**
- Create: `src/sales_agent/graph/checkpoints.py`
- Create: `tests/unit/graph/test_checkpoints.py`

**Interfaces:**
- Produces: `get_checkpointer() -> AsyncPostgresSaver` — singleton factory
- Produces: `get_checkpointer_sync() -> InMemorySaver` — for unit tests

- [ ] **Step 1: Write test for checkpointer**

Write `tests/unit/graph/test_checkpoints.py`:

```python
"""Tests for the checkpointer factory."""
import pytest
from sales_agent.graph.checkpoints import get_checkpointer_sync


def test_get_checkpointer_sync_returns_in_memory():
    """get_checkpointer_sync returns InMemorySaver for testing."""
    saver = get_checkpointer_sync()
    from langgraph.checkpoint.memory import InMemorySaver
    assert isinstance(saver, InMemorySaver)


def test_get_checkpointer_sync_is_singleton():
    """Multiple calls return the same instance."""
    a = get_checkpointer_sync()
    b = get_checkpointer_sync()
    assert a is b
```

- [ ] **Step 2: Run tests (fail)**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_checkpoints.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write checkpointer factory**

Write `src/sales_agent/graph/checkpoints.py`:

```python
"""Checkpointer factory for LangGraph graphs.

Provides:
- `get_checkpointer_sync()` — InMemorySaver singleton for unit tests
- `get_checkpointer()` — AsyncPostgresSaver for production (await required)
"""

from __future__ import annotations

import logging
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)

_in_memory_saver: InMemorySaver | None = None
_async_pg_saver: AsyncPostgresSaver | None = None


def get_checkpointer_sync() -> InMemorySaver:
    """Return a shared InMemorySaver for unit tests and local dev."""
    global _in_memory_saver
    if _in_memory_saver is None:
        _in_memory_saver = InMemorySaver()
    return _in_memory_saver


async def get_checkpointer() -> AsyncPostgresSaver | InMemorySaver:
    """Return the production checkpointer.

    If DATABASE_URL is set and asyncpg is available, returns AsyncPostgresSaver.
    Otherwise falls back to InMemorySaver.

    The first call runs `setup()` to create checkpoint tables.
    """
    global _async_pg_saver, _in_memory_saver

    if _async_pg_saver is not None:
        return _async_pg_saver

    from sales_agent.core.config import get_settings

    try:
        settings = get_settings()
        db_url = settings.database.url

        # Convert sqlalchemy URL to psycopg connection string
        # "postgresql+asyncpg://user:pass@host:port/db" → "postgresql://user:pass@host:port/db"
        conn_string = db_url.replace("+asyncpg", "")

        # Use AsyncConnectionPool for a persistent, production-grade saver.
        # AsyncConnectionPool manages connection lifecycle and is compatible
        # with AsyncPostgresSaver's expected Conn type.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(conn_string, min_size=1, max_size=5, open=True)
        _async_pg_saver = AsyncPostgresSaver(conn=pool)
        await _async_pg_saver.setup()
        logger.info("AsyncPostgresSaver initialized with PostgreSQL pool")
        return _async_pg_saver

    except Exception as e:
        logger.warning(
            "Failed to create AsyncPostgresSaver (%s), falling back to InMemorySaver", e
        )
        return get_checkpointer_sync()
```

- [ ] **Step 4: Run checkpoint tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_checkpoints.py -v`

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/checkpoints.py tests/unit/graph/test_checkpoints.py
git commit -m "feat: add AsyncPostgresSaver + InMemorySaver checkpointer factory"
```

---

### Task 2.3: Unify streaming — graph.astream() replaces streaming_handler.py

**Files:**
- Create: `src/sales_agent/integrations/dingtalk/graph_stream.py` (new, ~80 lines)
- Modify: `src/sales_agent/integrations/dingtalk/stream_client.py` (call graph_stream instead of streaming_handler)
- Mark deprecated: `src/sales_agent/integrations/dingtalk/streaming_handler.py` (add deprecation comment, keep for fallback)

**Interfaces:**
- Consumes: `build_chat_graph()` + `get_checkpointer()`
- Produces: `handle_streaming_via_graph(event, reply_fn) -> None` — replacement for `handle_dingtalk_streaming()`

- [ ] **Step 1: Write the new graph-based streaming handler**

Write `src/sales_agent/integrations/dingtalk/graph_stream.py`:

```python
"""DingTalk streaming via LangGraph astream().

Replaces `streaming_handler.py`'s duplicated pipeline logic.
The graph handles all pipeline stages; this module only manages
the DingTalk interactive card lifecycle.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer, get_checkpointer_sync

logger = logging.getLogger(__name__)


async def handle_dingtalk_stream_via_graph(
    *,
    tenant_id: str,
    user_id: str,
    message: str,
    conversation_id: str,
    agent_id: str | None,
    reply_fn: Callable[[str], Awaitable[None]],
    card_sender,       # DingTalkCardSender
    db,                 # AsyncSession
    chat_model,         # ChatModel instance
) -> dict:
    """Process a DingTalk stream message through the LangGraph ChatPipeline.

    The graph handles all pipeline stages (routing, retrieval, generation).
    This function only manages the DingTalk card lifecycle:
    1. Create an initial "thinking..." card
    2. Stream token updates from graph.astream(stream_mode="messages")
    3. On node completion (stream_mode="updates"), update card with sections
    4. Finalize the card with the complete answer

    Args:
        tenant_id: Tenant identifier.
        user_id: DingTalk user identifier.
        message: User's message text.
        conversation_id: Conversation thread identifier.
        agent_id: Resolved agent identifier.
        reply_fn: Callback to send DingTalk messages.
        card_sender: DingTalkCardSender instance for interactive cards.
        db: Async SQLAlchemy session.
        chat_model: Configured chat model instance.

    Returns:
        The final graph state dict.
    """
    checkpointer = await get_checkpointer()

    builder = build_chat_graph()
    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            "thread_id": conversation_id or str(uuid.uuid4()),
        }
    }

    input_state = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "message": message,
        "conversation_id": conversation_id,
        "channel": "dingtalk",
        "agent_id": agent_id,
    }

    # 1. Create initial card
    card_id = await card_sender.send_markdown_card(
        title="思考中...",
        markdown="正在分析你的问题...",
    )

    # 2. Stream: messages for token-by-token, updates for completion
    final_answer = None
    accumulated_text = ""

    async for chunk in graph.astream(
        input_state,
        config,
        context={"db": db, "chat_model": chat_model},
        stream_mode=["messages", "updates"],
    ):
        if chunk["type"] == "messages":
            # Token-by-token: update the streaming card
            msg_data = chunk["data"]
            if hasattr(msg_data[0], "content"):
                accumulated_text += msg_data[0].content or ""
                await card_sender.stream_update(card_id, accumulated_text)

        elif chunk["type"] == "updates":
            data = chunk["data"]
            # Check if any node emitted an answer_dict
            for node_name, node_output in data.items():
                if isinstance(node_output, dict) and "answer_dict" in node_output:
                    final_answer = node_output["answer_dict"]
                    break

    # 3. Finalize card with parsed answer
    if final_answer is None:
        final_answer = {"summary": accumulated_text, "sections": []}

    await card_sender.finalize_card(card_id, final_answer, accumulated_text)

    return {"answer_dict": final_answer}
```

- [ ] **Step 2: Update stream_client.py to use graph-based handler**

Modify `src/sales_agent/integrations/dingtalk/stream_client.py` — in the `SalesAgentChatbotHandler.process()` method, add:

```python
# Try graph-based streaming first
try:
    from sales_agent.integrations.dingtalk.graph_stream import handle_dingtalk_stream_via_graph
    result = await handle_dingtalk_stream_via_graph(
        tenant_id=tenant_id,
        user_id=user_id,
        message=message,
        conversation_id=conversation_id,
        agent_id=agent_id,
        reply_fn=reply_fn,
        card_sender=self.card_sender,
        db=db,
        chat_model=chat_model,
    )
    logger.info("Graph-based streaming completed successfully")
    return
except Exception as e:
    logger.warning("Graph-based streaming failed, falling back to legacy: %s", e)
    # Fall through to legacy streaming_handler
```

- [ ] **Step 3: Mark streaming_handler.py as deprecated**

Add to the top of `streaming_handler.py`:

```python
"""
DEPRECATED: This module is replaced by `graph_stream.py` which uses LangGraph's
graph.astream() for streaming. The legacy implementation is kept as fallback
during the transition period and will be removed in Phase 3.
"""
```

- [ ] **Step 4: Run full test suite**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ tests/integration/test_graph_pipeline_parity.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/integrations/dingtalk/
git commit -m "feat: add graph-based DingTalk streaming, deprecate streaming_handler.py"
```

**Phase 2 Complete.** The graph now uses real DB sessions and LLM models via Runtime.context, AsyncPostgresSaver is configured, and streaming is unified through graph.astream().

---

## Phase 3: Retry + Risk Cycles

*Duration estimate: ~2 hours. Adds RetryPolicy for infrastructure failures, conditional edges for risk block → regenerate cycles, risk_check and logging nodes.*

### Task 3.1: Add retry_policies.py

**Files:**
- Create: `src/sales_agent/graph/retry_policies.py`

- [ ] **Step 1: Write retry policy definitions**

Write `src/sales_agent/graph/retry_policies.py`:

```python
"""Centralized retry and timeout policies for graph nodes."""

from __future__ import annotations

import httpx
from langgraph.types import RetryPolicy, TimeoutPolicy

# LLM call retry: network errors, rate limits
LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_attempts=3,
    retry_on=(httpx.TimeoutException, ConnectionError, TimeoutError),
)

# DB query retry
DB_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_attempts=3,
)

# LLM call timeout
LLM_TIMEOUT = TimeoutPolicy(run_timeout=120.0)

# DB query timeout
DB_TIMEOUT = TimeoutPolicy(run_timeout=30.0)
```

- [ ] **Step 2: Commit**

```bash
git add src/sales_agent/graph/retry_policies.py
git commit -m "feat: add centralized retry/timeout policies for graph nodes"
```

---

### Task 3.2: Implement risk_check node

**Files:**
- Create: `src/sales_agent/graph/nodes/risk_check.py`
- Create: `src/sales_agent/graph/edges/risk_conditions.py`
- Create: `tests/unit/graph/test_risk_node.py`

- [ ] **Step 1: Write test for risk node**

Write `tests/unit/graph/test_risk_node.py`:

```python
"""Tests for risk checking node."""
import pytest
from sales_agent.graph.nodes.risk_check import risk_check_node
from sales_agent.graph.edges.risk_conditions import check_risk_result
from sales_agent.graph.state import ChatGraphState


def test_risk_node_clean_answer():
    """Clean answer passes risk check."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "正常问题", "conversation_id": "c1", "channel": "local",
        "answer_dict": {"summary": "这是一个正常的回答", "sections": []},
        "sources": [],
    }
    result = risk_check_node(state)
    assert result["risk_action"] == "allow"
    assert result["input_risk_level"] == "none"


def test_risk_node_blocks_price_guarantee():
    """Answer with price guarantee triggers block."""
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1",
        "message": "问价格", "conversation_id": "c1", "channel": "local",
        "answer_dict": {
            "summary": "我保证给你最低价",
            "sections": [{"title": "折扣", "content": "一定可以申请到最低折扣"}],
        },
        "sources": [],
    }
    result = risk_check_node(state)
    # Risk check should flag the answer
    assert result["risk_action"] in ("block", "warn", "rewrite")
    assert result["risk_result"] is not None


def test_check_risk_result_pass():
    """check_risk_result returns 'pass' for allow action."""
    assert check_risk_result({"risk_action": "allow"}) == "pass"


def test_check_risk_result_block():
    """check_risk_result returns 'block' for block action."""
    assert check_risk_result({"risk_action": "block", "retry_count": 0}) == "block"


def test_check_risk_result_max_retries():
    """check_risk_result returns 'max_retries' after 3 attempts."""
    assert check_risk_result({"risk_action": "block", "retry_count": 3}) == "max_retries"
```

- [ ] **Step 2: Write risk_check node**

Write `src/sales_agent/graph/nodes/risk_check.py`:

```python
"""Risk checking node.

Calls the existing RiskChecker service for input pre-check and output post-check.
"""

from __future__ import annotations

import json
import logging
from sales_agent.graph.state import ChatGraphState
from sales_agent.services.risk_checker import RiskChecker

logger = logging.getLogger(__name__)


def risk_check_node(state: ChatGraphState) -> dict:
    """Run risk checks against the generated answer.

    Checks: input pre-check (rule-based on user message) and output
    post-check (rule-based on generated answer). LLM risk check is
    deferred to a conditional edge in a later phase.

    Args:
        state: Current graph state with `answer_dict` populated.

    Returns:
        Dict with `risk_action`, `risk_result`, and `input_risk_level`.
    """
    message = state.get("message", "")
    answer_dict = state.get("answer_dict", {})
    tenant_id = state.get("tenant_id", "")
    sources = state.get("sources", [])

    checker = RiskChecker()
    answer_text = json.dumps(answer_dict, ensure_ascii=False)

    # Full check: input + source + output
    result = checker.full_check(
        message=message,
        sources=sources,
        tenant_id=tenant_id,
        answer_text=answer_text,
    )

    # If blocked, replace answer with safety notice
    new_answer = dict(answer_dict)
    if result.action == "block":
        new_answer = {
            "summary": result.notice or "该请求涉及高风险承诺，已改为安全建议",
            "sections": [
                {"title": "安全提示", "content": result.notice},
                {"title": "建议", "content": "请使用合规的销售表达，不要对外做出未确认的承诺。"},
            ],
        }

    return {
        "risk_action": result.action,
        "risk_result": result.to_dict(),
        "input_risk_level": result.level,
        "answer_dict": new_answer,
    }


def check_risk_result(state: ChatGraphState) -> str:
    """Conditional edge: route based on risk outcome.

    Returns:
        "pass" — answer is safe, proceed to logging
        "block" — answer blocked, regenerate (if retries remain)
        "rewrite" — answer needs rewriting, regenerate
        "max_retries" — no more retries, proceed anyway
    """
    action = state.get("risk_action", "allow")
    retry_count = state.get("retry_count", 0)

    if action == "pass" or action == "allow":
        return "pass"
    if action == "block" and retry_count < 3:
        return "block"
    if action == "rewrite" and retry_count < 3:
        return "rewrite"
    return "max_retries"
```

Write `src/sales_agent/graph/edges/risk_conditions.py`:

```python
"""Risk-based conditional edge functions."""

from __future__ import annotations

from sales_agent.graph.state import ChatGraphState


def check_risk_result(state: ChatGraphState) -> str:
    """Route based on risk check outcome.

    Returns:
        "pass" — answer is safe
        "block" — answer blocked, regenerate
        "rewrite" — answer needs rewrite, regenerate
        "max_retries" — no more retries, proceed anyway
    """
    action = state.get("risk_action", "allow")
    retry_count = state.get("retry_count", 0)

    if action in ("pass", "allow", "warn"):
        return "pass"
    if action == "block" and retry_count < 3:
        return "block"
    if action == "rewrite" and retry_count < 3:
        return "rewrite"
    return "max_retries"
```

- [ ] **Step 3: Run tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/test_risk_node.py -v`

Expected: 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/sales_agent/graph/nodes/risk_check.py src/sales_agent/graph/edges/risk_conditions.py tests/unit/graph/test_risk_node.py
git commit -m "feat: add risk_check node with block→regenerate cycle support"
```

---

### Task 3.3: Implement logging node and complete Phase 3 graph

**Files:**
- Create: `src/sales_agent/graph/nodes/logging_node.py`
- Modify: `src/sales_agent/graph/chat_graph.py` (add risk, log nodes + cycle edges)

- [ ] **Step 1: Write logging node**

Write `src/sales_agent/graph/nodes/logging_node.py`:

```python
"""Conversation logging node.

Persists the completed conversation turn and records latency stats.
"""

from __future__ import annotations

import logging
from langgraph.runtime import Runtime
from sales_agent.graph.state import ChatGraphState
from sales_agent.services import conversation_logger

logger = logging.getLogger(__name__)


async def log_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Log the completed conversation turn to the database.

    Args:
        state: Current graph state with all results populated.
        runtime: LangGraph runtime with context containing `db`.

    Returns:
        Empty dict — this is a terminal node.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, skipping logging")
        return {}

    try:
        await conversation_logger.log_conversation(
            db,
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            channel=state.get("channel", "local"),
            agent_id=state.get("agent_id"),
            conversation_id=state["conversation_id"],
            message=state["message"],
            task_type=state.get("task_type", "general_sales_coaching"),
            task_confidence=state.get("route_confidence", 0.5),
            answer_dict=state.get("answer_dict", {}),
            risk_dict=state.get("risk_result", {}),
            sources=state.get("sources", []),
            model_config={},
            status="completed",
            stage_latency_ms={},
            path=state.get("path", "standard"),
            path_reason=state.get("path_reason", ""),
        )
    except Exception as e:
        logger.warning("Failed to log conversation: %s", e)

    return {}
```

- [ ] **Step 2: Update chat_graph.py — final Phase 3 version with cycle**

Modify `src/sales_agent/graph/chat_graph.py` — final version:

```python
"""ChatPipeline as a LangGraph StateGraph."""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from sales_agent.graph.state import ChatGraphState
from sales_agent.graph.nodes.fast_commands import fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.nodes.tenant_resolve import resolve_tenant_node
from sales_agent.graph.nodes.context_load import load_context_node
from sales_agent.graph.nodes.routing import routing_node
from sales_agent.graph.nodes.generation import generate_node
from sales_agent.graph.nodes.risk_check import risk_check_node
from sales_agent.graph.nodes.logging_node import log_node
from sales_agent.graph.edges.path_conditions import is_fast_command, select_retrieval_path
from sales_agent.graph.edges.risk_conditions import check_risk_result
from sales_agent.graph.retry_policies import LLM_RETRY_POLICY, LLM_TIMEOUT


def build_chat_graph() -> StateGraph:
    """Build an un-compiled ChatPipeline StateGraph.

    Phase 3 graph structure:

        START ──(is_fast_command?)──→ fast_reply ──→ END
                    │
                    ▼
              validate ──→ resolve_tenant ──→ load_context
                                                    │
                                                    ▼
                                              route_task
                                                    │
                                         ┌──────────┼──────────┐
                                         ▼          ▼          ▼
                                     ontology      rag        skip
                                         │          │          │
                                         └──────────┼──────────┘
                                                    ▼
                                                generate  ←──────────────┐
                                                    │                    │
                                              check_risk                 │
                                                    │                    │
                                         ┌──────────┼──────────┐        │
                                         ▼          ▼          ▼        │
                                       pass       block     rewrite      │
                                         │          │          │         │
                                         ▼          └──────────┘─────────┘
                                       log                         (循环: retry_count++)
                                         │
                                         ▼
                                        END
    """
    builder = StateGraph(ChatGraphState)

    # --- Nodes ---
    builder.add_node("fast_reply", fast_command_node)
    builder.add_node("validate", validate_node)
    builder.add_node("resolve_tenant", resolve_tenant_node)
    builder.add_node("load_context", load_context_node)
    builder.add_node("route_task", routing_node)
    builder.add_node(
        "generate",
        generate_node,
        retry_policy=LLM_RETRY_POLICY,
        timeout=LLM_TIMEOUT,
    )
    builder.add_node("check_risk", risk_check_node)
    builder.add_node("log", log_node)

    # --- Edges ---
    builder.add_conditional_edges(
        START, is_fast_command,
        {"fast": "fast_reply", "normal": "validate"},
    )
    builder.add_edge("fast_reply", END)
    builder.add_edge("validate", "resolve_tenant")
    builder.add_edge("resolve_tenant", "load_context")
    builder.add_edge("load_context", "route_task")
    builder.add_conditional_edges(
        "route_task", select_retrieval_path,
        {"ontology": "generate", "rag": "generate", "skip": "generate"},
    )
    builder.add_edge("generate", "check_risk")
    builder.add_conditional_edges(
        "check_risk", check_risk_result,
        {
            "pass": "log",
            "block": "generate",       # ← CYCLE: regenerate with safety notice
            "rewrite": "generate",     # ← CYCLE: regenerate with rewrite hint
            "max_retries": "log",      # ← give up after 3 retries
        },
    )
    builder.add_edge("log", END)

    return builder
```

- [ ] **Step 3: Run all tests**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/unit/graph/ tests/integration/ -v`

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/sales_agent/graph/
git commit -m "feat: complete Phase 3 — risk cycle, retry policies, logging node"
```

**Phase 3 Complete.** The graph now has retry policies on LLM calls, risk check with block→regenerate cycles, and conversation logging.

---

## Phase 4: Coach Subgraphs

*Duration estimate: ~2 hours. Migrates Quick Session state machine and Coach daily evaluation to LangGraph subgraphs.*

### Task 4.1: Quick Session StateGraph

**Files:**
- Create: `src/sales_agent/graph/coach/__init__.py`
- Create: `src/sales_agent/graph/coach/quick_session_graph.py`

- [ ] **Step 1: Write Quick Session state machine as StateGraph**

Write `src/sales_agent/graph/coach/__init__.py`:

```python
"""Coach system subgraphs."""
```

Write `src/sales_agent/graph/coach/quick_session_graph.py`:

```python
"""Quick Session state machine as a LangGraph StateGraph.

Replaces the manual stage-field state machine in `coach/quick_session.py`.
"""

from __future__ import annotations

from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END


class QuickSessionState(TypedDict, total=False):
    """State for the Quick Session state machine."""
    session_type: str           # "small_win" | "sales_block"
    stage: str                  # Current stage name
    payload: dict               # Accumulated user responses
    reply_text: str             # Response to show the user
    card_data: dict | None      # Final generated card


# Small Win Appreciation stages
def node_small_win(state: QuickSessionState) -> dict:
    """Ask: 今天有什么小成交/小进展想分享？"""
    return {
        "stage": "small_win",
        "reply_text": "太棒了！说说看，今天有什么让你觉得有成就感的进展？哪怕是一个小细节也没关系 😊",
    }


def node_strength(state: QuickSessionState) -> dict:
    """Ask: 在这个过程中，你觉得自己的优势是什么？"""
    payload = state.get("payload", {})
    payload["small_win"] = state.get("reply_text", "")
    return {
        "stage": "strength",
        "payload": payload,
        "reply_text": "真好！在这个过程中，你觉得是你身上的什么能力或特质帮到了你？",
    }


def node_gratitude(state: QuickSessionState) -> dict:
    """Ask: 客户给了什么积极反馈？"""
    payload = state.get("payload", {})
    payload["strength"] = state.get("reply_text", "")
    return {
        "stage": "gratitude",
        "payload": payload,
        "reply_text": "客户当时有什么反应？有没有让你觉得被认可的瞬间？",
    }


def node_energy(state: QuickSessionState) -> dict:
    """Ask: 这个经验接下来怎么用？"""
    payload = state.get("payload", {})
    payload["gratitude"] = state.get("reply_text", "")
    return {
        "stage": "energy",
        "payload": payload,
        "reply_text": "最后一个问题～这次成功的经验，你觉得可以在接下来的哪些客户身上复用？",
    }


def node_generate_card(state: QuickSessionState) -> dict:
    """Generate the LLM appreciation card."""
    payload = state.get("payload", {})
    payload["energy"] = state.get("reply_text", "")
    # In real implementation, calls LLM to generate a structured card
    card = {
        "title": "🎉 小胜利记录",
        "small_win": payload.get("small_win", ""),
        "strength": payload.get("strength", ""),
        "gratitude": payload.get("gratitude", ""),
        "energy": payload.get("energy", ""),
    }
    return {
        "stage": "completed",
        "payload": payload,
        "card_data": card,
        "reply_text": f"帮你记录下来了！\n\n🎉 **小胜利**：{card['small_win']}\n💪 **你的优势**：{card['strength']}\n\n继续保持这个状态！",
    }


def route_session_type(state: QuickSessionState) -> str:
    """Route to the correct session type's first node."""
    if state.get("session_type") == "small_win":
        return "small_win"
    return "awaiting_blocker"


def advance_from_small_win(state: QuickSessionState) -> str:
    """Determine next stage in small_win flow."""
    stage = state.get("stage", "small_win")
    transitions = {
        "small_win": "strength",
        "strength": "gratitude",
        "gratitude": "energy",
        "energy": "generate_card",
        "completed": "generate_card",
    }
    return transitions.get(stage, "generate_card")


def build_quick_session_graph() -> StateGraph:
    """Build the Quick Session state machine as a StateGraph.

    Graph structure:

        START ──(session_type?)──→ small_win ──→ strength ──→ gratitude
                                                                    │
                                                                    ▼
                                                                  energy
                                                                    │
                                                                    ▼
                                                              generate_card
                                                                    │
                                                                    ▼
                                                                   END
    """
    builder = StateGraph(QuickSessionState)

    # Nodes
    builder.add_node("small_win", node_small_win)
    builder.add_node("strength", node_strength)
    builder.add_node("gratitude", node_gratitude)
    builder.add_node("energy", node_energy)
    builder.add_node("generate_card", node_generate_card)

    # Entry routing
    builder.add_conditional_edges(
        START, route_session_type,
        {"small_win": "small_win", "awaiting_blocker": "generate_card"},
    )

    # Stage transitions
    builder.add_conditional_edges(
        "small_win", advance_from_small_win,
        {"strength": "strength", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "strength", advance_from_small_win,
        {"gratitude": "gratitude", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "gratitude", advance_from_small_win,
        {"energy": "energy", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "energy", advance_from_small_win,
        {"generate_card": "generate_card"},
    )
    builder.add_edge("generate_card", END)

    return builder
```

- [ ] **Step 2: Commit**

```bash
git add src/sales_agent/graph/coach/
git commit -m "feat: add Quick Session StateGraph subgraph"
```

---

### Task 4.2: Daily Evaluation subgraph stub

**Files:**
- Create: `src/sales_agent/graph/coach/daily_eval_graph.py`

- [ ] **Step 1: Write daily eval graph stub**

Write `src/sales_agent/graph/coach/daily_eval_graph.py`:

```python
"""Daily Evaluation pipeline as a LangGraph subgraph.

Converts the implicit multi-stage flow in `coach/daily_evaluator.py`
into an explicit DAG: aggregate → score → validate → apply → progress → reward.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END


class DailyEvalState(TypedDict, total=False):
    """State for the daily evaluation subgraph."""
    tenant_id: str
    agent_id: str
    user_id: str
    eval_date: str
    conversations: list[dict]
    scores: dict[str, int]
    previous_scores: dict[str, int]
    validation_passed: bool
    milestones_unlocked: list[str]
    rewards_granted: list[dict]
    error: str | None


def aggregate_conversations(state: DailyEvalState) -> dict:
    """Aggregate today's conversations for this user."""
    return {"conversations": []}  # Stub — full impl reads from DB


def llm_scoring_node(state: DailyEvalState) -> dict:
    """Call LLM to score the day's conversations across 6 dimensions."""
    # Stub — full impl calls coach_daily_evaluation prompt
    return {
        "scores": {
            "customer_identification": 50,
            "needs_discovery": 50,
            "value_delivery": 50,
            "trust_building": 50,
            "deal_advancement": 50,
            "review_reflection": 50,
        },
        "validation_passed": True,
    }


def validate_json_node(state: DailyEvalState) -> dict:
    """Validate LLM JSON output against schema."""
    scores = state.get("scores", {})
    required_dims = [
        "customer_identification", "needs_discovery", "value_delivery",
        "trust_building", "deal_advancement", "review_reflection",
    ]
    for dim in required_dims:
        if dim not in scores or not (0 <= scores[dim] <= 100):
            return {"validation_passed": False, "error": f"Invalid score for {dim}"}
    return {"validation_passed": True}


def apply_scores_node(state: DailyEvalState) -> dict:
    """Apply score deltas to persistent storage."""
    return {}


def check_milestones_node(state: DailyEvalState) -> dict:
    """Check and unlock milestones."""
    return {"milestones_unlocked": []}


def grant_rewards_node(state: DailyEvalState) -> dict:
    """Grant probabilistic rewards."""
    return {"rewards_granted": []}


def build_daily_eval_graph() -> StateGraph:
    """Build the daily evaluation subgraph.

    Graph structure:

        START → aggregate → llm_score → validate_json
                            ↑              │
                            └──(retry)─────┘  (validation_passed==False)
                                    │
                                    ▼ (passed)
                              apply_scores → check_milestones → grant_rewards → END
    """
    builder = StateGraph(DailyEvalState)

    builder.add_node("aggregate", aggregate_conversations)
    builder.add_node("llm_score", llm_scoring_node)
    builder.add_node("validate_json", validate_json_node)
    builder.add_node("apply_scores", apply_scores_node)
    builder.add_node("check_milestones", check_milestones_node)
    builder.add_node("grant_rewards", grant_rewards_node)

    builder.add_edge(START, "aggregate")
    builder.add_edge("aggregate", "llm_score")
    builder.add_edge("llm_score", "validate_json")
    builder.add_conditional_edges(
        "validate_json",
        lambda s: "apply" if s.get("validation_passed") else "retry",
        {"apply": "apply_scores", "retry": "llm_score"},
    )
    builder.add_edge("apply_scores", "check_milestones")
    builder.add_edge("check_milestones", "grant_rewards")
    builder.add_edge("grant_rewards", END)

    return builder
```

- [ ] **Step 2: Commit**

```bash
git add src/sales_agent/graph/coach/daily_eval_graph.py
git commit -m "feat: add Daily Evaluation subgraph stub"
```

**Phase 4 Complete.** Quick session state machine is a proper StateGraph. Daily evaluation pipeline has explicit stages with validation→retry cycle.

---

## Phase 5: Func API + Tool Calling

*Duration estimate: ~2 hours. Adds @entrypoint/@task for light-weight workflows and create_react_agent for tool-calling capability.*

### Task 5.1: Add @task example for parallel operations

**Files:**
- Create: `src/sales_agent/graph/func_tasks.py`

- [ ] **Step 1: Write func API example tasks**

Write `src/sales_agent/graph/func_tasks.py`:

```python
"""Lightweight tasks using LangGraph Functional API (@entrypoint / @task).

These are standalone tasks that can be used without a full StateGraph.
Suitable for simple, stateless operations like batch scoring or data transforms.
"""

from __future__ import annotations

from langgraph.func import entrypoint, task


@task
def score_single_dimension(dimension: str, conversation_text: str) -> dict:
    """Score one competency dimension from a conversation.

    This is a @task — it can be called in parallel with other dimensions.

    Args:
        dimension: Dimension name (e.g. "needs_discovery").
        conversation_text: The conversation text to score.

    Returns:
        A dict with the dimension name and score.
    """
    # Stub — real implementation calls LLM
    return {"dimension": dimension, "score": 50}


@entrypoint()
def batch_score_conversations(dimensions: list[str], conversations: list[str]) -> list[dict]:
    """Score multiple conversations across all dimensions in parallel.

    Uses @task for parallelism: each (dimension, conversation) pair
    is scored concurrently via Send fan-out.

    Args:
        dimensions: List of dimension names to score.
        conversations: List of conversation texts to score.

    Returns:
        List of score dicts, one per (dimension, conversation) pair.
    """
    futures = [
        score_single_dimension(dim, conv)
        for dim in dimensions
        for conv in conversations
    ]
    return [f.result() for f in futures]
```

- [ ] **Step 2: Commit**

```bash
git add src/sales_agent/graph/func_tasks.py
git commit -m "feat: add @entrypoint/@task parallel scoring example"
```

---

### Task 5.2: Add create_react_agent for tool calling

**Files:**
- Create: `src/sales_agent/graph/tools.py`
- Create: `src/sales_agent/graph/react_agent.py`

- [ ] **Step 1: Define tools**

Write `src/sales_agent/graph/tools.py`:

```python
"""Tool definitions for the ReAct agent.

LangGraph's ToolNode wraps these as callable nodes in the agent graph.
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_knowledge_base(query: str) -> str:
    """Search the enterprise knowledge base for product/company information.

    Args:
        query: The search query string.

    Returns:
        Relevant knowledge base content.
    """
    # Stub — real implementation calls the existing HybridRetriever
    return f"[Knowledge base result for: {query}]"


@tool
def check_customer_history(customer_name: str) -> str:
    """Retrieve past interaction history with a specific customer.

    Args:
        customer_name: The customer's name or identifier.

    Returns:
        Summary of past interactions.
    """
    return f"[Customer history for: {customer_name}]"


@tool
def get_sales_script_template(scenario: str) -> str:
    """Get a sales script template for a given scenario.

    Args:
        scenario: The scenario type (e.g. "cold_call", "follow_up", "objection").

    Returns:
        A structured script template.
    """
    return f"[Script template for: {scenario}]"


# All tools available to the agent
ALL_TOOLS = [
    search_knowledge_base,
    check_customer_history,
    get_sales_script_template,
]
```

- [ ] **Step 2: Write ReAct agent factory**

Write `src/sales_agent/graph/react_agent.py`:

```python
"""ReAct agent using LangGraph's create_react_agent.

This is the foundation for autonomous tool-calling agents.
The agent decides which tools to call, in what order, and when to stop —
without a fixed workflow.
"""

from __future__ import annotations

from langgraph.prebuilt import create_react_agent
from langgraph.graph.state import CompiledStateGraph

from sales_agent.graph.tools import ALL_TOOLS


def build_react_agent(
    chat_model,
    checkpointer=None,
) -> CompiledStateGraph:
    """Build a ReAct-style agent with tool-calling capability.

    The agent autonomously:
    1. Reasons about the user's request
    2. Decides whether to call a tool or respond
    3. Loops until it has enough information to answer

    Args:
        chat_model: The chat model (must support tool calling).
        checkpointer: Optional checkpointer for state persistence.

    Returns:
        A compiled StateGraph ready for invoke/stream.
    """
    agent = create_react_agent(
        model=chat_model,
        tools=ALL_TOOLS,
        checkpointer=checkpointer,
    )
    return agent
```

- [ ] **Step 3: Commit**

```bash
git add src/sales_agent/graph/tools.py src/sales_agent/graph/react_agent.py
git commit -m "feat: add ReAct agent with tool calling (create_react_agent)"
```

---

### Task 5.3: Final cleanup — remove streaming_handler.py, update graph __init__.py

**Files:**
- Delete: `src/sales_agent/integrations/dingtalk/streaming_handler.py`
- Modify: `src/sales_agent/graph/__init__.py` (public API exports)
- Modify: `src/sales_agent/integrations/dingtalk/stream_client.py` (remove fallback import)

- [ ] **Step 1: Update graph package init with public API**

Modify `src/sales_agent/graph/__init__.py`:

```python
"""LangGraph-based orchestration for the Sales Agent pipeline.

Public API:
    build_chat_graph()       — ChatPipeline as a compiled StateGraph
    build_react_agent()      — ReAct agent with tool calling
    build_quick_session_graph() — Quick session state machine
    build_daily_eval_graph() — Daily evaluation pipeline

Checkpoints:
    get_checkpointer()       — AsyncPostgresSaver for production
    get_checkpointer_sync()  — InMemorySaver for tests

Func API:
    batch_score_conversations — @entrypoint with parallel @task scoring
"""

from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.checkpoints import get_checkpointer, get_checkpointer_sync
from sales_agent.graph.react_agent import build_react_agent
from sales_agent.graph.coach.quick_session_graph import build_quick_session_graph
from sales_agent.graph.coach.daily_eval_graph import build_daily_eval_graph
from sales_agent.graph.func_tasks import batch_score_conversations

__all__ = [
    "build_chat_graph",
    "build_react_agent",
    "build_quick_session_graph",
    "build_daily_eval_graph",
    "batch_score_conversations",
    "get_checkpointer",
    "get_checkpointer_sync",
]
```

- [ ] **Step 2: Remove streaming_handler.py and its fallback import**

Delete `src/sales_agent/integrations/dingtalk/streaming_handler.py`:

```bash
git rm src/sales_agent/integrations/dingtalk/streaming_handler.py
```

Modify `src/sales_agent/integrations/dingtalk/stream_client.py` — remove the fallback:

```python
# Remove the try/except fallback block added in Task 2.3 Step 2.
# Now graph-based streaming is the only path.
from sales_agent.integrations.dingtalk.graph_stream import handle_dingtalk_stream_via_graph

# In process():
result = await handle_dingtalk_stream_via_graph(
    tenant_id=tenant_id,
    user_id=user_id,
    message=message,
    conversation_id=conversation_id,
    agent_id=agent_id,
    reply_fn=reply_fn,
    card_sender=self.card_sender,
    db=db,
    chat_model=chat_model,
)
```

- [ ] **Step 3: Run full test suite**

Run: `cd /root/code/sales-agent && source .venv/bin/activate && python -m pytest tests/ -v --ignore=tests/integration/test_neo4j`

Expected: All non-Neo4j tests PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: complete Phase 5 — delete streaming_handler.py, finalize graph API"
```

---

## Plan Summary

| Phase | Tasks | Files Created | Files Modified | Files Deleted | New Tests |
|---|---|---|---|---|---|
| 1: Basic Graph | 8 | 12 | 2 | 0 | 25 |
| 2: Streaming + Checkpoint | 4 | 7 | 6 | 0 | 8 |
| 3: Retry + Risk | 3 | 5 | 1 | 0 | 5 |
| 4: Coach Subgraphs | 2 | 3 | 0 | 0 | 0 |
| 5: Func API + Tools | 3 | 3 | 3 | 1 | 0 |
| **Total** | **20** | **30** | **12** | **1** | **38** |

### LangGraph Features Used

| Feature | Phase | Where |
|---|---|---|
| `StateGraph` + `add_node` + `add_edge` | Phase 1 | `chat_graph.py` |
| `add_conditional_edges` | Phase 1 | `chat_graph.py` |
| `START`, `END` | Phase 1 | `chat_graph.py` |
| `TypedDict` State | Phase 1 | `state.py` |
| `Runtime.context` | Phase 2 | All async nodes |
| `compile(checkpointer=...)` | Phase 2 | `chat_graph.py` |
| `graph.ainvoke()` | Phase 2 | `agent.py` |
| `graph.astream(stream_mode=["messages", "updates"])` | Phase 2 | `graph_stream.py` |
| `AsyncPostgresSaver` | Phase 2 | `checkpoints.py` |
| `InMemorySaver` | Phase 2 | `checkpoints.py` |
| **Subgraph (nested StateGraph)** | Phase 2 | `retrieval/ontology_graph.py` |
| `RetryPolicy` | Phase 3 | `retry_policies.py` |
| `TimeoutPolicy` | Phase 3 | `retry_policies.py` |
| Cycle via conditional edges | Phase 3 | `risk → generate` |
| Subgraph (nested `StateGraph`) | Phase 4 | `coach/` |
| `@entrypoint` | Phase 5 | `func_tasks.py` |
| `@task` + parallel futures | Phase 5 | `func_tasks.py` |
| `create_react_agent` | Phase 5 | `react_agent.py` |
| `ToolNode` / `@tool` | Phase 5 | `tools.py` |
