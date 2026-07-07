# Research: Router Prompt Resolve Callers (层次 2 改造前置摸底)

- **Query**: 让 context_resolver / clarification_resolver / evidence_router 三个 router prompt 从「硬编码常量」改为运行时走 `PromptRegistry.resolve_prompt`（三级回退），需摸清调用方全貌与改法约束
- **Scope**: internal
- **Date**: 2026-07-07

---

## TL;DR（关键约束）

1. **生产路径只经 graph 两个节点**调用这三个 service：`context_resolution_node`（调 `resolve_context` + `resolve_clarification`）、`evidence_routing_node`（调 `route_intent_evidence`）。**ChatPipeline 不调用任何一个** → 给 service 加 `db/tenant_id/agent_id` 必填参数**不会**破坏 eval（lessons #31 警告的 ChatPipeline 老路径不走这里，可放心改）。
2. **runtime context 里只有 `db` / `chat_model` / `embedding_model` / `now`**（`online_conversation.py:228-233`），**没有** `tenant_id` / `agent_id` — 这两个在 graph **state** 里。`context_resolution_node` 已从 `state.get("tenant_id"/"agent_id")` 取；`evidence_routing_node` 目前**根本没取** tenant_id/agent_id（要新加）。
3. **BUILTIN_PROMPTS 已注册 `("router", "evidence_router")`**（`prompt_defaults.py:83`，占位符 `()` 即无 `.format`），但**没注册** `("router", "context_resolver")` 和 `("router", "clarification_resolver")` — 改造时必须补两条 `BuiltinPrompt` 行，否则 `resolve_prompt` 第三级回退落空、抛 `ValueError`。
4. **三个 prompt 模板都不含 `.format` 占位符**（无 `{message}` 之类），且 `required_placeholders` 应为 `()` → 调用方拿到模板后**不需要**再 `.format`，**避开 lessons #30 的字面花括号陷阱**（但仍需保证 prompt 文本里的 JSON 示例 `{...}` 不被任何路径当占位符处理；只要不调 `.format` 就安全）。
5. **resolve_clarification 的 9 个单测全部用位置参数 `message` + `chat_model=None`**，加必填 `db` 会全部炸 → 走「可选参数 + 内部 try/except 兜底」范式（参照 `risk_check_node` / `resolve_quick_session_prompts`）。
6. **runtime context 已是「节点入参」**，加 `db` 不破坏 ChatPipeline（其不构造 runtime context 调这三个 fn），但**会破坏所有 service 级单测**（30 个 call site）— 改造方案需让新参数可选 + 解析失败兜底回常量。

---

## Findings

### 1. 三个 service 函数的所有调用方

| Service 函数 | 定义位置 | 调用方 | 调用方类型 | 调用方位置 |
|---|---|---|---|---|
| `resolve_context` | `src/sales_agent/services/context_resolver.py:98` | `context_resolution_node` | graph 节点（生产） | `src/sales_agent/graph/nodes/context_resolution.py:280-285` |
| `resolve_context` | 同上 | （测试 import）`test_context_resolver` | 单测 | `tests/unit/test_context_resolver.py:12`（11 处 `await resolve_context(...)`） |
| `resolve_context` | 同上 | （注入覆盖入口）`context_resolver_override` | runtime ctx 可覆盖 hook | `context_resolution.py:195-197` |
| `resolve_clarification` | `src/sales_agent/services/topic_manager.py:409` | `context_resolution_node`（经 `_resolve_pending_turn` 转发） | graph 节点（生产） | `context_resolution.py:95-99`（在 `_resolve_pending_turn`，调用 `clar_resolver_fn(message, chat_model, attempt_count=...)` 位置参数）；`clar_resolver_fn` 绑定见 `:198-200` |
| `resolve_clarification` | 同上 | （测试 import）`test_topic_manager` | 单测 | `tests/unit/test_topic_manager.py:16`（9 处 `await resolve_clarification(...)`，**全部** `chat_model=None, attempt_count=0`） |
| `resolve_clarification` | 同上 | （注入覆盖入口）`clarification_resolver_override` | runtime ctx 可覆盖 hook | `context_resolution.py:198-200` |
| `route_intent_evidence` | `src/sales_agent/services/evidence_router.py:95` | `evidence_routing_node` | graph 节点（生产） | `src/sales_agent/graph/nodes/evidence_routing.py:71-74` |
| `route_intent_evidence` | 同上 | （测试 import）`test_evidence_router` | 单测 | `tests/unit/test_evidence_router.py:9`（10 处 `await route_intent_evidence(...)`） |
| `route_intent_evidence` | 同上 | （注入覆盖入口）`evidence_router_override` | runtime ctx 可覆盖 hook | `evidence_routing.py:60-62` |

**重要交叉验证**：
- `grep "resolve_context\|resolve_clarification\|route_intent_evidence" src/sales_agent/services/chat_pipeline.py` → **零命中**。ChatPipeline 走的是 `ChatGraph` 老路径，**完全不调用**这三个函数。lessons #31 担心的 eval/ChatPipeline 路径不涉及这三个 service。
- `grep ... src/sales_agent/cli.py` → 零命中。
- `grep ... tests/integration/` → 零命中。仅 `tests/unit/` 三个文件命中。

---

### 2. graph 节点如何从 runtime context 取 db / tenant_id / agent_id

#### runtime context 的构造（唯一注入点）

`src/sales_agent/services/online_conversation.py:225-234`（`invoke_online_turn`）：

```python
return await graph.ainvoke(
    input_state,
    config={"configurable": {"thread_id": thread_id}},
    context={
        "db": db,
        "chat_model": resolved_chat_model,
        "embedding_model": resolved_embedding_model,
        "now": now,
    },
)
```

**关键**：`tenant_id` / `agent_id` 不在 runtime context，而是放在 `input_state`（`online_conversation.py:210-222`）：

```python
input_state: dict[str, Any] = {
    "tenant_id": tenant_id,
    "agent_id": agent.id,   # 注意是 resolve 后的 agent.id
    ...
}
```

#### runtime context 的两种 unpack 范式（共存）

| 范式 | 代表节点 | 取法 |
|---|---|---|
| A. 从 `config: RunnableConfig` 取（旧式） | `context_resolution_node`、`evidence_routing_node`、`online_graph._unpack_context` | `config["configurable"]["__pregel_runtime"].context` — 经 `_unpack_context(config)` helper |
| B. 从 `runtime: Runtime` 直接取（新式，langgraph 1.x+） | `generate_node`、`risk_check_node`、`tenant_resolve_node`、`retrieval_node` | `runtime.context.get("db")` — 直接方法参数 |

`generate_node` 范式（`src/sales_agent/graph/nodes/generation.py:25,58,76`）：

```python
async def generate_node(state: ChatGraphState, runtime: Runtime) -> dict:
    chat_model = runtime.context.get("chat_model")  # :58
    db = runtime.context.get("db")                  # :76
    agent_id = state.get("agent_id")                # :77  ← 注意从 state 取
    tenant_id = state["tenant_id"]                  # :78  ← 从 state 取
```

`risk_check_node` 同款范式（`risk_check.py:47,89-90,100-101`）：db 从 `runtime.context`，agent_id/tenant_id 从 `state`，然后调 `resolve_risk_prompt(db, tenant_id, agent_id)`。

#### `context_resolution_node` 当前的 unpack（`context_resolution.py:185-211`）

```python
ctx = _unpack_context(config)                            # :185
db = ctx.get("db") if ctx else None                     # :186
chat_model = ctx.get("chat_model") if ctx else None     # :187
now = ctx.get("now") or datetime.now(timezone.utc)      # :188

manager: TopicManager = ctx.get("topic_manager") ...     # :191-194
context_resolver_fn = ctx.get("context_resolver_override") or resolve_context  # :195-197
clar_resolver_fn = ctx.get("clarification_resolver_override") or resolve_clarification  # :198-200

scope = {
    "tenant_id": state.get("tenant_id", ""),             # :206  ← state 取
    "agent_id": state.get("agent_id", ""),               # :207  ← state 取
    ...
}
```

调用点：
- `resolve_context`：`context_resolution.py:280-285`（keyword args）
- `resolve_clarification`：经 `_resolve_pending_turn`（`:60-70`），内部 `context_resolution.py:95-99` 用位置参数：

```python
decision = await clar_resolver_fn(
    message,
    chat_model,
    attempt_count=topic.clarification_attempts,
)
```

#### `evidence_routing_node` 当前的 unpack（`evidence_routing.py:54-74`）

```python
if state.get("context_status") != "resolved":
    return {}                                            # :55-56
ctx = _unpack_context(config)                            # :58
chat_model = ctx.get("chat_model") if ctx else None      # :59
evidence_router_fn = ctx.get("evidence_router_override") or route_intent_evidence  # :60-62
standalone_query = state.get("standalone_query", "")     # :64

decision = await evidence_router_fn(                     # :71-74
    standalone_query=standalone_query,
    chat_model=chat_model,
)
```

**关键 gap**：`evidence_routing_node` 当前**完全没取** `tenant_id` / `agent_id` / `db`。改造时必须新加 3 行类似：

```python
db = ctx.get("db") if ctx else None
tenant_id = state.get("tenant_id", "")
agent_id = state.get("agent_id")
```

参照 `risk_check_node` 的范式（同样从 ctx 取 db + state 取 ids）。

---

### 3. ChatPipeline 是否调用 → 决定加 db 必填是否破坏 eval

**结论：不调用，零命中。**

证据（`grep -rn "resolve_context\|resolve_clarification\|route_intent_evidence" src/sales_agent/services/chat_pipeline.py` → 空）。

ChatPipeline 调的是 `resolve_execution_prompts`（`chat_pipeline.py:484-486`，task/system 类）和 `resolve_risk_prompt`（`:867-869`），不经 router 类的这三个 service。

**含义**：
- 给三个 service 加 `db`/`tenant_id`/`agent_id` 参数**不会**让 eval 的 ChatPipeline 路径炸（lessons #31 的 ChatPipeline 老路径完全不经过这里）。
- 但会让 30 个单测全炸 → 必须让新参数**可选**（默认 None），内部 `if db is not None and tenant_id:` 才走 registry，否则回退到模块常量（保留旧路径）。这正是 `risk_check_node:100-101` + `resolve_quick_session_prompts:65-69` 的范式。

---

### 4. 测试如何调（决定单测改法）

| 测试文件 | 调用次数 | 调用范式（典型） | 是否传 db/tenant_id/agent_id |
|---|---|---|---|
| `tests/unit/test_context_resolver.py` | 11 处 | `await resolve_context(message=..., topic=..., recent_messages=[...], chat_model=StubModel(...))` | **否**（纯 keyword，StubModel 不调真 LLM） |
| `tests/unit/test_topic_manager.py` | 9 处 | `await resolve_clarification("继续，...", chat_model=None, attempt_count=0)` | **否**（位置 message + chat_model=None，多数命中 `_CONTINUE_COMMANDS`/`_NEW_COMMAND_PREFIXES`/`_CANCEL_COMMANDS` 短路、不调 LLM） |
| `tests/unit/test_evidence_router.py` | 10 处 | `await route_intent_evidence(standalone_query="...", chat_model=_FakeChatModel(...))` | **否**（纯 keyword） |

StubModel 示例（`test_context_resolver.py:21-36`）：固定返回 JSON，不连真 LLM，不连 DB。

**改法约束**：
- 30 个 call site 全部不传 db/tenant_id/agent_id → 新参数必须**默认 None**且 service 内部 `try/except` 兜底回常量，否则破坏向后兼容。
- 单测若改 service 内默认走 registry（仅当 db/tenant_id/agent_id 都给齐），所有现有单测自动走旧常量路径，**无需改动**即可继续通过。

---

### 5. `PromptRegistry.resolve_prompt` 精确签名与回退实现

**位置**：`src/sales_agent/services/prompt_registry.py:124-164`

**签名**（:124-130）：

```python
async def resolve_prompt(
    self,
    category: str,        # "task"|"system"|"router"|"risk"|"coach"|"web"
    key: str,             # 该 category 下的具体标识，如 "evidence_router"
    tenant_id: str,
    agent_id: str | None = None,
) -> str:                 # 返回 template_text（str）
```

**三级回退实现**（:135-164）：

1. **Agent prompt_set 映射**（:135-142，仅当 `agent_id` 非空）：`_resolve_agent_prompt_version` → 查 `Agent.prompt_set_id` → `AgentPromptSet.task_prompt_versions_json`（兼容新旧 schema）→ 取 `version_id` → 查 `PromptVersion`。返回 `mapped.template_text`。
2. **Tenant active 版本**（:144-157）：`SELECT PromptVersion WHERE tenant_id=? AND prompt_category=? AND COALESCE(prompt_key, task_type)=? AND status='active' LIMIT 1`。返回 `version.template_text`。
3. **内置默认**（:159-162）：`get_builtin(category, key)` → 返回 `BuiltinPrompt.template`。

**未注册 (category,key) 会怎样**（:159-164）：

```python
builtin = get_builtin(category, key)
if builtin is not None:
    return builtin.template
raise ValueError(f"No prompt found for {category}/{key}")
```

→ **抛 `ValueError`**（不回退、不返回 None）。调用方必须 try/except 自己兜底（参照 `prompt_resolver_helper.resolve_quick_session_prompts:65-69` 的 `try/except: pass` 范式）。

**对当前改造的含义**：`("router", "evidence_router")` 已注册，`resolve_prompt("router", "evidence_router", ...)` 第三级能落底；但 `("router", "context_resolver")` 和 `("router", "clarification_resolver")` **未注册**，必须在 `prompt_defaults.py:_system_router_risk_entries()` 里补两条 `BuiltinPrompt`，否则 tenant 未配 DB 版本时直接抛错。

---

### 6. system 消息是否含 `.format` 占位符（决定是否还要再 `.format`）

| 文件 | 构造行 | 模板常量 | 含 `{placeholder}`? | required_placeholders 应为 |
|---|---|---|---|---|
| `context_resolver.py:92-95` | `{"role":"system","content": CONTEXT_RESOLVER_PROMPT}` | `CONTEXT_RESOLVER_PROMPT`（`prompts/context_resolver_prompt.py:6-49`） | 否（仅 JSON 示例字面 `{...}`，非占位符） | `()` |
| `topic_manager.py:461-464` | `{"role":"system","content": CLARIFICATION_RESOLVER_PROMPT}` | `CLARIFICATION_RESOLVER_PROMPT`（`prompts/clarification_resolver_prompt.py:8-27`） | 否 | `()` |
| `evidence_router.py:125-128` | `{"role":"system","content": EVIDENCE_ROUTER_PROMPT}` | `EVIDENCE_ROUTER_PROMPT`（`prompts/evidence_router_prompt.py:6-82`） | 否 | `()`（已注册，见 `prompt_defaults.py:83`，确认为 `()`） |

**含义**：
- 三个模板**都不需要再 `.format`**。`resolve_prompt` 返回的字符串直接当 system content 即可。
- **避开了 lessons #30 的字面花括号陷阱**：因为不调 `.format`，模板里的 JSON 示例 `{...}` 不会被当未知占位符 → 不会抛 KeyError。
- **但 DB 版 prompt 由运营编辑后可能含字面 `{xxx}`**：只要调用方一律不调 `.format`，DB 版即使含字面花括号也安全（直接发给 LLM）。**禁止**未来有人为这三个 prompt 加 `{message}` 占位符 + `.format`，否则立即触发 #30 陷阱。

---

### 7. `resolve_clarification` 完整签名 & CLARIFICATION_RESOLVER_PROMPT 使用位置

**签名**（`topic_manager.py:409-413`）：

```python
async def resolve_clarification(
    message: str,              # 位置参数（不是 keyword-only）
    chat_model: Any,
    attempt_count: int = 0,
) -> ClarificationDecision:
```

注意：参数**不是 keyword-only**（函数签名前无 `*,`）。当前调用方 `context_resolution.py:95-99` 用位置传 `message` + keyword 传 `chat_model`/`attempt_count`。

**短路逻辑**（不调 LLM，`:434-456`）：
- `attempt_count >= MAX_CLARIFICATION_ATTEMPTS(=2)` → 直接返回 `resolution="new"`（:434-435）
- `_CONTINUE_COMMANDS`（`继续`/`接着刚才`）→ `continue`（:439-446）
- `_NEW_COMMAND_PREFIXES`（`新问题`/`换个话题`）→ `new`（:449-451）
- `_CANCEL_COMMANDS`（`取消`/`算了`）→ `cancel`（:454-456）

**LLM 调用段**（:459-475）：

```python
if chat_model is not None:
    try:
        messages = [
            {"role": "system", "content": CLARIFICATION_RESOLVER_PROMPT},   # :462
            {"role": "user", "content": trimmed},                           # :463
        ]
        response = await chat_model.generate(messages=..., temperature=0.0, max_tokens=500)
        return parse_model_json(response, ClarificationDecision)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("resolve_clarification parse failure: %s", exc, ...)
# Default fallback
return ClarificationDecision(resolution="new", confidence=0.5)  # :477
```

**CLARIFICATION_RESOLVER_PROMPT 的 import 与使用**：
- import：`topic_manager.py:28-30` — `from sales_agent.prompts.clarification_resolver_prompt import CLARIFICATION_RESOLVER_PROMPT`
- 唯一使用点：`topic_manager.py:462`（即上面 system 消息 content）

**改造要点**：
- 改造点就是把 `:462` 的 `CLARIFICATION_RESOLVER_PROMPT` 替换为 `await reg.resolve_prompt("router", "clarification_resolver", tenant_id, agent_id)`（或先解析到本地变量、解析失败回退常量）。
- 加 `db`/`tenant_id`/`agent_id` 参数后，**短路逻辑（434-456）在这些命令命中时不调 LLM 也不需要 prompt**，所以解析 prompt 的代码应放在 LLM 段（:459 之前），不要无脑前置（否则白做 DB 往返）。

---

## 改造路径建议（仅描述事实存在的范式，非推荐）

为完整起见，仓库里已存在两套可照搬的范式：

1. **「helper 函数集中解析」范式**（`prompt_resolver_helper.py`）：为 router 类新增 `resolve_router_prompts(db, tenant_id, agent_id) -> dict` 或三个独立函数，集中调 `PromptRegistry`，失败返回 `{}` / `None`。调用方（service）拿到 None 时回退常量。
2. **「调用点就地 try/except」范式**（`risk_check_node:96-110`、`generation_node:85-95`）：节点内 `if db is not None and tenant_id: try: ... prompt = await reg.resolve_prompt(...) except: pass`。

无论哪种，`prompt_defaults.py` 都必须先补两条 `BuiltinPrompt` 注册（`("router","context_resolver")` 和 `("router","clarification_resolver")`），否则第三级回退抛 `ValueError`。

## Caveats / Not Found

- 未追踪的潜在入口：guided_flow / coach 子系统不调用这三个 service（grep 未命中），但若未来 coach/guided_flow 自己也要用 router 类 prompt，需另查。
- `context_resolution_node` 的注入覆盖 hook（`context_resolver_override` / `clarification_resolver_override`）若被某些 e2e 测试用来注入 mock，加参数时需同步更新 mock 签名（grep 测试目录未发现使用这些 override 的测试，但不能完全排除 e2e fixture 在外层）。
- `topic_routing_enabled=False` 时（`online_graph.py:122-124`），`flow_action` 走 `direct_chat`，根本不进 `context_resolution` / `evidence_routing` 节点 — 这条路径下三个 service 不会被触发。
