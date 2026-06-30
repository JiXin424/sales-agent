# LangGraph Integration Design Spec

**Date**: 2026-06-30
**Status**: Approved
**Scope**: sales-agent project — integrate LangGraph framework across all orchestration layers

---

## 1. Motivation

### 1.1 Current Pain Points

| Pain Point | File(s) | Severity |
|---|---|---|
| 894-line monolithic `execute()` with nested if/elif tower | `chat_pipeline.py` | High |
| Two parallel pipeline implementations (HTTP + DingTalk Stream) | `chat_pipeline.py` + `streaming_handler.py` (542 lines) | High |
| Hand-rolled state machines with manual stage fields | `quick_session.py` | Medium |
| Implicit multi-stage flow hidden in sequential method calls | `daily_evaluator.py` | Medium |
| No retry/loop for blocked answers (just replaces answer) | `chat_pipeline.py` | Medium |
| "Button mode" vs "one-sentence mode" logic buried in prompt text | `visit_preparation.py`, `post_visit_review.py` | Low |
| No human-in-the-loop capability for high-risk responses | — | Gap |
| No tool-calling / agent loop pattern | — | Gap |

### 1.2 Goals

1. Replace `ChatPipeline.execute()` with LangGraph `StateGraph`, one graph serving both HTTP (`ainvoke`) and DingTalk Stream (`astream`)
2. Eliminate `streaming_handler.py`'s duplicated pipeline logic (542 → 0 lines)
3. Migrate Quick Session state machines to StateGraph with checkpoint persistence
4. Migrate Coach daily evaluation to subgraph with explicit stage dependencies
5. Add retry loops, risk block → regenerate cycles, and human-in-the-loop hooks
6. Introduce tool-calling capability via `create_react_agent` for future Agent features
7. Use `AsyncPostgresSaver` for checkpoint persistence, reusing existing PostgreSQL
8. Teach the developer how to use each LangGraph feature through hands-on migration

---

## 2. Architecture Overview

### 2.1 Post-Migration Request Flow

```
                            HTTP / DingTalk
                                 │
                      ┌──────────┴──────────┐
                      ▼                     ▼
                graph.ainvoke()      graph.astream()
                      │                     │
                      └──────────┬──────────┘
                                 │
                ┌────────────────┴────────────────┐
                │     CompiledStateGraph           │
                │                                  │
                │  ┌────────────────────────────┐  │
                │  │  ChatPipelineGraph          │  │
                │  │  ~8 nodes + conditional     │  │
                │  │  edges + retry/cycle         │  │
                │  └────────────────────────────┘  │
                │                                  │
                │  ┌────────────────────────────┐  │
                │  │  Coach Subgraph             │  │
                │  │  (daily eval, quick session) │  │
                │  └────────────────────────────┘  │
                │                                  │
                │  ┌────────────────────────────┐  │
                │  │  AsyncPostgresSaver          │  │
                │  │  (auto-checkpoint per node)  │  │
                │  └────────────────────────────┘  │
                └──────────────────────────────────┘
```

### 2.2 Key Principle

**The graph is the single source of truth for orchestration logic.** HTTP and streaming callers differ only in how they consume the output (`ainvoke` vs `astream`), not in the pipeline itself.

---

## 3. Directory Structure

```
src/sales_agent/graph/
├── __init__.py                 # Re-exports: build_chat_graph, build_coach_graph
├── state.py                    # ChatGraphState TypedDict
├── chat_graph.py               # ChatPipeline StateGraph builder
├── nodes/
│   ├── __init__.py
│   ├── validation.py           # Request validation node
│   ├── tenant_resolve.py       # Tenant + model resolution node
│   ├── context_load.py         # Conversation history loader
│   ├── routing.py              # Task routing (rules + LLM fallback)
│   ├── retrieval.py            # RAG / Ontology Neo4j retrieval
│   ├── generation.py           # LLM agent execution
│   ├── risk_check.py           # Risk checking (rules + LLM)
│   ├── logging_node.py         # Conversation logging + latency stats
│   ├── coach_observe.py        # Realtime coach observation
│   └── fast_commands.py        # Help/reset fast path handler
├── edges/
│   ├── __init__.py
│   ├── path_conditions.py      # Conditional edge functions (path router logic)
│   └── risk_conditions.py      # Risk-based conditional routing
├── coach/
│   ├── __init__.py
│   ├── daily_eval_graph.py     # Daily evaluation subgraph
│   └── quick_session_graph.py  # Quick session state machine subgraph
├── retry_policies.py           # Centralized retry policy definitions
├── checkpoints.py              # AsyncPostgresSaver factory
└── context.py                  # Runtime context schema (DB session, model instances)
```

### 3.1 Migration Path for Existing Files

| Old File | Disposition |
|---|---|
| `services/chat_pipeline.py` | Keep, mark deprecated, delegate to graph internally during transition |
| `services/agent_executor.py` | Keep — nodes call it unchanged |
| `services/task_router.py` | Keep — nodes call it unchanged |
| `services/risk_checker.py` | Keep — nodes call it unchanged |
| `services/retriever.py` | Keep — nodes call it unchanged |
| `services/path_router.py` | Logic moves to `edges/path_conditions.py`; old file kept for reference |
| `integrations/dingtalk/streaming_handler.py` | **Delete after migration** |
| `coach/quick_session.py` | Migrate to `graph/coach/quick_session_graph.py` |
| `coach/daily_evaluator.py` | Migrate to `graph/coach/daily_eval_graph.py` |

---

## 4. GraphState Definition

### 4.1 `ChatGraphState`

```python
# src/sales_agent/graph/state.py

from typing import Annotated, Any, TypedDict
from operator import add


class ChatGraphState(TypedDict, total=False):
    # === Input ===
    tenant_id: str
    user_id: str
    message: str
    conversation_id: str
    channel: str                           # "local" | "dingtalk"
    agent_id: str | None
    model_override: str | None

    # === Intermediate ===
    tenant_info: dict[str, Any]
    history_messages: list[dict[str, str]]
    task_type: str
    route_confidence: float
    needs_retrieval: bool
    prompt_text: str | None
    system_prompt_text: str | None

    # === Path Decision ===
    path: str                              # "fast" | "standard" | "slow"
    path_reason: str

    # === Retrieval ===
    retrieval_result: Any
    sources: list[dict]
    skip_generation: bool                  # True when ontology pre-computed answer

    # === Coach ===
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
    needs_clarification: bool

    # === Streaming ===
    stream_tokens: Annotated[list[str], add]

    # === Output ===
    final_answer: dict[str, Any]
    final_sources: list[dict]
    run_id: str | None
    usage: dict[str, int]
```

### 4.2 Runtime Context (non-serialized)

The following are passed via LangGraph's `Runtime.context`, NOT through State (they are non-serializable):

```python
# src/sales_agent/graph/context.py

class GraphContext(TypedDict):
    db: AsyncSession           # DB session
    chat_model: ChatModel      # LLM instance
    embedding_model: Any       # Embedding model instance
    reply_fn: Callable | None  # DingTalk reply callback
```

---

## 5. ChatPipeline Graph Structure

### 5.1 Node List

| Node | Responsibility | Calls Existing |
|---|---|---|
| `validate` | Request validation | `request_validator.validate_chat_request()` |
| `resolve_tenant` | Tenant + model resolution | `TenantResolver` |
| `load_context` | Load conversation history | `_load_recent_history()` |
| `route_task` | Task type routing (rules→LLM) | `task_router.route_task()` |
| `resolve_prompts` | Prompt resolution (3-tier) | `PromptRegistry` |
| `coach_observe` | Realtime coach observation | `coach_observe.observe()` |
| `retrieve` | RAG / Ontology retrieval | `HybridRetriever` / `OntologyAnswerService` |
| `generate` | LLM agent execution | `agent_executor.execute_agent()` |
| `check_risk` | Risk checking (rules + LLM) | `RiskChecker` |
| `log` | Conversation logging + stats | `conversation_logger` |
| `fast_reply` | Help/reset commands | (inline) |

### 5.2 Graph Edges

```
START
  │
  ├──(is_fast_command?)──→ fast_reply ──→ END
  │
  ▼
validate ──→ resolve_tenant ──→ load_context ──→ route_task
                                                      │
                                                      ▼
                                               resolve_prompts
                                                      │
                                                      ▼
                                                coach_observe
                                                      │
                                           ┌──────────┼──────────┐
                                           ▼          ▼          ▼
                                       ontology     rag        skip
                                           │          │          │
                                           └──────────┼──────────┘
                                                      ▼
                                                  generate  ←─────────────────┐
                                                      │                      │
                                           ┌──────────┼──────────┐          │
                                           ▼          ▼          ▼          │
                                         allow       warn      rewrite    block
                                           │          │          │          │
                                           ▼          ▼          ▼          │
                                          log        log    generate ───────┘
                                           │          │
                                           └──────────┘
                                                      │
                                                      ▼
                                                     END
```

### 5.3 Conditional Edge Functions

```python
# edges/path_conditions.py

def is_fast_command(state: ChatGraphState) -> str:
    """Check if message is a help/reset command → route to fast_reply."""
    msg = state["message"].strip()
    if msg in HELP_COMMANDS | RESET_COMMANDS:
        return "fast"
    return "normal"

def select_retrieval_path(state: ChatGraphState) -> str:
    """Select retrieval strategy based on settings and task type."""
    settings = get_settings()
    if state.get("needs_retrieval") and settings.ontology.knowledge_engine == "ontology_neo4j":
        return "ontology"
    if state.get("needs_retrieval"):
        return "rag"
    return "skip"

def select_path(state: ChatGraphState) -> str:
    """Replicate PathRouter logic for path selection."""
    # Mirror of PathRouter.select_path() logic
    ...

def check_risk_result(state: ChatGraphState) -> str:
    """Route based on risk check outcome."""
    action = state.get("risk_action", "allow")
    if action == "block":
        if state.get("retry_count", 0) < 3:
            return "block"      # triggers regenerate loop
        return "max_retries"    # give up after 3 tries
    if action == "rewrite":
        return "rewrite"        # triggers regenerate with safe context
    return "pass"
```

### 5.4 Retry & Timeout Configuration

```python
# retry_policies.py

LLM_RETRY = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_attempts=3,
    retry_on=(httpx.TimeoutException, ConnectionError, RateLimitError),
)

LLM_TIMEOUT = TimeoutPolicy(run_timeout=120.0)

# Applied per-node in graph builder:
builder.add_node("generate", generate_node, retry_policy=LLM_RETRY, timeout=LLM_TIMEOUT)
builder.add_node("route_task", route_task_node, retry_policy=LLM_RETRY)
```

---

## 6. Streaming Unification

### 6.1 Before vs After

```
BEFORE (two code paths):
  POST /agent/chat
    → ChatPipeline.execute() → execute_agent() → parse JSON → return

  DingTalk Stream message
    → streaming_handler.py (542 lines, PARALLEL IMPLEMENTATION)
    → stream_execute_agent() → StreamUpdateThrottle → card updates → finalize

AFTER (one graph, two consumption modes):
  POST /agent/chat
    → graph.ainvoke(input)
    → result.value["answer_dict"] → return JSON

  DingTalk Stream message
    → async for chunk in graph.astream(input, stream_mode="messages"):
          card.stream_update(chunk)
    → async for event in graph.astream(input, stream_mode="updates"):
          if "answer_dict" in event:
              card.finalize(answer_dict)
```

### 6.2 Streaming in API Routes

```python
# api/routes/agent.py — HTTP endpoint (unchanged interface)
@router.post("/agent/chat")
async def chat(request: ChatRequest):
    graph = get_chat_graph()  # singleton compiled graph
    result = await graph.ainvoke(
        {
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "message": request.message,
            ...
        },
        config={"configurable": {"thread_id": request.conversation_id}},
        context={"db": db, "chat_model": chat_model, ...},
    )
    return build_chat_response(result.value)

# integrations/dingtalk/streaming_handler.py — streaming (simplified from 542 lines)
async def handle_dingtalk_streaming(event):
    graph = get_chat_graph()
    card = await card_sender.create_card()

    async for chunk in graph.astream(
        input_data,
        config=config,
        context=context,
        stream_mode=["messages", "updates"],
    ):
        if chunk["type"] == "messages":
            card.stream_update(chunk["data"])
        elif chunk["type"] == "updates":
            if "answer_dict" in chunk["data"]:
                card.finalize(chunk["data"]["answer_dict"])
```

### 6.3 `streaming_handler.py` Retirement

After migration, `streaming_handler.py` is **deleted**. The ~542 lines of duplicated pipeline logic are gone. Only card-management logic remains (~50 lines in the DingTalk handler, calling the shared graph).

---

## 7. Coach Subgraphs

### 7.1 Quick Session State Machine

```
Current (quick_session.py): Manual stage string + if/elif dispatch

After: StateGraph with explicit state transitions

  small_win ──→ strength ──→ gratitude ──→ energy ──→ card ──→ completed
                                            │
  awaiting_blocker ──→ awaiting_split ──→ awaiting_possibilities ──→ card
```

```python
# graph/coach/quick_session_graph.py

class QuickSessionState(TypedDict):
    session_type: str       # "small_win" | "sales_block"
    stage: str
    payload: dict[str, Any]
    reply_text: str

def build_quick_session_graph() -> CompiledStateGraph:
    builder = StateGraph(QuickSessionState)

    builder.add_node("small_win", handle_small_win)
    builder.add_node("strength", handle_strength)
    builder.add_node("gratitude", handle_gratitude)
    builder.add_node("energy", handle_energy)
    builder.add_node("generate_card", generate_llm_card)
    builder.add_node("awaiting_blocker", handle_awaiting_blocker)
    builder.add_node("awaiting_split", handle_awaiting_split)
    builder.add_node("awaiting_possibilities", handle_awaiting_possibilities)

    # Edges with transition guard functions
    builder.add_conditional_edges(
        "small_win", advance_from_small_win,
        {"strength": "strength", "completed": "generate_card"}
    )
    # ...

    return builder.compile(checkpointer=checkpointer)
```

### 7.2 Daily Evaluation Pipeline

```python
# graph/coach/daily_eval_graph.py

def build_daily_eval_graph() -> CompiledStateGraph:
    """Explicit DAG for: aggregate → score → validate → apply → progress → reward"""
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
        {"apply": "apply_scores", "retry": "llm_score"}
    )
    builder.add_edge("apply_scores", "check_milestones")
    builder.add_edge("check_milestones", "grant_rewards")
    builder.add_edge("grant_rewards", END)

    return builder.compile(checkpointer=checkpointer)
```

---

## 8. AsyncPostgresSaver Integration

### 8.1 Factory

```python
# src/sales_agent/graph/checkpoints.py

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_global_saver: AsyncPostgresSaver | None = None

async def get_checkpointer() -> AsyncPostgresSaver:
    global _global_saver
    if _global_saver is None:
        from sales_agent.core.config import get_settings
        settings = get_settings()

        conn_string = (
            f"postgresql://{settings.db.user}:{settings.db.password}"
            f"@{settings.db.host}:{settings.db.port}/{settings.db.database}"
        )
        _global_saver = AsyncPostgresSaver.from_conn_string(conn_string)
        await _global_saver.setup()
    return _global_saver
```

### 8.2 Impact on Existing DB Code

- `AsyncPostgresSaver` creates its own tables (`langgraph_checkpoints`, `langgraph_checkpoint_writes`, `langgraph_checkpoint_blobs`) — **no conflict** with existing tables
- Existing `conversation_logger` and `RunTracer` can be **gradually simplified** — the checkpoint already records every node's input/output and timing
- Conversation history can be reconstructed from checkpoints instead of `ConversationMessage` table (long-term, not immediate)

---

## 9. Future Capabilities Enabled

### 9.1 Human-in-the-Loop (interrupt/Command)

```python
def generate_node(state, config):
    answer = llm.generate(...)
    if state["input_risk_level"] == "high":
        approval = interrupt({"type": "approval", "answer": answer})
        if not approval.get("approved"):
            return {"answer_dict": safe_version(answer)}
    return {"answer_dict": answer}

# Caller:
graph.stream(Command(resume={"approved": True}), config=config)
```

### 9.2 Tool Calling (create_react_agent)

```python
from langgraph.prebuilt import create_react_agent

tools = [search_knowledge_base, check_inventory, create_order]
agent = create_react_agent(llm, tools, checkpointer=saver)
# Agent autonomously decides which tools to call, in what order, how many times
```

### 9.3 Parallel Fan-out (Send)

```python
def fan_out_dimensions(state):
    return [Send("score_dim", {"dim": d}) for d in SIX_DIMENSIONS]
# All 6 dimensions scored in parallel, results merged
```

---

## 10. Five-Phase Implementation Plan (Learning Path)

| Phase | What | LangGraph Features Learned | Files Affected |
|---|---|---|---|
| **1** | ChatPipeline graph (first 5 steps) | StateGraph, nodes, edges, compile, invoke | New: `graph/` (initial); Old: `chat_pipeline.py` (parallel) |
| **2** | Streaming + Checkpoint | astream, stream_mode, AsyncPostgresSaver, checkpoint | `streaming_handler.py` (deleted); `agent.py`, DingTalk handler |
| **3** | Retry + Risk cycles | RetryPolicy, TimeoutPolicy, conditional edges with loops | `graph/nodes/generation.py`, `retry_policies.py` |
| **4** | Coach subgraphs | Subgraph, Send fan-out, interrupt/Command | `coach/quick_session.py` → `graph/coach/` |
| **5** | Func API + Tool calling | @entrypoint, @task, create_react_agent, ToolNode | New: agent tools |

---

## 11. Risk Mitigation

| Risk | Mitigation |
|---|---|
| Graph performance overhead | Each node is lightweight (calls existing services); LangGraph overhead < 5ms per node |
| Checkpoint storage growth | Configure TTL-based cleanup; checkpoints are append-only but compactible |
| Existing callers break | ChatPipeline.execute() preserved as wrapper, gradually switches to graph internally |
| Learning curve | 5-phase plan, each phase self-contained and testable, teaches one concept at a time |

---

## 12. Success Criteria

1. `POST /agent/chat` returns identical responses (same answer, same sources, same risk checks)
2. DingTalk HTTP and Stream modes work without regression
3. `streaming_handler.py` deleted; pipeline logic exists in exactly one place
4. All existing tests pass; new graph-node unit tests added
5. Quick session state machine runs on StateGraph with checkpoint persistence
6. Risk block → regenerate cycle works end-to-end (LLM generates safe alternative)
7. Coach daily evaluation runs as subgraph with explicit stages
8. Each phase has a learning summary document explaining what was used and why
