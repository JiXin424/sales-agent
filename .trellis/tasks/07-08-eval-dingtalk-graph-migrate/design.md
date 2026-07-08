# Design: DeepEval 迁移到钉钉生产链路并删除 ChatPipeline

## 架构与边界

### 改动范围（3 个区域）

```
区域 A: 生产 graph 代码（补全返回信息，不改行为）
  - graph/online/state.py       OnlineConversationState 加字段
  - graph/online/nodes.py       chat_node 透传字段
  - graph/chat/nodes/retrieval.py  ontology sources 补 text

区域 B: eval 代码（换调用入口）
  - eval/deepeval_test_cases.py  call_agent_pipeline 改调 invoke_online_turn
                                   删 _fetch_ontology_sources/_is_ontology_engine

区域 C: 删除死代码
  - services/chat_pipeline.py
  - services/latency_tracker.py
  - services/run_tracer.py
  - services/path_router.py (PathRouter 类; PathRouterConfig 保留在 config.py)
  - tests/unit/test_processing_notice.py
  - tests/unit/test_latency_tracker.py
  - tests/unit/test_run_tracer.py
  - tests/unit/test_path_router.py
  - tests/integration/coach/test_coach_pipeline_integration.py
  - tests/integration/coach/test_realtime_guidance.py
```

### 不改动（确认保留）
- `services/agent_executor.py` — graph generation node + guided_flow + cli 在用
- `services/task_router.py` — graph routing node 在用，内部函数 `_match_rules`/`_resolve_priority`/`TASK_DEFAULT_RETRIEVAL` 自用
- `core/config.py` 的 `PathRouterConfig` — routing node + task_router 引用
- `graph/chat/edges.py` / `fast_commands.py` / `path_router.py` 各自的 `_HELP_COMMANDS`/`_RESET_COMMANDS` 副本

---

## 数据流与契约

### 1. chat_node 透传字段

**现状**：`chat_node` 调 Chat Graph 的 `ainvoke`，拿到完整 `ChatGraphState` 末态，但只 return 3 个字段给 OnlineConversationState。

**改动**：`graph/online/nodes.py` 的 `chat_node` return 补全：

```python
return {
    "answer_dict": result.get("answer_dict") or result.get("final_answer", {}),
    "response_kind": "chat",
    "last_event_id": state.get("event_id"),
    # ── 新增透传（eval + 未来消费者需要）──
    "sources": result.get("sources", []),
    "risk_result": result.get("risk_result", {}),
    "usage": result.get("usage", {}),
    "route_confidence": result.get("route_confidence"),
    "path": result.get("path"),
    "task_type": result.get("task_type"),
}
```

**OnlineConversationState 新增字段**（`graph/online/state.py`）：

```python
# ── Output (补全) ───────────────────────────────────────────────
sources: list[dict]
risk_result: dict[str, Any]
usage: dict[str, int]
route_confidence: float | None
path: str | None
```

**注意**：`task_type` 已在 OnlineConversationState（evidence_routing 写入），但 chat_node 的 return 会覆盖为 ChatGraphState 末态的 task_type（可能被 LLM router 修正过）。这是正确的——最终 task_type 应以 chat graph 末态为准。

**为什么属"不改行为"**：这些字段本来就在 ChatGraphState 里被计算了，只是没回传。钉钉 processor 当前从 `answer_dict` 里拆 sources/risk_result（`processor.py:140-143`），透传后 processor 也能直接从 state 取，但本次不改 processor（避免扩大范围）。

### 2. retrieve_node ontology sources 补 text

**现状**：`graph/chat/nodes/retrieval.py` ontology 路径构建的 sources 只有 title：
```python
sources = [{"document_id": "", "title": title, "display_title": title, ...} for title in ...]
```

**改动**：给每个 source 补 `text` 字段，值用 `ontology_context_text`（完整实体+事实文本）：

```python
sources = [
    {
        "document_id": "",
        "title": title,
        "display_title": title,
        "text": ontology_context_text,  # 新增：完整检索上下文
        "score": compacted.get("confidence", 0.8),
        "source_type": "ontology",
    }
    for title in compacted.get("source_documents", [])[:3]
]
```

**影响**：
- eval 的 `_extract_sources` 优先取 `text` 字段 → 直接拿到完整事实文本做 retrieval_context
- 钉钉 renderer 仍用 `title`/`display_title` → 不影响文末引用
- `_fetch_ontology_sources` 补丁可删

**边界**：如果 `source_documents` 为空（无实体无事实），sources 仍为空列表，eval 的 retrieval_context 为空 → FaithfulnessMetric 会跳过（已有空 context 处理逻辑）。

### 3. call_agent_pipeline 改调 invoke_online_turn

**现状**：
```python
from sales_agent.services.chat_pipeline import ChatPipeline
pipeline = ChatPipeline(db, settings)
result = await pipeline.execute(tenant_id=..., reply_fn=reply_fn, ...)
# 从 PipelineResult 取 timings/fast_reply/route_result/sources/risk_result/usage
```

**改动后**：
```python
from sales_agent.services.online_conversation import invoke_online_turn

t_start = time.monotonic()
result = await invoke_online_turn(
    db=db,
    tenant_id=tenant_id,
    agent_id=agent_id,
    user_id="deepeval_eval",
    session_user_id="deepeval_eval",   # eval 不需要真实钉钉 staff_id
    channel="dingtalk",                # 模拟钉钉渠道
    conversation_id=f"eval_{generate_id()}",
    message=question.text,
    event_id=f"eval_{generate_id()}",  # 去重 ID
    chat_model=model_override,         # 可选模型覆盖
)
latency_ms = int((time.monotonic() - t_start) * 1000)

# 从 Online State 末态取字段
answer_dict = result.get("answer_dict") or {}
sources = result.get("sources", [])
risk_result = result.get("risk_result", {})
usage = result.get("usage", {})
task_type = result.get("task_type", "")
```

**字段映射**：

| AgentResponse 字段 | 旧来源 (PipelineResult) | 新来源 (Online State) |
|---|---|---|
| answer_text | answer_dict → _extract_answer_text | answer_dict → _extract_answer_text（不变） |
| rendered_output | renderer.render(answer_dict, sources, risk_result) | 同上（不变） |
| summary / sections | answer_dict.get("summary"/"sections") | 同上（不变） |
| sources | result.sources → _extract_sources | result["sources"] → _extract_sources |
| task_type | result.route_result.task_type | result["task_type"] |
| risk_level | result.risk_result.level | result["risk_result"].get("level") |
| risk_flags | result.risk_result.flags | result["risk_result"].get("flags", []) |
| latency_ms | result.timings.total_ms | wall-clock (time.monotonic) |
| ttft_ms | 逐阶段累加 | 0（弃用，报告显示"—"） |
| prompt_tokens | result.usage["prompt_tokens"] | result["usage"].get("prompt_tokens", 0) |
| completion_tokens | result.usage["completion_tokens"] | result["usage"].get("completion_tokens", 0) |
| total_tokens | result.usage["total_tokens"] | result["usage"].get("total_tokens", 0) |

**reply_fn 处理**：`invoke_online_turn` 不接受 `reply_fn` 参数（钉钉 processor 在调 graph 后自己渲染回复）。eval 不需要 reply_fn（它直接从返回值取 answer_dict 自己渲染），所以 `captured_replies` 逻辑可删。

**model 覆盖**：旧代码 `pipeline.execute(model=model)` 支持 eval 切模型测对比。`invoke_online_turn` 接受 `chat_model` 参数，但需要传 ChatModel 实例而非模型名字符串。需在 eval 侧用 TenantResolver 构建指定模型的 ChatModel 实例传入。如果 `model=None` 则 `invoke_online_turn` 内部自动 resolve。

**删除**：
- `_fetch_ontology_sources` 函数（graph sources 已有 text）
- `_is_ontology_engine` 函数
- `_MAX_SOURCE_CHARS` 常量（仅 _fetch_ontology_sources 用）
- ontology 兜底调用块

---

## 兼容性与迁移注意

### invoke_online_turn 会写对话日志
`invoke_online_turn` → chat graph 的 `log_node` 会调 `conversation_logger.log_conversation` 写 DB。旧 ChatPipeline 也写日志。行为一致，但 eval 会产生大量 `user_id="deepeval_eval"` 的对话记录——旧代码也这样，不是新问题。

### thread_id 隔离
`invoke_online_turn` 用 `build_online_thread_id` 生成日期作用域 thread_id（`online:<tenant>:<agent>:dingtalk:<session_user_id>:<YYYY-MM-DD>`）。eval 每题用不同 `conversation_id`，但 `session_user_id="deepeval_eval"` 固定 → 同一天的多题会共享 thread_id，上下文会累积。

**对策**：每题用唯一 `session_user_id`（如 `f"deepeval_eval_{question.id}"`），确保 thread_id 隔离、上下文不串。这比旧 ChatPipeline（每题独立 conversation_id 无状态累积）更接近真实钉钉行为（钉钉用户也是按 session 隔离）。

### guided_flows / 去重
`invoke_online_turn` 会检查 `guided_flows_enabled` 和 `event_id` 去重。eval 传唯一 `event_id` 避免去重误判。guided_flows 默认关闭时不影响。

### risk_result.to_dict() vs RiskCheckResult
旧代码 `result.risk_result` 是 `RiskCheckResult` 对象（有 `.level`/`.flags` 属性）。新代码 `result["risk_result"]` 是 `result.to_dict()` 的字典。需确认 dict 的 key 名。

### coach 拦截功能丢失
coach 报告意图拦截（`CoachIntentRouter`）只在 chat_pipeline 接入，graph 路径未接入。删除 chat_pipeline 后该功能彻底消失。这是**已知独立遗留问题**，不在本任务范围。`coach/intent_router.py` 模块本身保留（不删定义），仅断开 chat_pipeline 的调用。

---

## 风险与回滚

### 风险
1. **Online State 字段新增可能影响 checkpoint 序列化**：新字段加在 `total=False` 的 TypedDict 里，旧 checkpoint 不含这些字段，`result.get()` 返回 None/默认值 → 向后兼容。
2. **eval 指标可能系统性偏离**：graph 路径与 ChatPipeline 行为有细微差异（如 context_resolution 会改写 standalone_query 作为 message）。用户已明确接受此风险。
3. **model 覆盖路径**：如果 eval 的 `--model` 参数依赖构建 ChatModel，需确认 TenantResolver 的接口。

### 回滚
改动集中在 3 个区域，git revert 即可。删除的文件可从 git 历史恢复。无 DB migration、无配置变更。
