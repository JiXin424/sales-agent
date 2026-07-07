# Design — prompt 死代码清理 + 补注册 + web 默认启用

## 改动逐项方案

### 1. 删 maybe_update_summary
- 位置：`services/context_loader.py:177-280`（async def，含内联 `summary_prompt` :221-233）。
- 确认零调用：全仓 `grep maybe_update_summary` 仅定义处。
- 删除后清理独占 import：函数用 re/json/ConversationSummary/ConversationMessage/select/get_settings/ChatModel/AsyncSession/logger —— implementer 删除前对每个符号在文件内 grep 出现次数，仅此函数用的才删。

### 2. 删 image_to_text
- 位置：`ontology/img_parser.py:73-165`。
- 确认零外部调用：`ingestion_service` 用的是 `_image_to_text_via_vision`（ingestion_service.py 内部定义，不依赖 image_to_text）；`IMAGE_INTERPRET_PROMPT` 被 ingestion_service 直接 import。
- 保留：`IMAGE_INTERPRET_PROMPT`（:44）、`is_image_file`、`get_image_mime_type`、`SUPPORTED_IMAGE_EXTENSIONS`、`_HEIF_EXTENSIONS`。
- 清理独占 import：`base64`（仅 image_to_text 用）→ 删；`Any`（仅签名）→ 删；`logging`/`Path` 保留。

### 3. web_search 默认 True
- `core/config.py:142` `WebSearchConfig.enabled: bool = False` → `True`。
- `default.yaml` 无 `web_search` 段（grep 确认），用代码默认 → 只改代码。
- 安全性：`graph/retrieval/web_fallback.py:40` `if not api_key: return None` —— 无 BOCHA_API_KEY 时优雅跳过，不崩。retrieve_node 调用条件 `not sources and settings.web_search.enabled`，enabled=True + 无 key → 进 web_fallback → return None → 当作无 web 结果。微开销可接受。
- `config.py:353-356`「BOCHA_API_KEY 自动置 True」逻辑保留（幂等）。

### 4. 补注册 resolver（层次 1）
- `services/prompt_defaults.py` `_system_router_risk_entries()` 加：
  - `from sales_agent.prompts.context_resolver_prompt import CONTEXT_RESOLVER_PROMPT`
  - `from sales_agent.prompts.clarification_resolver_prompt import CLARIFICATION_RESOLVER_PROMPT`
  - `BuiltinPrompt("router", "context_resolver", CONTEXT_RESOLVER_PROMPT, (), "上下文消解（话语-话题关系）")`
  - `BuiltinPrompt("router", "clarification_resolver", CLARIFICATION_RESOLVER_PROMPT, (), "澄清回复决策")`
- category=router（与 evidence_router/task_router 同类）。`required_placeholders=()`（system prompt 无 `.format` 占位符；user content 是 f-string 拼）。
- **运行时保持硬编码**（不改 resolve_context / resolve_clarification 调用方）。

### 5. ChatPipeline deprecation + eval 迁移 todo
- `services/chat_pipeline.py` 类 docstring 强化：「生产零调用；仅 eval/deepeval（`eval/deepeval_*.py`）依赖；待 eval 迁移到 `invoke_online_turn` 后整文件删除」。
- `tasks/todo_eval_migrate_to_graph.md`：记录 eval 迁移工作（独立立项，本次不执行）。

## 关键设计决策

### 决策 1：做「层次 2」——注册 + 运行时走 PromptRegistry（用户选定）
- **现状**：evidence_router 虽在 BUILTIN_PROMPTS，但 `route_intent_evidence`（evidence_router.py:126）硬编码用 `EVIDENCE_ROUTER_PROMPT` 常量，调用方没传 db → 运行时不读 DB 版本（「注册了但半残」）；context/clarification resolver 连注册都没有。
- **本次（层次 2）**：三个 router prompt 都注册 + 运行时走 `PromptRegistry.resolve_prompt`，让后台编辑生效。
- **可行性（research/router-prompt-resolve-callers.md 已确认）**：
  - 三个 service（`resolve_context` / `resolve_clarification` / `route_intent_evidence`）**只被 2 个 graph 节点调用**（context_resolution_node、evidence_routing_node）+ 30 个单测点；`chat_pipeline.py`/`cli.py`/集成测试**零调用** → 加参数**不破坏 eval**（lessons #31 风险消除）。
  - `tenant_id/agent_id` 在 graph STATE（不在 runtime ctx），`db` 在 runtime ctx。`context_resolution_node` 已从 state 取 ids（:206-207）；**`evidence_routing_node` 需补取 ids + db**。
  - 30 个单测都不传 db → 新参数 `db/tenant_id/agent_id` 一律默认 None，None 时内部回退硬编码常量（参照 `risk_check_node:96-110` / `resolve_quick_session_prompts:65-69`）→ **单测零改动、向后兼容**。
  - `resolve_prompt(category, key, tenant_id, agent_id=None)` 对未注册 `(category,key)` **抛 ValueError** → 必须先补注册 context_resolver / clarification_resolver（evidence_router 已注册）。
  - 三个 prompt 模板都无 `{placeholder}` → resolve 后不需 `.format` → 避开 lessons #30 字面花括号陷阱。
- **实现模式**：新增 helper `resolve_router_prompt(db, key, tenant_id, agent_id, *, default)`（加到 prompt_resolver_helper.py）：db 为空或 resolve 抛错时返回 default 常量。三个 service 加可选 `db=None, tenant_id=None, agent_id=None`，用 helper 取 prompt。graph 节点从 state 取 ids、ctx 取 db 传入。
- **生产影响**：topic_routing 默认 OFF → 两个节点默认不进 → 生产零行为变化；仅在 topic_routing 开启 + DB 有自定义版本时才生效（这正是想要的）。

### 决策 2：ChatPipeline 保留
- 生产零调用（HTTP 走 `_execute_via_graph`，钉钉走 `invoke_online_turn`），但 `eval/deepeval_*.py` 依赖它（lessons #31 早指出 eval 走 ChatPipeline 老路径）。
- 删除 = 摧毁 eval。迁移 eval 到 `invoke_online_turn` 是独立大工作（用户已确认独立立项）。

### 决策 3：web 默认 True 安全
- 无 key 时 web_fallback return None（已验证 web_fallback.py:40），不崩。

## 影响面

- **前端**：prompt 编辑器列表从 `api/routes/prompts.py` 读 BUILTIN_PROMPTS → 补注册后自动出现 context_resolver/clarification_resolver，无需前端改（implementer 确认 prompts.py 列出逻辑）。
- **DB**：补注册是代码常量层，无 schema 变更，**不需要 migration/seed**。PromptRegistry 三级回退（Agent 绑定 → tenant active → BUILTIN_PROMPTS）最后一层读代码常量。
- **配置**：web_search 默认值变更，已有部署若 yaml/env 显式设了 `web_search.enabled` 不受影响（只改默认）。

## 风险与回滚

- 风险低：删的都是零调用代码；补注册纯追加；web 默认值有无 key 兜底。
- 回滚：`git revert`，无 DB 不可逆操作。
