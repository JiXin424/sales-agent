# Unified Guided Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将访前准备、访后复盘、小赢欣赏、卡点破框统一为可抢占、逐轮推进、重启及跨天清空的在线引导流程，同时保持普通 Chat、Ontology Retrieval 和定时 Daily Evaluation 的职责边界。

**Architecture:** 新增 Online Conversation Graph 作为 HTTP 与钉钉在线消息的统一入口；它按“新触发、退出、活跃流程、普通 Chat”的优先级，将引导消息交给参数化 Guided Flow 子图，将其他消息交给现有 Chat Graph。引导状态使用进程级 `InMemorySaver`，thread ID 按租户、Agent、渠道、用户和上海自然日隔离；Daily Evaluation 继续走现役 service/scheduler，本计划只隐藏其未完成占位图。

**Tech Stack:** Python 3.10+、FastAPI、LangGraph 1.2+、Pydantic 2、SQLAlchemy asyncio、pytest、pytest-asyncio、DingTalk Stream/HTTP。

---

## Scope and file map

### New files

- `src/sales_agent/graph/guided_flow/__init__.py` — 引导流程公共 API。
- `src/sales_agent/graph/guided_flow/types.py` — Flow 定义、启动结果、推进结果和运行依赖类型。
- `src/sales_agent/graph/guided_flow/state.py` — Guided Flow 子图状态。
- `src/sales_agent/graph/guided_flow/triggers.py` — action/明确短语/退出指令的确定性解析。
- `src/sales_agent/graph/guided_flow/definitions.py` — 四个 Flow Definition 注册表。
- `src/sales_agent/graph/guided_flow/handlers/coach_flows.py` — 从现役实现提取的小赢、卡点纯业务逻辑。
- `src/sales_agent/graph/guided_flow/handlers/visit_flows.py` — 访前、访后逐轮逻辑与出卡降级。
- `src/sales_agent/graph/guided_flow/nodes.py` — 初始化、推进、退出、日志节点。
- `src/sales_agent/graph/guided_flow/graph.py` — 通用 Guided Flow StateGraph builder。
- `src/sales_agent/graph/online_state.py` — 在线根图的窄状态模型。
- `src/sales_agent/graph/online_graph.py` — 在线根图 builder 与 Chat 子图适配节点。
- `src/sales_agent/services/online_conversation.py` — Agent 解析、thread ID、单例编译和 invoke/stream 门面。
- `src/sales_agent/integrations/dingtalk/agent_resolver.py` — 钉钉渠道绑定 Agent 的公共解析器。
- `tests/unit/graph/guided_flow/` — trigger、definition、handler、graph 单元测试。
- `tests/unit/graph/test_online_graph.py` — 根图路由、抢占、跨天、重启和去重测试。
- `tests/integration/test_online_guided_flows.py` — HTTP/钉钉共用入口集成测试。

### Modified files

- `src/sales_agent/core/config.py`、`config/default.yaml`、`.env.example` — 引导流程开关和时区。
- `src/sales_agent/api/routes/agent.py` — 改用在线统一门面。
- `src/sales_agent/integrations/dingtalk/processor.py` — 非流式消息改用在线统一门面。
- `src/sales_agent/integrations/dingtalk/graph_stream.py` — 流式消息改用 Online Graph。
- `src/sales_agent/integrations/dingtalk/quick_entry.py` — 四种 action 统一启动 Guided Flow。
- `src/sales_agent/graph/nodes/retrieval.py` — 缓存 Ontology 子图，停止逐请求编译。
- `src/sales_agent/graph/chat_graph.py`、`src/sales_agent/api/routes/graph_debug.py` — 独立调试注册表并隐藏 Daily Eval 占位图。
- `tests/unit/coach/test_quick_session.py` — 测试迁移到新的纯 handler。

### Removed after cutover

- `src/sales_agent/coach/quick_session.py` — 不再承担 DB 会话或业务逻辑。
- `src/sales_agent/graph/coach/quick_session_graph.py` — 未接入且不能逐轮暂停的占位图。
- `src/sales_agent/graph/coach/daily_eval_graph.py` — 未接入且包含固定分数/空写入的占位图；现役 `DailyEvaluationService` 保留。

`src/sales_agent/models/quick_session.py` 和数据库表暂不删除，保留一个观察版本以便回滚和审计。

---

### Task 1: Add rollout configuration and establish a clean baseline

**Files:**
- Modify: `src/sales_agent/core/config.py:31-46,175-190,204-260`
- Modify: `config/default.yaml`
- Modify: `.env.example`
- Test: `tests/unit/test_guided_flows_config.py`

- [ ] **Step 1: Run the existing graph, coach, DingTalk, and API regression baseline**

Run:

```bash
pytest -q tests/unit/graph tests/unit/coach tests/unit/dingtalk tests/integration/test_graph_pipeline_parity.py
```

Expected: record the exact pass/fail count before changes. Existing failures must be written into the task notes and must not be attributed to this implementation.

- [ ] **Step 2: Write failing configuration tests**

Create `tests/unit/test_guided_flows_config.py`:

```python
from sales_agent.core.config import Settings


def test_guided_flows_defaults_enabled_in_shanghai():
    settings = Settings()
    assert settings.guided_flows.enabled is True
    assert settings.guided_flows.timezone == "Asia/Shanghai"


def test_guided_flows_env_override(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("guided_flows:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("GUIDED_FLOWS_ENABLED", "false")
    settings = Settings.from_yaml(config_file)
    assert settings.guided_flows.enabled is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:

```bash
pytest -q tests/unit/test_guided_flows_config.py
```

Expected: FAIL because `Settings.guided_flows` does not exist.

- [ ] **Step 4: Add the configuration model and environment override**

Add to `src/sales_agent/core/config.py` before `Settings`:

```python
class GuidedFlowsConfig(BaseModel):
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
```

Add to `Settings`:

```python
guided_flows: GuidedFlowsConfig = GuidedFlowsConfig()
```

Inside `Settings.from_yaml`, before `return cls(**raw)`, add:

```python
guided_flows_enabled = os.getenv("GUIDED_FLOWS_ENABLED")
if guided_flows_enabled is not None:
    raw.setdefault("guided_flows", {})["enabled"] = (
        guided_flows_enabled.strip().lower() in {"1", "true", "yes", "on"}
    )
```

Add to `config/default.yaml`:

```yaml
guided_flows:
  enabled: true
  timezone: "Asia/Shanghai"
```

Add to `.env.example`:

```dotenv
# Unified online guided flows; set false for immediate rollback to plain Chat routing.
GUIDED_FLOWS_ENABLED=true
```

- [ ] **Step 5: Run configuration tests**

Run:

```bash
pytest -q tests/unit/test_guided_flows_config.py
```

Expected: `2 passed`.

- [ ] **Step 6: Commit configuration**

```bash
git add src/sales_agent/core/config.py config/default.yaml .env.example tests/unit/test_guided_flows_config.py
git commit -m "feat: configure unified guided flows"
```

---

### Task 2: Define flow contracts and deterministic triggers

**Files:**
- Create: `src/sales_agent/graph/guided_flow/__init__.py`
- Create: `src/sales_agent/graph/guided_flow/types.py`
- Create: `src/sales_agent/graph/guided_flow/triggers.py`
- Test: `tests/unit/graph/guided_flow/test_triggers.py`

- [ ] **Step 1: Write trigger and contract tests**

Create `tests/unit/graph/guided_flow/test_triggers.py`:

```python
import pytest

from sales_agent.graph.guided_flow.triggers import (
    is_cancel_command,
    resolve_requested_flow,
)


@pytest.mark.parametrize(
    ("text", "flow_id"),
    [
        ("访前准备", "visit_preparation"),
        ("访后复盘", "post_visit_review"),
        ("小赢欣赏", "small_win_appreciation"),
        ("卡点破框", "sales_block_breakthrough"),
    ],
)
def test_exact_phrases_trigger_flow(text, flow_id):
    assert resolve_requested_flow(message=f"  {text}  ", entry_action=None) == flow_id


def test_entry_action_has_priority():
    assert resolve_requested_flow(
        message="小赢欣赏", entry_action="post_visit_review"
    ) == "post_visit_review"


def test_substrings_do_not_trigger():
    assert resolve_requested_flow(
        message="明天访前准备材料还没写完", entry_action=None
    ) is None


def test_cancel_is_exact():
    assert is_cancel_command("退出") is True
    assert is_cancel_command("我不想退出客户沟通") is False
```

- [ ] **Step 2: Run trigger tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_triggers.py
```

Expected: collection FAIL because the package does not exist.

- [ ] **Step 3: Implement immutable flow contracts**

Create `src/sales_agent/graph/guided_flow/types.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FlowStart:
    stage: str
    payload: dict[str, Any]
    reply: str


@dataclass(frozen=True)
class FlowAdvance:
    stage: str
    payload: dict[str, Any]
    reply: str
    completed: bool


@dataclass(frozen=True)
class FlowServices:
    db: Any
    chat_model: Any
    tenant_id: str
    agent_id: str


StartFlow = Callable[[], FlowStart]
AdvanceFlow = Callable[[str, dict[str, Any], str, FlowServices], Awaitable[FlowAdvance]]


@dataclass(frozen=True)
class FlowDefinition:
    id: str
    label: str
    trigger_phrases: frozenset[str]
    start: StartFlow
    advance: AdvanceFlow
    metadata: dict[str, str] = field(default_factory=dict)
```

Create `src/sales_agent/graph/guided_flow/__init__.py`:

```python
from sales_agent.graph.guided_flow.types import FlowAdvance, FlowDefinition, FlowServices, FlowStart

__all__ = [
    "FlowAdvance",
    "FlowDefinition",
    "FlowServices",
    "FlowStart",
]
```

Do not import `graph.py` yet; that module is created in Task 5. Task 5 adds `build_guided_flow_graph` to this public API after the file exists.

- [ ] **Step 4: Implement exact trigger parsing**

Create `src/sales_agent/graph/guided_flow/triggers.py`:

```python
from __future__ import annotations

import unicodedata


ACTION_TO_FLOW = {
    "visit_preparation": "visit_preparation",
    "post_visit_review": "post_visit_review",
    "small_win_appreciation": "small_win_appreciation",
    "sales_block_breakthrough": "sales_block_breakthrough",
}

PHRASE_TO_FLOW = {
    "访前准备": "visit_preparation",
    "访后复盘": "post_visit_review",
    "小赢欣赏": "small_win_appreciation",
    "卡点破框": "sales_block_breakthrough",
}

CANCEL_COMMANDS = frozenset({"退出", "取消", "结束", "cancel", "exit", "/exit", "/cancel"})


def normalize_explicit_text(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower()


def resolve_requested_flow(*, message: str | None, entry_action: str | None) -> str | None:
    action = normalize_explicit_text(entry_action)
    if action in ACTION_TO_FLOW:
        return ACTION_TO_FLOW[action]
    return PHRASE_TO_FLOW.get(normalize_explicit_text(message))


def is_cancel_command(message: str | None) -> bool:
    return normalize_explicit_text(message) in CANCEL_COMMANDS
```

- [ ] **Step 5: Run trigger tests**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_triggers.py
```

Expected: all parameterized cases pass.

- [ ] **Step 6: Commit contracts and trigger parser**

```bash
git add src/sales_agent/graph/guided_flow tests/unit/graph/guided_flow/test_triggers.py
git commit -m "feat: define guided flow contracts and triggers"
```

---

### Task 3: Extract the proven small-win and sales-block handlers

**Files:**
- Create: `src/sales_agent/graph/guided_flow/handlers/__init__.py`
- Create: `src/sales_agent/graph/guided_flow/handlers/coach_flows.py`
- Modify: `tests/unit/coach/test_quick_session.py`
- Test: `tests/unit/graph/guided_flow/test_coach_handlers.py`

- [ ] **Step 1: Write adapter-level failing tests**

Create `tests/unit/graph/guided_flow/test_coach_handlers.py`:

```python
import pytest

from sales_agent.graph.guided_flow.handlers.coach_flows import (
    advance_sales_block,
    advance_small_win,
    start_sales_block,
    start_small_win,
)
from sales_agent.graph.guided_flow.types import FlowServices


SERVICES = FlowServices(db=None, chat_model=None, tenant_id="t1", agent_id="a1")


@pytest.mark.asyncio
async def test_small_win_advances_one_turn():
    started = start_small_win()
    result = await advance_small_win(
        started.stage,
        started.payload,
        "今天主动联系了一个一直没回复的客户",
        SERVICES,
    )
    assert result.stage == "strength"
    assert result.completed is False
    assert "能力" in result.reply or "特质" in result.reply


@pytest.mark.asyncio
async def test_sales_block_finishes_with_fallback_card():
    started = start_sales_block()
    first = await advance_sales_block(
        started.stage, started.payload, "客户嫌贵一直不回", SERVICES
    )
    second = await advance_sales_block(
        first.stage, first.payload, "事实是嫌贵；我判断没戏，担心被拒", SERVICES
    )
    final = await advance_sales_block(
        second.stage, second.payload, "可能怕买错，先问清风险", SERVICES
    )
    assert final.completed is True
    assert "最小行动" in final.reply
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_coach_handlers.py
```

Expected: import FAIL because handler functions do not exist.

- [ ] **Step 3: Move pure business logic without DB session functions**

Create `src/sales_agent/graph/guided_flow/handlers/coach_flows.py` by moving the existing constants and pure helpers from `src/sales_agent/coach/quick_session.py:68-414`. Do not move `_get_active`, `_get_recent_completed`, `start_session`, `advance_active_session`, SQLAlchemy imports, or `QuickSession` model imports.

Expose adapters with these exact signatures:

```python
from sales_agent.graph.guided_flow.types import FlowAdvance, FlowServices, FlowStart
from sales_agent.services.prompt_resolver_helper import resolve_quick_session_prompts


def start_small_win() -> FlowStart:
    stage, payload, reply = _sw_start()
    return FlowStart(stage=stage, payload=payload, reply=reply)


async def advance_small_win(
    stage: str,
    payload: dict,
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    prompts = {}
    if services.db is not None:
        prompts = await resolve_quick_session_prompts(
            services.db, services.tenant_id, services.agent_id
        )
    next_stage, next_payload, reply, completed = await _sw_advance(
        services.chat_model, stage, payload, text, prompts
    )
    return FlowAdvance(next_stage, next_payload, reply, completed)


def start_sales_block() -> FlowStart:
    stage, payload, reply = _sb_start()
    return FlowStart(stage=stage, payload=payload, reply=reply)


async def advance_sales_block(
    stage: str,
    payload: dict,
    text: str,
    services: FlowServices,
) -> FlowAdvance:
    prompts = {}
    if services.db is not None:
        prompts = await resolve_quick_session_prompts(
            services.db, services.tenant_id, services.agent_id
        )
    next_stage, next_payload, reply, completed = await _sb_advance(
        services.chat_model, stage, payload, text, prompts
    )
    return FlowAdvance(next_stage, next_payload, reply, completed)
```

- [ ] **Step 4: Point existing pure-logic tests at the new handler module**

In `tests/unit/coach/test_quick_session.py`, replace:

```python
from sales_agent.coach import quick_session as qs
```

with:

```python
from sales_agent.graph.guided_flow.handlers import coach_flows as qs
```

Keep all existing assertions for clarification, LLM use, fallback cards and cancel detection. Replace the old label/`VALID_TYPES` assertion with registry assertions in Task 5.

- [ ] **Step 5: Run old and new coach handler tests**

Run:

```bash
pytest -q tests/unit/coach/test_quick_session.py tests/unit/graph/guided_flow/test_coach_handlers.py
```

Expected: all existing behavioral tests plus the two adapter tests pass.

- [ ] **Step 6: Commit extracted handlers**

```bash
git add src/sales_agent/graph/guided_flow/handlers tests/unit/coach/test_quick_session.py tests/unit/graph/guided_flow/test_coach_handlers.py
git commit -m "refactor: extract guided coach flow handlers"
```

---

### Task 4: Implement visit preparation and post-visit review as one-question-per-turn handlers

**Files:**
- Create: `src/sales_agent/graph/guided_flow/handlers/visit_flows.py`
- Test: `tests/unit/graph/guided_flow/test_visit_handlers.py`
- Reuse: `src/sales_agent/prompts/visit_preparation.py`
- Reuse: `src/sales_agent/prompts/post_visit_review.py`

- [ ] **Step 1: Write failing visit-flow tests**

Create `tests/unit/graph/guided_flow/test_visit_handlers.py`:

```python
import pytest

from sales_agent.graph.guided_flow.handlers.visit_flows import (
    advance_post_visit,
    advance_visit_preparation,
    start_post_visit,
    start_visit_preparation,
)
from sales_agent.graph.guided_flow.types import FlowServices


SERVICES = FlowServices(db=None, chat_model=None, tenant_id="t1", agent_id="a1")


@pytest.mark.asyncio
async def test_visit_preparation_is_three_answers_then_card():
    state = start_visit_preparation()
    assert state.stage == "customer"
    first = await advance_visit_preparation(state.stage, state.payload, "上海某工会负责人", SERVICES)
    assert first.stage == "situation" and first.completed is False
    second = await advance_visit_preparation(first.stage, first.payload, "正在比较两家福利平台", SERVICES)
    assert second.stage == "goal" and second.completed is False
    final = await advance_visit_preparation(second.stage, second.payload, "确认试点范围和时间", SERVICES)
    assert final.completed is True
    assert "访前作战卡" in final.reply


@pytest.mark.asyncio
async def test_post_visit_is_three_answers_then_card():
    state = start_post_visit()
    first = await advance_post_visit(state.stage, state.payload, "客户认可方案但担心预算", SERVICES)
    second = await advance_post_visit(first.stage, first.payload, "有兴趣，仍在观望", SERVICES)
    final = await advance_post_visit(second.stage, second.payload, "周五前补预算测算", SERVICES)
    assert final.completed is True
    assert "机会推进卡" in final.reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_visit_handlers.py
```

Expected: import FAIL because `visit_flows.py` does not exist.

- [ ] **Step 3: Implement deterministic starts, one-step advances, and fallback cards**

Create `src/sales_agent/graph/guided_flow/handlers/visit_flows.py`. The public functions must use the same contracts as Task 3. The rule-only fallback renderers must include collected facts and must not invent values:

```python
from __future__ import annotations

from sales_agent.graph.guided_flow.types import FlowAdvance, FlowServices, FlowStart
from sales_agent.services.agent_executor import execute_agent
from sales_agent.services.prompt_resolver_helper import resolve_execution_prompts


def start_visit_preparation() -> FlowStart:
    return FlowStart("customer", {}, "你这次要见谁？请说一下客户、组织或对方角色。")


def start_post_visit() -> FlowStart:
    return FlowStart("customer_words", {}, "刚才客户主要说了什么？请尽量保留客户的原话或明确事实。")


def _visit_fallback(payload: dict) -> str:
    return (
        "## 访前作战卡\n\n"
        f"**客户对象**：{payload['customer']}\n\n"
        f"**客户现状**：{payload['situation']}\n\n"
        f"**本次沟通目标**：{payload['goal']}\n\n"
        "**建议动作**：围绕目标确认客户当前优先级、关键顾虑和可接受的下一步。"
    )


def _post_visit_fallback(payload: dict) -> str:
    return (
        "## 访后机会推进卡\n\n"
        f"**客户表达**：{payload['customer_words']}\n\n"
        f"**客户态度**：{payload['attitude']}\n\n"
        f"**下一步约定**：{payload['next_step']}\n\n"
        "**复盘动作**：按约定时间跟进，并验证客户是否完成了承诺动作。"
    )


async def _generate_card(flow_id: str, message: str, fallback: str, services: FlowServices) -> str:
    if services.chat_model is None:
        return fallback
    prompt_text = None
    system_prompt_text = None
    if services.db is not None:
        prompt_text, system_prompt_text = await resolve_execution_prompts(
            services.db, services.agent_id, services.tenant_id, flow_id
        )
    answer = await execute_agent(
        chat_model=services.chat_model,
        task_type=flow_id,
        message=message,
        context={},
        retrieval_result=None,
        history_messages=[],
        tenant_style={},
        prompt_text=prompt_text,
        system_prompt_text=system_prompt_text,
    )
    summary = answer.get("summary", "")
    sections = answer.get("sections", [])
    rendered_sections = "\n\n".join(
        f"## {section.get('title', '')}\n{section.get('content', '')}"
        for section in sections
        if section.get("content")
    )
    return "\n\n".join(part for part in (summary, rendered_sections) if part) or fallback
```

Implement `advance_visit_preparation` with transitions `customer -> situation -> goal -> completed`, and `advance_post_visit` with `customer_words -> attitude -> next_step -> completed`. Each transition stores a stripped answer in a new payload dict. At the terminal transition, call `_generate_card` inside `try/except Exception`; on exception return the deterministic fallback and `completed=True`.

- [ ] **Step 4: Run visit handler tests**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_visit_handlers.py
```

Expected: `2 passed` using deterministic fallback cards.

- [ ] **Step 5: Add LLM-failure tests**

Add a model whose `generate` raises `RuntimeError("model down")` and assert both terminal advances still return `completed=True` and the correct fallback card heading.

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_visit_handlers.py
```

Expected: all visit tests pass.

- [ ] **Step 6: Commit visit handlers**

```bash
git add src/sales_agent/graph/guided_flow/handlers/visit_flows.py tests/unit/graph/guided_flow/test_visit_handlers.py
git commit -m "feat: add guided visit workflows"
```

---

### Task 5: Register four flows and build the one-turn Guided Flow subgraph

**Files:**
- Create: `src/sales_agent/graph/guided_flow/definitions.py`
- Create: `src/sales_agent/graph/guided_flow/state.py`
- Create: `src/sales_agent/graph/guided_flow/nodes.py`
- Create: `src/sales_agent/graph/guided_flow/graph.py`
- Test: `tests/unit/graph/guided_flow/test_graph.py`

- [ ] **Step 1: Write failing registry and graph tests**

Create `tests/unit/graph/guided_flow/test_graph.py`:

```python
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sales_agent.graph.guided_flow.definitions import FLOW_DEFINITIONS
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph


def test_registry_has_exactly_four_product_flows():
    assert set(FLOW_DEFINITIONS) == {
        "visit_preparation",
        "post_visit_review",
        "small_win_appreciation",
        "sales_block_breakthrough",
    }


@pytest.mark.asyncio
async def test_start_and_advance_are_separate_invocations():
    graph = build_guided_flow_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "flow:t1:a1:dingtalk:u1:2026-07-06"}}
    started = await graph.ainvoke(
        {
            "tenant_id": "t1",
            "agent_id": "a1",
            "user_id": "u1",
            "channel": "dingtalk",
            "conversation_id": "c1",
            "message": "小赢欣赏",
            "flow_action": "start",
            "requested_flow": "small_win_appreciation",
        },
        config=config,
        context={"db": None, "chat_model": None},
    )
    assert started["active_flow"] == "small_win_appreciation"
    assert started["flow_stage"] == "small_win"
    assert "小赢" in started["answer_dict"]["summary"]

    advanced = await graph.ainvoke(
        {"message": "今天主动联系了一个一直没回复的客户", "flow_action": "advance"},
        config=config,
        context={"db": None, "chat_model": None},
    )
    assert advanced["flow_stage"] == "strength"
    assert advanced["active_flow"] == "small_win_appreciation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/guided_flow/test_graph.py
```

Expected: import FAIL for missing registry/state/graph.

- [ ] **Step 3: Implement the registry**

Create `definitions.py` using the four public start/advance functions. Each `trigger_phrases` contains only the corresponding standard Chinese phrase. Export `FLOW_DEFINITIONS: dict[str, FlowDefinition]` and `get_flow_definition(flow_id)` which raises `ValueError("unknown guided flow: <id>")` for invalid IDs.

- [ ] **Step 4: Implement serializable state**

Create `state.py`:

```python
from typing import Any
from typing_extensions import TypedDict


class GuidedFlowState(TypedDict, total=False):
    tenant_id: str
    agent_id: str
    user_id: str
    channel: str
    conversation_id: str
    message: str
    flow_action: str
    requested_flow: str | None
    active_flow: str | None
    flow_stage: str | None
    flow_payload: dict[str, Any]
    answer_dict: dict[str, Any]
    response_kind: str
    completed_flow: str | None
```

- [ ] **Step 5: Implement start, advance, and cancel nodes**

Create `nodes.py`. All callbacks are loaded from the registry, never stored in state. `start_flow_node` returns the first question and replaces old payload. `advance_flow_node` creates `FlowServices` from state/runtime, invokes exactly one handler advance, and clears `active_flow`, `flow_stage`, and `flow_payload` only when completed. `cancel_flow_node` clears those fields and returns `{"summary": "已退出当前引导流程，你可以正常提问了。", "sections": []}`.

On handler exception, `advance_flow_node` must preserve `active_flow`, `flow_stage`, and `flow_payload`, and return `response_kind="flow_retry"` with `"刚才处理失败了，请重新回答当前问题。"`.

- [ ] **Step 6: Build the subgraph**

Create `graph.py`:

```python
from langgraph.graph import END, START, StateGraph

from sales_agent.graph.guided_flow.nodes import advance_flow_node, cancel_flow_node, start_flow_node
from sales_agent.graph.guided_flow.state import GuidedFlowState


def _route_action(state: GuidedFlowState) -> str:
    return state.get("flow_action", "advance")


def build_guided_flow_graph() -> StateGraph:
    builder = StateGraph(GuidedFlowState)
    builder.add_node("start_flow", start_flow_node)
    builder.add_node("advance_flow", advance_flow_node)
    builder.add_node("cancel_flow", cancel_flow_node)
    builder.add_conditional_edges(
        START,
        _route_action,
        {"start": "start_flow", "advance": "advance_flow", "cancel": "cancel_flow"},
    )
    builder.add_edge("start_flow", END)
    builder.add_edge("advance_flow", END)
    builder.add_edge("cancel_flow", END)
    return builder
```

Then update `guided_flow/__init__.py`:

```python
from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
from sales_agent.graph.guided_flow.types import FlowAdvance, FlowDefinition, FlowServices, FlowStart

__all__ = [
    "FlowAdvance",
    "FlowDefinition",
    "FlowServices",
    "FlowStart",
    "build_guided_flow_graph",
]
```

- [ ] **Step 7: Add completion, same-flow restart, and cancellation tests**

Test that four small-win answers require four separate `ainvoke` calls; a second `flow_action="start"` resets payload even for the same flow; `cancel` clears state. No single invoke may reach `completed` from a start action.

Run:

```bash
pytest -q tests/unit/graph/guided_flow
```

Expected: all trigger, handler, registry and graph tests pass.

- [ ] **Step 8: Commit Guided Flow graph**

```bash
git add src/sales_agent/graph/guided_flow tests/unit/graph/guided_flow
git commit -m "feat: build one-turn guided flow graph"
```

---

### Task 6: Build the Online Conversation Graph and in-memory runtime

**Files:**
- Create: `src/sales_agent/graph/online_state.py`
- Create: `src/sales_agent/graph/online_graph.py`
- Create: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/graph/checkpoints.py`
- Test: `tests/unit/graph/test_online_graph.py`

- [ ] **Step 1: Write failing routing and lifecycle tests**

Create tests for these transitions using an injected `InMemorySaver` and a stub `chat_runner`:

```python
@pytest.mark.asyncio
async def test_new_flow_preempts_existing_flow(online_graph, config):
    await online_graph.ainvoke(base_input(message="小赢欣赏", event_id="e1"), config=config)
    await online_graph.ainvoke(base_input(message="一个真实进展", event_id="e2"), config=config)
    switched = await online_graph.ainvoke(base_input(message="访前准备", event_id="e3"), config=config)
    assert switched["active_flow"] == "visit_preparation"
    assert switched["flow_stage"] == "customer"
    assert switched["flow_payload"] == {}


@pytest.mark.asyncio
async def test_duplicate_event_does_not_advance(online_graph, config):
    await online_graph.ainvoke(base_input(message="小赢欣赏", event_id="e1"), config=config)
    first = await online_graph.ainvoke(base_input(message="今天有进展", event_id="e2"), config=config)
    duplicate = await online_graph.ainvoke(base_input(message="今天有进展", event_id="e2"), config=config)
    assert duplicate["flow_stage"] == first["flow_stage"]
    assert duplicate["response_kind"] == "duplicate"
```

Also test: no active flow calls the Chat runner; cancel beats active-flow advance; `GUIDED_FLOWS_ENABLED=false` always calls Chat; different dates produce different thread IDs; a fresh Saver cannot recover the previous active flow.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/test_online_graph.py
```

Expected: import FAIL because Online Graph does not exist.

- [ ] **Step 3: Define a narrow online state**

`OnlineConversationState` contains identity/input fields, guided-flow fields, final response fields, `event_id`, and `last_event_id`. Do not inherit `ChatGraphState`; Chat internals such as `sources` reducers, retry counters and retrieval objects must not persist in the online checkpoint.

- [ ] **Step 4: Implement Online Graph routing**

`normalize_turn_node` must set `requested_flow` on every turn, including explicit `None`, and choose `flow_action` with this order:

```python
if state.get("event_id") and state.get("event_id") == state.get("last_event_id"):
    return "duplicate"
if state.get("guided_flows_enabled") and requested_flow:
    return "start"
if state.get("guided_flows_enabled") and active_flow and is_cancel_command(message):
    return "cancel"
if state.get("guided_flows_enabled") and active_flow:
    return "advance"
return "chat"
```

Add nodes `guided_flow`, `chat`, and `duplicate`. The Guided Flow node invokes a cached compiled Guided Flow graph without its own checkpointer so parent state owns persistence. The Chat node invokes a cached Chat Graph without checkpointer and maps only stable response fields back to Online State. Every non-duplicate terminal node sets `last_event_id=event_id`.

- [ ] **Step 5: Implement runtime helpers**

In `services/online_conversation.py`, implement:

```python
def build_online_thread_id(
    tenant_id: str,
    agent_id: str,
    channel: str,
    session_user_id: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    return ":".join(("online", tenant_id, agent_id, channel, session_user_id, current.date().isoformat()))
```

Add a process-level Online `InMemorySaver`, cache the compiled Online Graph once, and expose this concrete production/test seam:

```python
async def invoke_online_turn(
    *,
    db,
    tenant_id: str,
    agent_id: str | None,
    user_id: str,
    session_user_id: str,
    channel: str,
    conversation_id: str,
    message: str,
    entry_action: str | None = None,
    event_id: str | None = None,
    chat_model=None,
    embedding_model=None,
    now: datetime | None = None,
    checkpointer=None,
) -> dict:
    agent = await resolve_tenant_agent_id(db, tenant_id, agent_id)
    settings = get_settings()
    thread_id = build_online_thread_id(
        tenant_id,
        agent.id,
        channel,
        session_user_id,
        now=now,
        timezone_name=settings.guided_flows.timezone,
    )
    graph = get_online_graph(checkpointer=checkpointer)
    return await graph.ainvoke(
        {
            "tenant_id": tenant_id,
            "agent_id": agent.id,
            "user_id": user_id,
            "session_user_id": session_user_id,
            "channel": channel,
            "conversation_id": conversation_id,
            "message": message,
            "entry_action": entry_action,
            "event_id": event_id,
            "guided_flows_enabled": settings.guided_flows.enabled,
        },
        config={"configurable": {"thread_id": thread_id}},
        context={"db": db, "chat_model": chat_model, "embedding_model": embedding_model},
    )
```

When `chat_model` or `embedding_model` is absent, resolve the tenant model provider once in this service using `TenantResolver`; do not make individual graph nodes create providers. Allow tests to inject a Saver and `now`; production callers must not construct a new Saver per request.

- [ ] **Step 6: Add guided-turn conversation logging**

After Guided Flow output, call `conversation_logger.log_conversation` with `task_type=active_flow or completed_flow`, `path="guided_flow"`, `answer_dict`, and the existing conversation ID. Chat output must not be logged again by the parent because Chat Graph already owns its log node.

- [ ] **Step 7: Run Online Graph tests**

Run:

```bash
pytest -q tests/unit/graph/test_online_graph.py tests/unit/graph/guided_flow
```

Expected: all routing, preemption, deduplication, date and restart tests pass.

- [ ] **Step 8: Commit Online Graph runtime**

```bash
git add src/sales_agent/graph/online_state.py src/sales_agent/graph/online_graph.py src/sales_agent/services/online_conversation.py src/sales_agent/graph/checkpoints.py tests/unit/graph/test_online_graph.py
git commit -m "feat: add unified online conversation graph"
```

---

### Task 7: Route HTTP Chat through the Online Graph

**Files:**
- Modify: `src/sales_agent/api/routes/agent.py:34-88,91-169,174-242`
- Test: `tests/integration/test_online_guided_flows.py`
- Test: `tests/integration/test_graph_pipeline_parity.py`

- [ ] **Step 1: Write failing HTTP integration tests**

Use the existing FastAPI test fixture and dependency overrides. Assert:

1. `POST /agent/chat` with message `访前准备` returns the first question and `debug.path == "guided_flow"`.
2. Three subsequent requests with the same tenant, Agent, user, channel and conversation produce the second question, third question and final card.
3. A request with `小赢欣赏` between visit steps resets the flow.
4. A normal question still reaches Chat and retains existing response shape.

- [ ] **Step 2: Run the new integration tests to verify they fail**

Run:

```bash
pytest -q tests/integration/test_online_guided_flows.py
```

Expected: FAIL because `/agent/chat` still invokes `build_chat_graph_compiled` directly.

- [ ] **Step 3: Replace `_execute_via_graph` with the online service**

Keep the public `_execute_via_graph` signature for compatibility, but delegate to `invoke_online_turn`. Pass `session_user_id=user_id`, a deterministic event ID derived from request ID if available or `None` for HTTP, and explicit `entry_action=None`. Remove per-request `InMemorySaver`, `InMemoryStore`, and graph compilation.

Map Guided Flow output into existing `ChatResponse` without adding a second API response type:

```python
answer_dict = result.get("answer_dict") or {"summary": "", "sections": []}
task_type = result.get("task_type", "general_sales_coaching")
path = result.get("path", "standard")
```

- [ ] **Step 4: Run HTTP and parity tests**

Run:

```bash
pytest -q tests/integration/test_online_guided_flows.py tests/integration/test_graph_pipeline_parity.py tests/unit/graph
```

Expected: guided HTTP tests pass and existing Chat graph parity remains green.

- [ ] **Step 5: Commit HTTP cutover**

```bash
git add src/sales_agent/api/routes/agent.py tests/integration/test_online_guided_flows.py tests/integration/test_graph_pipeline_parity.py
git commit -m "feat: route HTTP chat through online graph"
```

---

### Task 8: Route all DingTalk entry and message paths through the same Online Graph

**Files:**
- Create: `src/sales_agent/integrations/dingtalk/agent_resolver.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py:98-220`
- Modify: `src/sales_agent/integrations/dingtalk/graph_stream.py:20-188`
- Modify: `src/sales_agent/integrations/dingtalk/quick_entry.py:119-162,205-283`
- Modify: `src/sales_agent/integrations/dingtalk/stream_client.py:183-255`
- Test: `tests/unit/dingtalk/test_online_flow_routing.py`
- Test: `tests/unit/dingtalk/test_quick_entry_jsapi.py`
- Test: `tests/unit/dingtalk/test_quick_entry_oauth2.py`

- [ ] **Step 1: Write failing DingTalk routing tests**

Patch `invoke_online_turn` and assert:

- Processor passes `session_user_id=sender_id`, internal `user_id`, event ID and resolved Agent ID.
- Stream path uses the same online service rather than Chat Graph directly.
- `_fulfill_quick_action` passes each of the four actions as `entry_action`, sends the returned first question, and never creates `QuickSession` rows.
- “教练模式” still opens the chooser page; its two choices produce `visit_preparation` and `post_visit_review` actions.

- [ ] **Step 2: Run DingTalk routing tests to verify they fail**

Run:

```bash
pytest -q tests/unit/dingtalk/test_online_flow_routing.py tests/unit/dingtalk/test_quick_entry_jsapi.py tests/unit/dingtalk/test_quick_entry_oauth2.py
```

Expected: new tests fail because processor/stream/quick entry still use separate paths.

- [ ] **Step 3: Extract channel Agent resolution**

Move the inline `AgentChannelConfig` lookup from `processor.py:126-153` into:

```python
async def resolve_dingtalk_agent_id(db: AsyncSession, tenant_id: str) -> str:
```

It must return the bound active Agent when present; otherwise call `resolve_tenant_agent_id(db, tenant_id, None)` and return that Agent's ID. Use this helper in processor, stream handler, and quick-entry fulfillment.

- [ ] **Step 4: Cut over non-streaming and streaming messages**

Replace direct `build_chat_graph_compiled` usage with Online Graph invoke/stream functions. Guided replies may finalize immediately as Markdown; normal Chat retains token streaming and progress events. Both paths must pass the same `event_id`, external sender ID as `session_user_id`, internal mapped user ID for logging, and resolved Agent ID.

- [ ] **Step 5: Cut over four quick actions**

For all four action configs, set `flow_id` equal to the action. `_fulfill_quick_action` must:

1. map the DingTalk user to internal user ID;
2. resolve the concrete Agent ID;
3. call `invoke_online_turn` with `message=""`, `entry_action=action`, channel `dingtalk`, and a button event ID;
4. send `result["answer_dict"]["summary"]`;
5. commit only message/log writes, with no `quick_sessions` access.

When `settings.guided_flows.enabled` is false, quick-action fulfillment returns a clear “该引导功能暂时停用” response and does not invoke the Online Graph with an empty message. Ordinary text continues to fall through to the existing Chat path, providing a safe rollback without reviving `quick_sessions`.

- [ ] **Step 6: Run DingTalk tests**

Run:

```bash
pytest -q tests/unit/dingtalk tests/integration/test_online_guided_flows.py
```

Expected: all DingTalk tests pass; quick-entry mocks assert zero calls to legacy session functions.

- [ ] **Step 7: Commit DingTalk cutover**

```bash
git add src/sales_agent/integrations/dingtalk tests/unit/dingtalk tests/integration/test_online_guided_flows.py
git commit -m "feat: unify DingTalk guided flow routing"
```

---

### Task 9: Cache Ontology, clean Graph Debug, and retire legacy session code

**Files:**
- Modify: `src/sales_agent/graph/nodes/retrieval.py:72-157`
- Create: `src/sales_agent/graph/registry.py`
- Modify: `src/sales_agent/graph/chat_graph.py:192-216`
- Modify: `src/sales_agent/api/routes/graph_debug.py:26,116-142,339-352`
- Modify: `src/sales_agent/graph/__init__.py`
- Delete: `src/sales_agent/coach/quick_session.py`
- Delete: `src/sales_agent/graph/coach/quick_session_graph.py`
- Delete: `src/sales_agent/graph/coach/daily_eval_graph.py`
- Test: `tests/unit/graph/test_registry.py`
- Test: `tests/unit/graph/test_retrieval_node.py`

- [ ] **Step 1: Write failing registry and compile-cache tests**

Assert Graph Registry contains exactly `online`, `guided-flow`, and `ontology-retrieval`; `daily-eval` is absent until a real implementation exists. Patch `build_ontology_retrieval_graph` and invoke ontology retrieval twice; assert the builder/compile path runs once.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest -q tests/unit/graph/test_registry.py tests/unit/graph/test_retrieval_node.py
```

Expected: registry still exposes four old entries and Ontology compiles twice.

- [ ] **Step 3: Cache the compiled Ontology subgraph**

Add a module-level `@lru_cache(maxsize=1)` helper in `retrieval.py`:

```python
@lru_cache(maxsize=1)
def _get_ontology_subgraph():
    return build_ontology_retrieval_graph().compile()
```

Use it in `_retrieve_via_ontology`. Add a test-only cache clear call in the fixture. Do not cache DB sessions or model objects; they continue through `runtime.context`.

- [ ] **Step 4: Move registry out of `chat_graph.py`**

Create `graph/registry.py` with builders for Online, Guided Flow and Ontology only. Update Graph Debug imports. Keep builder call signatures zero-argument so existing Mermaid and debug compilation code remains valid.

- [ ] **Step 5: Delete unused stub graphs and legacy DB session service**

Delete the two coach stub Graph files and remove exports/imports. Delete `coach/quick_session.py` only after all references resolve to Guided Flow handlers. Keep `models/quick_session.py`, migration/schema, and historical table untouched.

Run:

```bash
grep -RInE "start_session|advance_active_session|build_quick_session_graph|build_daily_eval_graph" src tests --include='*.py'
```

Expected: no production references. Tests may mention deleted names only in explicit migration assertions; otherwise the command returns no matches.

- [ ] **Step 6: Run graph and coach regressions**

Run:

```bash
pytest -q tests/unit/graph tests/unit/coach tests/integration/test_graph_pipeline_parity.py tests/integration/test_online_guided_flows.py
```

Expected: all tests pass; Graph Debug registry has no fake Daily Evaluation graph.

- [ ] **Step 7: Commit cleanup**

```bash
git add src/sales_agent/graph src/sales_agent/coach src/sales_agent/api/routes/graph_debug.py tests/unit/graph tests/unit/coach
git commit -m "refactor: retire legacy guided flow orchestration"
```

---

### Task 10: Full verification, rollout evidence, and documentation

**Files:**
- Modify: `README.md`
- Create: `changelog/2026-07-06.md`
- Verify: all changed Python/config files

- [ ] **Step 1: Run static syntax and whitespace checks**

Run:

```bash
python -m compileall -q src/sales_agent
git diff --check
```

Expected: both commands exit 0 with no errors.

- [ ] **Step 2: Run focused test suite**

Run:

```bash
pytest -q \
  tests/unit/graph \
  tests/unit/coach \
  tests/unit/dingtalk \
  tests/unit/test_guided_flows_config.py \
  tests/integration/test_graph_pipeline_parity.py \
  tests/integration/test_online_guided_flows.py
```

Expected: zero failures.

- [ ] **Step 3: Run the full non-live suite**

Run:

```bash
pytest -q --ignore=tests/integration/test_ontology_neo4j_live.py
```

Expected: zero failures. Any pre-existing baseline failure from Task 1 must be separately documented with its original evidence.

- [ ] **Step 4: Perform four end-to-end smoke scripts against a test instance**

For each flow, send the exact trigger then one answer per expected step. Verify:

- only one question is returned per request;
- the final response has the correct card type/heading;
- triggering another flow midway resets the prior payload;
- `退出` returns to normal Chat;
- restarting the process or changing the date starts from the first question;
- repeated DingTalk event ID does not advance twice.

Record sanitized request/response evidence in `changelog/2026-07-06.md`; do not record customer content, tokens, secrets, or raw DingTalk payloads.

- [ ] **Step 5: Document operation and rollback**

Update README with:

```text
GUIDED_FLOWS_ENABLED=true enables unified visit preparation, post-visit review,
small-win appreciation, and sales-block breakthrough routing.
Set GUIDED_FLOWS_ENABLED=false and restart the instance to route all text to the
ordinary Chat pipeline. In-flight guided state is process memory and is discarded
on restart or Shanghai natural-day rollover.
```

Document that `quick_sessions` is retained but no longer written, and that Daily Evaluation still runs through `DailyEvaluationService` and scheduler.

- [ ] **Step 6: Review the final diff against the design acceptance criteria**

Run:

```bash
git diff --stat 4290592..HEAD
git log --oneline 4290592..HEAD
```

Check every acceptance criterion in `docs/superpowers/specs/2026-07-06-unified-guided-flows-design.md:346-354` against a test or smoke result. Do not declare completion if any criterion lacks evidence.

- [ ] **Step 7: Commit verification documentation**

```bash
git add README.md changelog/2026-07-06.md
git commit -m "docs: document unified guided flow rollout"
```

---

## Out-of-scope follow-up plan

Create a separate design and implementation plan for converting the real `DailyEvaluationService` into a production LangGraph. That work must preserve per-user idempotency, dry-run behavior, validation, score writes, progression, rewards and scheduler error isolation; it must not reuse the deleted placeholder graph.
