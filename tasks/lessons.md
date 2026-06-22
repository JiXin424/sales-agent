# Lessons（教训记录）

> 每次纠正后更新。记录模式，防止重复犯错。

## 1. `str.format` 双花括号是转义，不是占位符
- **场景**：prompt 模板用户输入区写了 `{{message}}`，executor 用 `template.format(message=...)` 填充。
- **教训**：Python `str.format` 中 `{{` → 字面 `{`，`{{message}}` **不会**被替换，会原样输出
  `{message}`。JSON 示例区用双花括号是对的（要输出字面 JSON 结构），但**变量占位符必须用单花括号**
  `{message}`。
- **检查方法**：`string.Formatter().parse()` 对 `{{message}}` 也识别为含字段 `message`，所以
  `"{message}" in prompt` 这种子串校验**无法发现此 bug**。必须对 `.format()` **渲染后的结果**做断言
  （值是否真的注入）。见 `tests/unit/test_visit_post_visit_placeholders.py`。

## 2. SQLAlchemy：先 flush 子对象再设外键，否则外键可能丢失
- **场景**：测试里 `db.add(prompt_set)` 后立即 `agent.prompt_set_id = prompt_set.id`，一次 `flush`，
  结果 `agent.prompt_set_id` 没持久化（读回为 `None`）。
- **教训**：新增子对象（如 `AgentPromptSet`）并绑定到父对象外键时，**先 `flush` 子对象拿到稳定 id，
  再设父外键，再 `flush`**。`add + 赋值 + 一次 flush` 的组合会导致外键丢失（与 identity map / dirty
  追踪时序有关）。
- **正确范式**（见 `test_two_agents_different_prompt_versions`）：
  ```python
  db.add(ps); await db.flush()      # 先持久化 ps
  agent.prompt_set_id = ps.id
  await db.flush()                  # 再持久化外键
  ```
- **排查手段**：`_resolve_agent_prompt_version` 返回 None 时，打印 `agent.prompt_set_id` 确认是否持久化。

## 3. `create_all` 不处理已有表的加列，必须用 Alembic
- **场景**：项目用 `Base.metadata.create_all` 建表，改模型加列后，已有库不会自动加列。
- **教训**：生产 DB schema 变更**必须走 Alembic migration**（CLAUDE.md 强制要求）。`create_all` 只建
  新表，不改已有表结构。
- **baseline 策略**：对已有生产库用 `alembic stamp head` 标记当前状态（不执行 DDL），再 `alembic
  upgrade head` 跑增量 migration。新库可直接 `upgrade head`（建表仍由 create_all 完成）。

## 4. 解耦改造的"接入面"必须逐链路核对
- **场景**：prompt 解耦第一阶段只接了主 Web 链路，钉钉流式 + CLI 绕过，导致运营改后台对生产主渠道
  不生效——半成品。
- **教训**：解耦类改造要**列出所有调用点**（grep 函数名），逐个确认是否接入新路径，不能只改主链路。
  本次用子代理梳理出 4 个调用点（chat_pipeline / streaming_handler / cli×2）+ router/risk/coach。

## 5. 测试用 `_make_agent` 而非 `ensure_default_agent_for_tenant` 建 Agent
- **场景**：`ensure_default_agent_for_tenant` 会自己创建 prompt_set 并绑定 agent，测试中覆写
  `prompt_set_id` 时行为异常（配合 lessons #2 的 flush 时序问题）。
- **教训**：需要精确控制 agent 的 prompt_set 绑定的测试，用 `AgentService.create_agent`（不预绑
  prompt_set）+ 手动建 set + 设外键，可控性更好。
