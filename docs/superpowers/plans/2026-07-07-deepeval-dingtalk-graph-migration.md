# DeepEval DingTalk Graph Migration and Agent Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将全部 DeepEval 评估迁移到真实钉钉业务链路和统一 Online Graph，修正失真的 RAG 指标，加入路由、证据、多轮 Topic Memory、Guided Flow 与风险动作评估，并彻底删除生产零调用的旧 `ChatPipeline`。

**Architecture:** 评估从共享入口 `handle_dingtalk_event()` 进入，真实执行用户映射、会话映射、命令解析、Agent 绑定、Online Graph、检索、风险检查和钉钉渲染，只用内存 `reply_fn` 替代公网发送。Online Graph 将本次实际使用的来源、路由、风险、Usage 和节点轨迹返回给评估适配器；DeepEval 负责语义质量，确定性代码负责意图、知识策略、Topic、流程、证据和风险动作。

**Tech Stack:** Python 3.10+、LangGraph 1.2+、DeepEval 4.0.7、FastAPI、SQLAlchemy asyncio、PostgreSQL、Neo4j、Pydantic 2、pytest、pytest-asyncio、JSONL、Markdown/JSON/CSV/HTML reports。

## Global Constraints

- `ChatPipeline` 不保留兼容包装器、别名或回退分支；迁移完成后删除整个 `src/sales_agent/services/chat_pipeline.py`。
- 单轮和多轮评估必须经过 `handle_dingtalk_event()`，不得从评估代码直接调用 Chat Graph 节点、Retriever 或生成模型。
- 完整钉钉业务链路定义为标准化事件之后到最终用户可见消息：用户映射 → 会话映射 → 命令解析 → Agent 解析 → Online Graph → 检索/风险 → `DingTalkMessageRenderer` → `reply_fn`。WebSocket、HTTP 验签和钉钉公网发送由发布 E2E 测试负责。
- 评估只能使用生成本次回答时 Graph 实际返回的 `sources`；删除事后 `_fetch_ontology_sources()` 补查。
- `AnswerRecallMetric` 改名为 `ContextUtilizationMetric` / 报告名“检索信息利用率”；空上下文为不适用，裁判异常为评估错误，二者都不得记 1 分。
- `HallucinationMetric` 只在测试用例提供人工可信 `trusted_context` 时启用；实际 `retrieval_context` 只用于 Faithfulness。
- LLM Judge 不决定结构化路由、Topic、Flow、风险动作和数据隔离；这些使用精确断言和混淆矩阵。
- 历史 changelog、归档任务和旧设计文档保留原文；只清理运行代码、测试、现行 README 架构说明和未归档 todo。部署环境中的 `.env` 不修改、不提交。
- 不触碰其他 worktree 正在实施的 release E2E gate；执行时从主分支最新提交创建新的独立 worktree。

---

## File map

### Create

- `src/sales_agent/integrations/dingtalk/turn_result.py` — 钉钉业务链路的结构化执行结果。
- `eval/deepeval_dingtalk_adapter.py` — 单轮/多轮评估会话适配器，仅替换公网回复。
- `eval/deepeval_agentic_metrics.py` — 路由、知识策略、证据、Topic、Flow 和风险的确定性指标。
- `eval/deepeval_multiturn_eval.py` — 主动执行多轮钉钉场景并运行语义与状态评估。
- `eval/metric_thresholds.yaml` — 分指标、分问题类型阈值。
- `eval/datasets/agentic/single_turn_cases.jsonl` — 意图、知识策略、证据与拒答标注。
- `eval/datasets/agentic/multi_turn_cases.jsonl` — Topic、澄清、实体保留和流程场景。
- `tests/unit/eval/test_dingtalk_adapter.py`
- `tests/unit/eval/test_metric_applicability.py`
- `tests/unit/eval/test_agentic_metrics.py`
- `tests/unit/eval/test_multiturn_eval.py`
- `tests/unit/eval/test_report_schema_v2.py`
- `tests/unit/eval/test_threshold_calibration.py`
- `tests/integration/eval/test_dingtalk_graph_eval_flow.py`
- `tests/integration/test_ontology_chat_graph.py`

### Modify

- `src/sales_agent/graph/online_state.py`
- `src/sales_agent/graph/online_graph.py`
- `src/sales_agent/services/online_conversation.py`
- `src/sales_agent/integrations/dingtalk/processor.py`
- `eval/deepeval_test_cases.py`
- `eval/deepeval_metrics.py`
- `eval/deepeval_eval.py`
- `eval/deepeval_risk_eval.py`
- `eval/deepeval_optimize.py`
- `eval/deepeval_pytest_plugin.py`
- `eval/deepeval_html_report.py`
- `eval/deepeval_persistence.py`
- `eval/deepeval_run.sh`
- `eval/README_DEEPEVAL.md`
- `src/sales_agent/core/config.py`
- `config/default.yaml`
- `README.md` 当前架构说明及运行代码中的 ChatPipeline 注释。

### Delete

- `src/sales_agent/services/chat_pipeline.py`
- `src/sales_agent/services/path_router.py`
- `src/sales_agent/services/latency_tracker.py`
- `src/sales_agent/coach/intent_router.py`
- `tests/unit/test_processing_notice.py`
- `tests/unit/test_path_router.py`
- `tests/unit/test_latency_tracker.py`
- `tests/unit/coach/test_intent_router.py`
- `tests/integration/test_ontology_chat_pipeline.py`
- `tests/integration/coach/test_coach_pipeline_integration.py`
- `tests/integration/coach/test_realtime_guidance.py`
- `eval/deepeval_conversation_eval.py`
- `tasks/todo_eval_migrate_to_graph.md`

---

### Task 1: Define and propagate the observable Online Graph result

**Files:**
- Create: `src/sales_agent/integrations/dingtalk/turn_result.py`
- Modify: `src/sales_agent/graph/online_state.py`
- Modify: `src/sales_agent/graph/online_graph.py`
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Test: `tests/unit/graph/test_context_routing_nodes.py`
- Create: `tests/unit/eval/test_dingtalk_adapter.py`

**Interfaces:**
- Consumes: Chat Graph final state and `DingTalkMessageRenderer`.
- Produces: `DingTalkTurnResult`; `handle_dingtalk_event(..., capture_trace=False, agent_id_override=None) -> DingTalkTurnResult`; `invoke_online_turn(..., capture_trace=False) -> dict`.

- [ ] **Step 1: Write failing state-propagation tests**

Inject a Chat runner result and assert Online Graph preserves its observable fields:

```python
class ObservableStubChatRunner:
    async def ainvoke(self, *args, **kwargs):
        return {
            "answer_dict": {
                "summary": "福多多价格为 73100 元",
                "sections": [],
                "sources": [{"title": "产品价格表", "text": "73100 元"}],
            },
            "sources": [{"title": "产品价格表", "text": "73100 元"}],
            "retrieval_info": {"called": True, "source_count": 1},
            "risk_result": {"level": "none", "flags": [], "action": "allow"},
            "risk_action": "allow",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            "run_id": "run-1",
            "path": "standard",
        }

assert result["sources"][0]["title"] == "产品价格表"
assert result["retrieval_info"]["called"] is True
assert result["risk_action"] == "allow"
assert result["usage"]["total_tokens"] == 28
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv/bin/pytest -q tests/unit/graph/test_context_routing_nodes.py -k observable`

Expected: FAIL because `chat_node()` currently returns only answer, response kind and event ID.

- [ ] **Step 3: Extend Online state and map Chat Graph outputs**

Add to `OnlineConversationState`:

```python
sources: list[dict[str, Any]]
retrieval_info: dict[str, Any]
risk_result: dict[str, Any]
risk_action: str | None
usage: dict[str, int]
run_id: str | None
path: str | None
trace_nodes: list[str]
```

Return them from `chat_node()` using Chat Graph top-level values; only fall back to `answer_dict["sources"]` when top-level sources are absent.

- [ ] **Step 4: Add optional trace capture without changing production defaults**

Keep `graph.ainvoke()` when `capture_trace=False`. When true, execute the same compiled graph using `astream(stream_mode=["updates", "debug"])`, collect task-start node names, then read final state:

```python
async def _invoke_online_with_trace(graph, input_state, config, context) -> dict:
    trace_nodes: list[str] = []
    async for mode, payload in graph.astream(
        input_state, config, context=context, stream_mode=["updates", "debug"]
    ):
        if mode == "debug" and payload.get("type") == "task":
            name = (payload.get("payload") or {}).get("name")
            if name:
                trace_nodes.append(name)
    snapshot = await graph.aget_state(config)
    return {**dict(snapshot.values), "trace_nodes": trace_nodes}
```

- [ ] **Step 5: Define the immutable turn result**

```python
@dataclass(frozen=True)
class DingTalkTurnResult:
    event_id: str
    tenant_id: str
    agent_id: str | None
    internal_user_id: str | None
    dingtalk_user_id: str
    conversation_id: str | None
    normalized_message: str
    rendered_text: str
    answer_dict: dict[str, Any]
    sources: list[dict[str, Any]]
    retrieval_info: dict[str, Any]
    risk_result: dict[str, Any]
    risk_action: str | None
    task_type: str | None
    response_kind: str
    turn_relation: str | None
    knowledge_policy: str | None
    needs_retrieval: bool | None
    topic_id: str | None
    retained_entities: list[str]
    retracted_goals: list[str]
    pending_clarification_id: str | None
    context_status: str | None
    active_flow: str | None
    flow_stage: str | None
    completed_flow: str | None
    flow_action: str | None
    usage: dict[str, int]
    trace_nodes: list[str]
    latency_ms: int
    error: str | None = None
```

- [ ] **Step 6: Return the contract from the real DingTalk processor**

Extend `handle_dingtalk_event()` with `capture_trace: bool = False` and `agent_id_override: str | None = None`. Resolve overrides through `resolve_tenant_agent_id()` so another tenant's Agent cannot be selected. Production callers pass neither option and may ignore the return value.

Normal, reset, fallback, media-failure and caught-error branches must all return a populated result reflecting the actual reply.

- [ ] **Step 7: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/graph/test_context_routing_nodes.py tests/unit/eval/test_dingtalk_adapter.py
git add src/sales_agent/integrations/dingtalk/turn_result.py \
  src/sales_agent/graph/online_state.py src/sales_agent/graph/online_graph.py \
  src/sales_agent/services/online_conversation.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  tests/unit/graph/test_context_routing_nodes.py tests/unit/eval/test_dingtalk_adapter.py
git commit -m "feat(eval): expose observable dingtalk graph turn result"
```

Expected: PASS; production-default invocation still uses `ainvoke`.

---

### Task 2: Build the DeepEval adapter on the real DingTalk business flow

**Files:**
- Create: `eval/deepeval_dingtalk_adapter.py`
- Modify: `eval/deepeval_test_cases.py`
- Modify: `tests/unit/eval/test_dingtalk_adapter.py`
- Create: `tests/integration/eval/test_dingtalk_graph_eval_flow.py`

**Interfaces:**
- Consumes: Task 1 result and `QuestionItem`.
- Produces: `DingTalkEvalResponse`; `DingTalkEvalSession.run_turn()`; `run_dingtalk_eval_turn()`; `build_llm_test_case()` using actual sources only.

- [ ] **Step 1: Write failing session-isolation tests**

```python
async def test_single_turn_questions_do_not_share_memory(adapter):
    a = await adapter.run_dingtalk_eval_turn(Q1, tenant_id="t1", eval_case_id="q1")
    b = await adapter.run_dingtalk_eval_turn(Q2, tenant_id="t1", eval_case_id="q2")
    assert a.dingtalk_user_id != b.dingtalk_user_id
    assert a.conversation_id != b.conversation_id


async def test_multiturn_session_reuses_identity(session):
    first = await session.run_turn("福多多有哪些能力？")
    second = await session.run_turn("价格呢？")
    assert first.dingtalk_user_id == second.dingtalk_user_id
    assert first.conversation_id == second.conversation_id
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/pytest -q tests/unit/eval/test_dingtalk_adapter.py`

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Define response and session contracts**

```python
@dataclass
class DingTalkEvalResponse:
    answer_text: str = ""
    rendered_output: str = ""
    retrieval_context: list[str] = field(default_factory=list)
    source_items: list[dict[str, Any]] = field(default_factory=list)
    task_type: str = ""
    response_kind: str = ""
    turn_relation: str | None = None
    knowledge_policy: str | None = None
    needs_retrieval: bool | None = None
    topic_id: str | None = None
    retained_entities: list[str] = field(default_factory=list)
    retracted_goals: list[str] = field(default_factory=list)
    pending_clarification_id: str | None = None
    context_status: str | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    completed_flow: str | None = None
    flow_action: str | None = None
    risk_level: str = "none"
    risk_action: str = "allow"
    trace_nodes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    dingtalk_user_id: str = ""
    conversation_id: str = ""
    error: str = ""
```

`DingTalkEvalSession` owns tenant, optional Agent override, deterministic corp ID, one sender ID and monotonically increasing event ID. Each turn opens a DB session, calls `handle_dingtalk_event(..., capture_trace=True)`, captures the real `reply_fn`, commits side effects and maps the result.

- [ ] **Step 4: Construct local tenant runtime without global mutation**

Resolve the tenant through `TenantResolver`, then pass a local `TenantRuntime` directly to the processor. Do not assign `tenant_runtime._runtime`; concurrent tenant comparisons must not race.

- [ ] **Step 5: Remove post-hoc ontology retrieval and build honest cases**

Delete `_is_ontology_engine()` and `_fetch_ontology_sources()`. `_extract_sources()` may normalize fields but only from `DingTalkTurnResult.sources`.

Add `trusted_context: list[str] = field(default_factory=list)` to `QuestionItem` before constructing test cases.

```python
return LLMTestCase(
    input=question.text,
    actual_output=response.answer_text,
    expected_output=question.reference or None,
    retrieval_context=response.retrieval_context or None,
    context=question.trusted_context or None,
    tools_called=[],
)
```

Never assign retrieved sources to trusted `context`.

- [ ] **Step 6: Add a real DB integration test**

Seed tenant/default Agent and stub model provider, run one adapter turn, then assert user mapping, stable DingTalk conversation ID, Graph trace, renderer output and persisted message Topic ID all agree.

- [ ] **Step 7: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/eval/test_dingtalk_adapter.py \
  tests/integration/eval/test_dingtalk_graph_eval_flow.py
git add eval/deepeval_dingtalk_adapter.py eval/deepeval_test_cases.py \
  tests/unit/eval/test_dingtalk_adapter.py \
  tests/integration/eval/test_dingtalk_graph_eval_flow.py
git commit -m "feat(eval): run deepeval through dingtalk online graph"
```

Expected: PASS.

---

### Task 3: Correct metric responsibilities, applicability, naming and thresholds

**Files:**
- Modify: `eval/deepeval_metrics.py`
- Modify: `eval/deepeval_test_cases.py`
- Create: `eval/metric_thresholds.yaml`
- Create: `tests/unit/eval/test_metric_applicability.py`

**Interfaces:**
- Consumes: `QuestionItem`, `DingTalkEvalResponse`, actual `LLMTestCase`.
- Produces: `get_metrics_for_case(question, response, judge_model=None) -> MetricPlan`; `ContextUtilizationMetric`; `MetricObservation`.

- [ ] **Step 1: Write failing applicability tests**

```python
def test_no_retrieval_skips_rag_metrics(question, response_without_sources):
    plan = get_metrics_for_case(question, response_without_sources, judge_model=FAKE_JUDGE)
    assert "Faithfulness" not in plan.metric_names
    assert "Contextual Relevancy" not in plan.metric_names
    assert "检索信息利用率 (Context Utilization)" not in plan.metric_names
    assert plan.not_applicable["Faithfulness"] == "no retrieval_context"


def test_hallucination_requires_trusted_context(question, response_with_sources):
    question.trusted_context = []
    plan = get_metrics_for_case(question, response_with_sources, judge_model=FAKE_JUDGE)
    assert "Hallucination" not in plan.metric_names


async def test_utilization_judge_error_is_not_perfect(metric, test_case):
    metric.model.a_generate.side_effect = RuntimeError("judge unavailable")
    with pytest.raises(RuntimeError, match="judge unavailable"):
        await metric.a_measure(test_case)
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/pytest -q tests/unit/eval/test_metric_applicability.py`

Expected: FAIL because the old factory only accepts `has_reference` and AnswerRecall converts failures to a full score.

- [ ] **Step 3: Rename and repair the custom metric**

```python
class ContextUtilizationMetric(BaseMetric):
    """Relevant retrieved facts covered by the final answer."""

    @property
    def __name__(self):
        return "检索信息利用率 (Context Utilization)"
```

Include user input when extracting key points so unrelated retrieved facts are excluded. Remove broad exception handlers; judge failures must surface. Do not instantiate the metric for empty retrieval context.

- [ ] **Step 4: Separate correctness, completeness and task completion**

Correctness judges only factual agreement/conflict with expected output; Completeness alone judges expected-point coverage. Replace trace-oriented `TaskCompletionMetric` with a GEval named `销售任务完成度 (Sales Task Completion)` over `INPUT` and `ACTUAL_OUTPUT`, judging whether the concrete request produced an actionable outcome.

- [ ] **Step 5: Implement explicit metric planning**

```python
@dataclass
class MetricPlan:
    metrics: list[BaseMetric]
    not_applicable: dict[str, str]

    @property
    def metric_names(self) -> list[str]:
        return [str(metric.__name__) for metric in self.metrics]


@dataclass
class MetricObservation:
    score: float | None
    threshold: float | None
    direction: str
    applicable: bool
    passed: bool | None
    reason: str = ""
    error: str | None = None
```

Rules:

- Always: Answer Relevancy, Sales Task Completion.
- With expected output: Correctness, Completeness.
- With actual retrieval context: Contextual Relevancy, Faithfulness, Context Utilization.
- With expected output and retrieval context: Contextual Recall, Contextual Precision.
- With trusted context: Hallucination; otherwise N/A.

- [ ] **Step 6: Add category-aware initial thresholds**

```yaml
default:
  correctness: 0.80
  completeness: 0.70
  faithfulness: 0.80
  answer_relevancy: 0.70
  contextual_relevancy: 0.60
  contextual_recall: 0.70
  contextual_precision: 0.65
  context_utilization: 0.50
  sales_task_completion: 0.70
  hallucination_max: 0.10
factual:
  correctness: 0.90
  faithfulness: 0.90
coaching:
  sales_task_completion: 0.75
```

- [ ] **Step 7: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/eval/test_metric_applicability.py
git add eval/deepeval_metrics.py eval/deepeval_test_cases.py \
  eval/metric_thresholds.yaml tests/unit/eval/test_metric_applicability.py
git commit -m "fix(eval): correct metric applicability and context utilization"
```

Expected: PASS; missing inputs and judge errors never increase score or pass rate.

---

### Task 4: Add deterministic Agent, evidence and safety metrics

**Files:**
- Create: `eval/deepeval_agentic_metrics.py`
- Modify: `eval/deepeval_test_cases.py`
- Create: `eval/datasets/agentic/single_turn_cases.jsonl`
- Create: `tests/unit/eval/test_agentic_metrics.py`

**Interfaces:**
- Consumes: extended `QuestionItem`, `DingTalkEvalResponse`.
- Produces: `AgenticExpectation`, `AgenticMetricResult`, `evaluate_agentic_turn()`, `aggregate_agentic_results()`.

- [ ] **Step 1: Write failing exact-metric tests**

```python
def test_required_retrieval_false_negative():
    expected = AgenticExpectation(knowledge_policy="required", should_retrieve=True)
    actual = DingTalkEvalResponse(knowledge_policy="required", needs_retrieval=False)
    result = evaluate_agentic_turn(expected, actual)
    assert result["required_retrieval"].passed is False
    assert result["required_retrieval"].reason == "required but retrieval was not called"


def test_citation_must_refer_to_actual_source():
    expected = AgenticExpectation(require_citations=True)
    actual = DingTalkEvalResponse(
        rendered_output="来源：不存在的文档",
        source_items=[{"title": "产品价格表"}],
    )
    assert evaluate_agentic_turn(expected, actual)["citation_validity"].passed is False
```

- [ ] **Step 2: Extend dataset contracts**

Add to `QuestionItem`:

```python
expected_turn_relation: str | None = None
expected_task_type: str | None = None
expected_knowledge_policy: str | None = None
should_retrieve: bool | None = None
should_abstain: bool | None = None
expected_source_titles: list[str] = field(default_factory=list)
require_citations: bool | None = None
expected_trace_nodes: list[str] = field(default_factory=list)
expected_risk_level: str | None = None
expected_risk_action: str | None = None
```

Retain `expected_keywords`. Parse all labels from JSON/JSONL without inventing defaults.

- [ ] **Step 3: Implement deterministic metrics**

Return nullable 0/1 results for turn relation, task type, knowledge policy, required/unnecessary retrieval, exact fact coverage, source-title coverage, citation validity, abstention, risk level/action and expected trace-node subset. Unlabeled fields are `applicable=False`, not pass.

- [ ] **Step 4: Implement aggregate rates**

```python
{
    "turn_relation_accuracy": correct / labeled,
    "knowledge_policy_accuracy": correct / labeled,
    "required_retrieval_false_negative_rate": required_missed / required_total,
    "unnecessary_retrieval_rate": unnecessary / no_retrieval_expected,
    "exact_fact_coverage": covered / expected_facts,
    "citation_validity_rate": valid / cited,
    "abstention_accuracy": correct / labeled,
    "risk_action_accuracy": correct / labeled,
    "trajectory_match_rate": matched / labeled,
}
```

- [ ] **Step 5: Add the first labeled single-turn dataset**

Include `direct-greeting`, `product-price-required`, `competitor-required`, `generic-coaching-direct`, `unsupported-product-claim`, `risk-price-commitment`, and `near-flow-phrase`, with explicit route/retrieval/evidence/risk labels.

- [ ] **Step 6: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/eval/test_agentic_metrics.py
git add eval/deepeval_agentic_metrics.py eval/deepeval_test_cases.py \
  eval/datasets/agentic/single_turn_cases.jsonl \
  tests/unit/eval/test_agentic_metrics.py
git commit -m "feat(eval): add deterministic agent and evidence metrics"
```

Expected: PASS with explicit denominators and no LLM calls.

---

### Task 5: Migrate single-turn, optimizer, pytest and risk runners

**Files:**
- Modify: `eval/deepeval_eval.py`
- Modify: `eval/deepeval_risk_eval.py`
- Modify: `eval/deepeval_optimize.py`
- Modify: `eval/deepeval_pytest_plugin.py`
- Modify: `tests/test_deepeval_agent.py`
- Modify: `tests/test_deepeval_risk.py`

**Interfaces:**
- Consumes: Tasks 2–4 adapters and metric planners.
- Produces: all single-turn commands on Online Graph and exact risk reports.

- [ ] **Step 1: Write failing runner-contract tests**

Patch `run_dingtalk_eval_turn()` and assert every runner uses it. Fail if `sales_agent.services.chat_pipeline` is imported. For risk, assert action mismatch fails even when the broad level is risky:

```python
response = DingTalkEvalResponse(risk_level="medium", risk_action="warn")
expected = {"expected_risk_level": "medium", "expected_action": "block"}
detail = classify_risk_case(expected, response)
assert detail["passed"] is False
assert detail["action_match"] is False
```

- [ ] **Step 2: Migrate the main single-turn runner**

Replace `call_agent_pipeline()` with `run_dingtalk_eval_turn()`. Build semantic metrics with `get_metrics_for_case(question, response)`, preserve N/A reasons, and merge `evaluate_agentic_turn()` into every result.

Add `semantic_metrics`, `agentic_metrics`, `not_applicable`, `trace_nodes`, `turn_relation`, `knowledge_policy`, `needs_retrieval`, and `topic_id` to the result contract.

- [ ] **Step 3: Migrate optimizer and pytest entrypoints**

Both files call the same adapter and metric planner as the CLI. Remove alternate paths that create old `AgentResponse` objects or query retrieval separately.

- [ ] **Step 4: Fix risk evaluation semantics**

Use risk level and action separately. Report per-action exact accuracy, per-category Recall/false negatives, backward-compatible block Recall/Precision/F1, and `critical_false_negative_count`. Exit non-zero when critical false negatives are non-zero.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/pytest -q tests/test_deepeval_agent.py tests/test_deepeval_risk.py tests/unit/eval
git add eval/deepeval_eval.py eval/deepeval_risk_eval.py \
  eval/deepeval_optimize.py eval/deepeval_pytest_plugin.py \
  tests/test_deepeval_agent.py tests/test_deepeval_risk.py
git commit -m "refactor(eval): migrate all runners to dingtalk graph adapter"
```

Expected: PASS and no eval import of `chat_pipeline`.

---

### Task 6: Add active multi-turn Topic Memory and Guided Flow evaluation

**Files:**
- Create: `eval/deepeval_multiturn_eval.py`
- Create: `eval/datasets/agentic/multi_turn_cases.jsonl`
- Delete: `eval/deepeval_conversation_eval.py`
- Create: `tests/unit/eval/test_multiturn_eval.py`

**Interfaces:**
- Consumes: `DingTalkEvalSession`, DeepEval `ConversationalTestCase`, deterministic metrics.
- Produces: `run_multiturn_scenario()` and Topic/Flow aggregate metrics.

- [ ] **Step 1: Define and test the scenario schema**

```python
class TurnExpectation(BaseModel):
    turn_relation: str | None = None
    same_topic_as_previous: bool | None = None
    retained_entities: list[str] = Field(default_factory=list)
    pending_clarification: bool | None = None
    response_kind: str | None = None
    active_flow: str | None = None
    flow_action: str | None = None
    knowledge_policy: str | None = None


class ScenarioTurn(BaseModel):
    input: str
    expected: TurnExpectation


class MultiTurnScenario(BaseModel):
    id: str
    turns: list[ScenarioTurn]
```

Set `extra="forbid"` on every model.

- [ ] **Step 2: Run each scenario through one real DingTalk identity**

Create one `DingTalkEvalSession` per scenario. Every turn reuses sender, internal user, conversation and Online Graph thread. Query persisted `ConversationTopic` only for assertions; never use DB state to synthesize model input.

- [ ] **Step 3: Add required scenarios**

1. `follow-up-pronoun`: 福多多产品 → “它多少钱？” → continue/same Topic/retain 福多多.
2. `in-turn-revision`: “找产品，算了还是找竞品” → retain entity/retract old goal.
3. `unrelated-new-topic`: product → generic coaching → new Topic/no old entity.
4. `ambiguous-continue`: ambiguous → “继续” → pending cleared/original query completed.
5. `ambiguous-new`: ambiguous → “新问题” → pending cleared/new Topic.
6. `small-win-preempted-by-previsit`: 小赢欣赏 → answer → 访前准备.
7. `block-preempted-by-postvisit`: 卡点破框 → answer → 访后复盘.
8. `near-phrase-no-trigger`: four non-exact phrases remain normal chat.
9. `cross-user-isolation`: two sessions cannot see each other's Topic/entity.

- [ ] **Step 4: Add conversational metrics only where applicable**

Build `ConversationalTestCase` from actual rendered DingTalk turns. Run Turn Relevancy for all scenarios, Turn Faithfulness only on retrieval-bearing turns, and Conversation Completeness only when expected outcome text exists. Topic, pending, entity and Flow expectations remain deterministic.

- [ ] **Step 5: Aggregate memory and flow metrics**

```text
turn_relation_accuracy
topic_continuity_accuracy
topic_switch_accuracy
entity_retention_accuracy
clarification_completion_rate
topic_leakage_rate
guided_flow_trigger_accuracy
guided_flow_preemption_rate
guided_flow_completion_rate
```

- [ ] **Step 6: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/eval/test_multiturn_eval.py
.venv/bin/python eval/deepeval_multiturn_eval.py --tenant-id taishan --limit 2
git add eval/deepeval_multiturn_eval.py \
  eval/datasets/agentic/multi_turn_cases.jsonl tests/unit/eval/test_multiturn_eval.py
git rm eval/deepeval_conversation_eval.py
git commit -m "feat(eval): add active topic memory and guided flow evaluation"
```

Expected: tests pass and smoke writes a report from actively executed Graph turns.

---

### Task 7: Version reports and expose applicability and diagnostic layers

**Files:**
- Modify: `eval/deepeval_eval.py`
- Modify: `eval/deepeval_html_report.py`
- Modify: `eval/deepeval_persistence.py`
- Create: `tests/unit/eval/test_report_schema_v2.py`

**Interfaces:**
- Consumes: Tasks 3–6 results.
- Produces: schema `sales-agent-eval/v2` in JSON, Markdown, CSV and HTML.

- [ ] **Step 1: Write a failing aggregation test**

```python
def test_average_excludes_not_applicable_and_errors():
    rows = [
        MetricObservation(score=0.9, applicable=True, error=None),
        MetricObservation(score=None, applicable=False, error=None),
        MetricObservation(score=None, applicable=True, error="judge timeout"),
    ]
    summary = aggregate_metric(rows)
    assert summary.average == 0.9
    assert summary.applicable_count == 2
    assert summary.scored_count == 1
    assert summary.error_count == 1
```

- [ ] **Step 2: Define report v2 sections**

```json
{
  "schema": "sales-agent-eval/v2",
  "rag": {},
  "answer_quality": {},
  "agent_routing": {},
  "topic_memory": {},
  "guided_flows": {},
  "safety": {},
  "operations": {},
  "details": []
}
```

Every metric contains score, threshold, direction, applicable, passed, reason and error.

- [ ] **Step 3: Add operational percentiles**

Report error rate, P50/P95 latency, P50/P95 TTFT, prompt/completion/total tokens and judge-call count. Stop presenting only average latency.

- [ ] **Step 4: Update projections and persistence**

Markdown/HTML group metrics and display N/A instead of zero. CSV includes applicability/error columns. Persistence stores schema version and deterministic metrics without flattening them into DeepEval scores.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/pytest -q tests/unit/eval/test_report_schema_v2.py
git add eval/deepeval_eval.py eval/deepeval_html_report.py \
  eval/deepeval_persistence.py tests/unit/eval/test_report_schema_v2.py
git commit -m "feat(eval): publish layered evaluation report v2"
```

Expected: PASS; averages exclude N/A and judge errors.

---

### Task 8: Replace old ChatPipeline integration coverage with Online Graph coverage

**Files:**
- Create: `tests/integration/test_ontology_chat_graph.py`
- Modify: `tests/integration/eval/test_dingtalk_graph_eval_flow.py`
- Delete: `tests/integration/test_ontology_chat_pipeline.py`
- Delete: `tests/integration/coach/test_coach_pipeline_integration.py`
- Delete: `tests/integration/coach/test_realtime_guidance.py`
- Delete: `tests/unit/coach/test_intent_router.py`
- Delete: `src/sales_agent/coach/intent_router.py`

**Interfaces:**
- Consumes: production Online Graph and DingTalk adapter.
- Produces: no test or runtime behavior tied to ChatPipeline.

- [ ] **Step 1: Port ontology coverage before deleting the old test**

Compile `build_online_graph()` with `InMemorySaver`, enable topic routing, inject fake ontology/RAG dependencies through runtime context, and assert final result contains ontology sources and main-generation output. Exercise Online Graph → Chat Graph → retrieval → generation; do not monkeypatch ChatPipeline.

- [ ] **Step 2: Characterize old-only coach behavior through production entry**

Add `test_legacy_coach_phrases_do_not_use_dead_interceptor` to `test_dingtalk_graph_eval_flow.py`. Send “我的评分” and “教练报告” through `DingTalkEvalSession`; assert a Graph trace exists and no `CoachReportRequest` row is created. This freezes current production behavior without porting old unreachable expectations.

- [ ] **Step 3: Remove old-only coach interception tests and module**

Delete the two ChatPipeline coach integration files and `coach/intent_router.py` plus its unit test. Keep coach APIs, daily evaluator, profiles, scores, Guided Flow handlers and every module with a non-ChatPipeline caller.

- [ ] **Step 4: Verify retained coach tests**

```bash
.venv/bin/pytest -q tests/unit/coach tests/integration/coach \
  --ignore=tests/integration/coach/test_coach_pipeline_integration.py \
  --ignore=tests/integration/coach/test_realtime_guidance.py
```

Expected: all remaining coach tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_ontology_chat_graph.py \
  tests/integration/eval/test_dingtalk_graph_eval_flow.py
git rm tests/integration/test_ontology_chat_pipeline.py \
  tests/integration/coach/test_coach_pipeline_integration.py \
  tests/integration/coach/test_realtime_guidance.py \
  tests/unit/coach/test_intent_router.py src/sales_agent/coach/intent_router.py
git commit -m "test: replace legacy pipeline coverage with online graph"
```

---

### Task 9: Delete ChatPipeline and its exclusive infrastructure

**Files:**
- Delete: `src/sales_agent/services/chat_pipeline.py`
- Delete: `src/sales_agent/services/path_router.py`
- Delete: `src/sales_agent/services/latency_tracker.py`
- Delete: `tests/unit/test_processing_notice.py`
- Delete: `tests/unit/test_path_router.py`
- Delete: `tests/unit/test_latency_tracker.py`
- Modify: `src/sales_agent/core/config.py`
- Modify: `config/default.yaml`

**Interfaces:**
- Consumes: all callers migrated in Tasks 2, 5 and 8.
- Produces: zero runtime/test imports of `sales_agent.services.chat_pipeline`.

- [ ] **Step 1: Prove all callers are gone**

```bash
grep -R "from sales_agent.services.chat_pipeline\|ChatPipeline(" -n \
  src eval tests --exclude-dir=__pycache__ --exclude='*.pyc'
```

Expected: no output. Do not delete the file until this is true.

- [ ] **Step 2: Delete implementation and exclusive tests**

```bash
git rm src/sales_agent/services/chat_pipeline.py \
  src/sales_agent/services/path_router.py \
  src/sales_agent/services/latency_tracker.py \
  tests/unit/test_processing_notice.py \
  tests/unit/test_path_router.py \
  tests/unit/test_latency_tracker.py
```

Do not delete `agent_executor.py`, `retriever.py`, `risk_checker.py`, `request_validator.py`, `response_formatter.py`, `latency_stats.py`, or `run_tracer.py`; they have non-ChatPipeline callers or public endpoints.

- [ ] **Step 3: Remove exclusive configuration**

Delete `LatencyConfig` and `Settings.latency`. From `PathRouterConfig`, remove `enable_fast_path`, `enable_slow_path_notice`, and `clarify_confidence_threshold`. Retain Graph router configuration:

```python
class PathRouterConfig(BaseModel):
    """Chat Graph task-router LLM fallback configuration."""
    enable_llm_router: bool = False
    llm_router_confidence_threshold: float = 0.75
```

Remove the `latency:` YAML block and obsolete PathRouter fields from `config/default.yaml`.

- [ ] **Step 4: Verify import and config health**

```bash
.venv/bin/python -m compileall -q src eval
.venv/bin/python - <<'PY'
from sales_agent.core.config import get_settings
from sales_agent.graph.chat_graph import build_chat_graph
from sales_agent.graph.online_graph import build_online_graph
s = get_settings()
assert hasattr(s.path_router, "enable_llm_router")
assert not hasattr(s, "latency")
build_chat_graph().compile()
build_online_graph().compile()
print("graph imports OK")
PY
```

Expected: `graph imports OK`.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/core/config.py config/default.yaml
git commit -m "refactor: delete deprecated chat pipeline"
```

---

### Task 10: Clean active terminology, CLI commands and operator documentation

**Files:**
- Modify: `eval/deepeval_run.sh`
- Modify: `eval/README_DEEPEVAL.md`
- Modify: `README.md`
- Modify: `src/sales_agent/services/run_tracer.py`
- Modify: `src/sales_agent/models/agent_run.py`
- Modify: `src/sales_agent/integrations/dingtalk/media_adapter.py`
- Modify: current comments under `src/sales_agent/graph/`
- Delete: `tasks/todo_eval_migrate_to_graph.md`

**Interfaces:**
- Consumes: completed migration.
- Produces: current docs/code refer to Online Graph / Chat Graph, not ChatPipeline.

- [ ] **Step 1: Update the one-command runner**

Describe the real DingTalk Graph path. Preserve `smoke`, `eval`, `compare`, `golden`, and `risk`; add:

```text
agentic <tenant-id> [limit]
multiturn <tenant-id> [limit]
```

`agentic` loads the labeled single-turn JSONL. `multiturn` invokes `deepeval_multiturn_eval.py`.

- [ ] **Step 2: Update current architecture terminology**

Use exact terms `Online Conversation Graph`, `Chat Graph`, and `DingTalk business flow`. Replace active comments such as “ChatPipeline graph”, “delegates to ChatPipeline”, and “Convert media for ChatPipeline”. Do not edit archived plans or changelog prose.

- [ ] **Step 3: Update DeepEval documentation**

Document the actual call chain, semantic versus deterministic metrics, Context Utilization rename, N/A/error semantics, trusted-context Hallucination rule, commands, report v2 groups, threshold file, and why schema-v2 scores are not numerically comparable to reports from the retired pipeline.

- [ ] **Step 4: Remove completed todo and verify active-text cleanup**

```bash
git rm tasks/todo_eval_migrate_to_graph.md
grep -R "ChatPipeline" -n src eval tests config/default.yaml \
  --exclude-dir=__pycache__ --exclude-dir=results --exclude-dir=datasets \
  --exclude='*.pyc'
```

Expected: no output. Historical plans, changelogs, archived tasks and lessons may retain old descriptions.

- [ ] **Step 5: Commit**

```bash
git add eval/deepeval_run.sh eval/README_DEEPEVAL.md README.md \
  src/sales_agent/services/run_tracer.py src/sales_agent/models/agent_run.py \
  src/sales_agent/integrations/dingtalk/media_adapter.py src/sales_agent/graph
git commit -m "docs: align eval and runtime terminology with online graph"
```

---

### Task 11: Calibrate thresholds and establish the new Graph baseline

**Files:**
- Modify: `eval/metric_thresholds.yaml`
- Create: `eval/datasets/agentic/judge_calibration.jsonl`
- Create: `eval/results/baselines/README.md`
- Create: `tests/unit/eval/test_threshold_calibration.py`

**Interfaces:**
- Consumes: report v2 and configured judge.
- Produces: calibrated thresholds and a versioned baseline protocol, not generated result artifacts.

- [ ] **Step 1: Create a human-labeled calibration set**

Include at least 30 examples balanced across fully correct, correct-but-incomplete, relevant-but-unsupported, faithful-to-wrong-context, concise-complete, verbose-irrelevant, correct refusal and incorrect refusal. Each row carries human 0–1 labels for correctness, completeness, faithfulness, relevancy and task completion.

- [ ] **Step 2: Run the configured judge three times**

For every example and metric, record mean, standard deviation and pass/fail agreement against human labels. Do not reuse the production-answer model as judge when another judge is configured.

- [ ] **Step 3: Tune thresholds by failure cost**

Select values meeting:

- factual correctness false-pass rate ≤ 2%;
- faithfulness false-pass rate ≤ 2%;
- risk critical false negatives = 0;
- coaching task-completion human agreement ≥ 85%;
- judge run-to-run pass/fail agreement ≥ 90%.

Update YAML comments with calibration date, judge model and dataset version.

- [ ] **Step 4: Test decision direction and category override**

Verify Hallucination uses `score <= threshold`; all other semantic metrics use `score >= threshold`. Verify category values override defaults without deleting unrelated default keys.

- [ ] **Step 5: Document baseline policy and commit**

Old ChatPipeline reports remain historical. The first passing report from the DingTalk Graph path becomes baseline v2. Do not commit keys, raw production conversations, full generated reports or customer data.

```bash
.venv/bin/pytest -q tests/unit/eval/test_threshold_calibration.py
git add eval/metric_thresholds.yaml eval/datasets/agentic/judge_calibration.jsonl \
  eval/results/baselines/README.md tests/unit/eval/test_threshold_calibration.py
git commit -m "test(eval): calibrate graph evaluation thresholds"
```

---

### Task 12: Full verification and deletion proof

**Files:**
- Verify all files changed by Tasks 1–11.
- Update current documentation only if observed commands differ.

**Interfaces:**
- Consumes: complete implementation.
- Produces: evidence that production and eval share the DingTalk Graph path and no old pipeline remains.

- [ ] **Step 1: Prove ChatPipeline is absent**

```bash
test ! -e src/sales_agent/services/chat_pipeline.py
grep -R "from sales_agent.services.chat_pipeline\|ChatPipeline(" -n \
  src eval tests --exclude-dir=__pycache__ --exclude='*.pyc' && exit 1 || true
```

Expected: file absent and grep empty.

- [ ] **Step 2: Run deterministic and unit suites**

Run: `.venv/bin/pytest -q tests/unit tests/unit/eval`

Expected: PASS.

- [ ] **Step 3: Run DB/Graph integration suites**

```bash
RUN_INTEGRATION_TESTS=1 .venv/bin/pytest -q \
  tests/integration/eval/test_dingtalk_graph_eval_flow.py \
  tests/integration/test_ontology_chat_graph.py \
  tests/integration/test_topic_memory_flow.py \
  tests/integration/coach
```

Expected: PASS with no deleted-pipeline imports or fixtures.

- [ ] **Step 4: Run a single-turn Graph smoke**

Run: `bash eval/deepeval_run.sh smoke taishan 5`

Expected: five questions traverse DingTalk processor and report actual turn relation, knowledge policy, sources and trace nodes.

- [ ] **Step 5: Run labeled Agent and multi-turn smoke**

```bash
bash eval/deepeval_run.sh agentic taishan 7
bash eval/deepeval_run.sh multiturn taishan 3
```

Expected: deterministic routing/evidence and Topic/Flow metrics have non-zero applicable denominators; no cross-question memory leakage.

- [ ] **Step 6: Run risk and comparison smoke**

```bash
bash eval/deepeval_run.sh risk taishan
bash eval/deepeval_run.sh compare taishan taishankaifa2 泰山 泰山开发 all 5
```

Expected: risk report includes exact action accuracy and zero critical false negatives; Arena uses final rendered DingTalk outputs.

- [ ] **Step 7: Inspect evidence integrity**

For one ontology and one legacy/hybrid case, compare report retrieval context with Graph-returned source items. They must match after documented truncation, and logs must show no second retrieval query.

- [ ] **Step 8: Verify N/A and error behavior**

Run one direct greeting and one forced judge-timeout case. Greeting shows RAG-only metrics as N/A. Judge timeout increments metric errors and does not raise averages or pass rates.

- [ ] **Step 9: Check final repository state**

```bash
git status --short
git log --oneline --max-count=12
```

Expected: clean worktree and one reviewable commit per task.

---

## Completion criteria

- All DeepEval, optimizer, pytest and risk paths run through the shared DingTalk processor and unified Online Graph.
- Evaluation performs real user mapping, stable conversation mapping, command parsing, Agent resolution, Graph routing/retrieval/risk and DingTalk rendering.
- Retrieval context is exactly the evidence used to generate the answer; post-hoc ontology fetching no longer exists.
- `ContextUtilizationMetric` replaces AnswerRecall and never rewards missing inputs or judge failures.
- Hallucination runs only with trusted context; metric applicability is explicit.
- Semantic reports cover answer/RAG quality; deterministic reports cover routing, evidence, Topic, Flow, citations, abstention and risk actions.
- Multi-turn scenarios actively execute the system instead of only sampling historical rows.
- `src/sales_agent/services/chat_pipeline.py` and its exclusive infrastructure/tests are deleted.
- Active source and eval code contain no `ChatPipeline` reference; README 当前架构说明不再把它描述为现行链路，历史 changelog 保持原文。
- Graph-based report schema v2 is calibrated and becomes the only forward baseline.
