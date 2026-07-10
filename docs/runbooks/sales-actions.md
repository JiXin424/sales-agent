# Runbook: 销售动作卡片与提醒（Sales Action Cards）

钉钉单聊内的销售任务卡片、一次性提醒、每日 digest、按钮回调，以及后台调度投递。本运行手册覆盖开关、排障、SQL 巡检、安全重放与回滚。

## 功能开关

- `settings.sales_actions.enabled`（默认 `False`）：总开关。关闭时 Online Graph 不路由销售动作、worker 不启动调度器。
- `settings.sales_actions.scheduler_enabled`（默认 `True`）：仅在 `enabled=True` 时生效，控制后台调度 loop 是否随 worker 启动。
- 其余默认：`scan_interval_seconds=30`、`batch_size=50`、`max_attempts=5`、`default_timezone="Asia/Shanghai"`、`morning_digest_time="09:00"`、`evening_digest_time="18:30"`、`default_snooze_minutes=30`、`expire_after_days=7`、`llm_confidence_threshold=0.75`。

启用：在租户/实例配置里设 `sales_actions.enabled=true`，重启 worker（`<tenant>-stream` / worker 容器）。**验证首选查 stream 容器日志**确认调度 loop 起来且无 crash（本项目生产主入口是钉钉 Stream）：

```bash
docker logs <tenant>-stream --tail 100 | grep -iE "sales.action|scheduler"
```

## 架构速览

- **理解层**（Task 2）：`detect_fast_action_intent`（正则快路由）+ `parse_sales_action_request`（LLM 抽取）+ `validate_action_extraction`（确定性校验：低置信/缺时间/缺动作/过去时间 -> 澄清）。
- **状态机**（Task 3）：`SalesActionRepository` 的 `create/complete/cancel/snooze/list` + `claim_due_reminders`（`FOR UPDATE SKIP LOCKED`，含失败重试）。幂等键：`one_time:{t}:{a}:{u}:{action_id}:{scheduled_at}`、`snooze:{action_id}:{event_id}:{new_time}`。
- **Graph 接线**（Task 4）：`sales_action_command_node` 路由优先级在 guided flow 之后、普通 chat 之前；澄清 partial 经 checkpoint 跨轮合并。
- **投递**（Task 5）：`run_sales_action_scheduler_once` 两段式——Pass1 认领->渲染->`send_markdown_card`->`record_delivery_*`->**commit**；Pass2 幂等创建 digest（每条 insert 包 SAVEPOINT 吸收并发撞键）。按钮回调 `POST /integrations/dingtalk/sales-actions/callback` 验签后驱动状态机 + `update_card` 重渲染。
- **运维视图**（Task 6）：只读 API `/agents/{id}/sales-actions` + 控制台「销售任务」页。

## SQL 巡检

```sql
-- 某 Agent 下各状态任务计数
SELECT status, count(*) FROM sales_action_cards
 WHERE tenant_id=:t AND agent_id=:a GROUP BY status;

-- 在途/失败提醒
SELECT id, action_id, reminder_type, status, attempts, remind_at, next_attempt_at, last_error
 FROM sales_action_reminders
 WHERE tenant_id=:t AND agent_id=:a AND status IN ('scheduled','sending','failed')
 ORDER BY remind_at;

-- 投递记录（含失败）
SELECT id, action_id, reminder_id, delivery_type, status, error, created_at
 FROM sales_action_deliveries
 WHERE tenant_id=:t AND agent_id=:a AND status='failed' ORDER BY created_at DESC;

-- 某动作的全生命周期事件
SELECT event_type, created_at FROM sales_action_events
 WHERE tenant_id=:t AND agent_id=:a AND action_id=:aid ORDER BY created_at;
```

## 排障：到期提醒未投递

1. 确认 `sales_actions.enabled` 与 `scheduler_enabled` 均为真，worker 日志有调度 tick。
2. 查 `sales_action_reminders` 是否有 `status='scheduled'` 且 `remind_at<=now()`：若有则调度器没认领（查 `scan_interval_seconds`/worker 健康）；若已 `sending` 但无对应 `sales_action_deliveries`，说明发送中途 crash，下次扫描不会重发（`sending` 不在认领条件内）——见「安全重放」。
3. `failed` 提醒：`attempts<max_attempts` 且 `next_attempt_at<=now()` 会被自动重认领；`attempts>=max_attempts` 为 dead-letter，不再自动重试。

## 安全重放失败/卡住的提醒

`claim_due_reminders` 只认领 `scheduled`（到期）与 `failed`（可重试）。**`sending` 态不会被重认领**（防并发重复投递）。若一条 `sending` 因 crash 卡住（无 delivery 记录），手动复位后再让调度器重投：

```sql
-- 仅对确无 delivery 记录的 sending 行复位（先核对！）
UPDATE sales_action_reminders
   SET status='scheduled', next_attempt_at=NULL
 WHERE id=:rid AND status='sending'
   AND NOT EXISTS (SELECT 1 FROM sales_action_deliveries WHERE reminder_id=:rid AND status='success');
```

复位后下一个调度 tick 会重新认领并投递。**切勿**把已 `delivered` 的复位——会导致用户收到重复卡片。

## 排障：钉钉卡片按钮回调

- 回调路由 `POST /integrations/dingtalk/sales-actions/callback`，走与 `/integrations/dingtalk/events` 相同的 `DingTalkSignatureVerifier` 验签。401 = 签名不匹配（检查 `app_secret` / 时间戳）；403 = corp 不属于本实例。
- `complete/snooze/cancel` 均幂等：重复点击返回 `already_done`/`already_snoozed`/`already_terminal`，不重复事件。`not_found` 不重渲染卡片（避免把回执写到无关卡片）。
- 卡片重渲染失败仅记 warning，不影响状态机结果（`update_card` 非阻塞）。
- **生产接线 TODO**：`normalize_dingtalk_card_callback` 当前为透传；真实钉钉互动卡片按钮载荷（`cardRequestId`/button key-value/加密包装）需按实样映射到内部 `SalesActionCallbackRequest`。

## 排障：digest 重复 / 缺失

- digest 按 `(kind, tenant, agent, user, date)` 幂等键去重，同作用域同日最多一次。并发 worker 撞唯一约束由 SAVEPOINT 吸收（不抛错、不回滚投递）。
- 若某用户该日无 digest：确认该作用域有 `pending` 动作（`list_active_action_scopes` 只返回有待办的作用域），且当时本地时间在 09:00/18:30 + `grace_hours` 窗口内。
- digest 用全局 `default_timezone`（非按卡片时区）；跨时区租户需按需调整。

## 回滚

1. 设 `sales_actions.enabled=false` 并重启 worker：调度 loop 停止、Graph 不再路由销售动作（用户消息回归普通 chat）。
2. 已创建的任务卡片/提醒/投递记录保留在库中（只读历史）；如需清理，按 `tenant_id` 删四张表（**不可逆**，先备份）。
3. Alembic 降级（如需）：`alembic downgrade 0015_memory_eval_operations`（会删表，谨慎）。

## 重复投递防护核对

- `claim_due_reminders`：`FOR UPDATE SKIP LOCKED` + 同事务翻 `scheduled->sending`，二次扫描看到 `sending` 不再认领。
- 失败重试：仅 `failed + next_attempt_at<=now() + attempts<max_attempts` 被重认领；`max_attempts` 后 dead-letter。
- digest：唯一约束 + 预检 + SAVEPOINT 三重保险。
- 按钮：状态机幂等（`already_*`），`complete/cancel` 终态翻转 `FOR UPDATE` 锁卡片行防 TOCTOU。

## 发布门禁

```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  bash scripts/run_sales_action_gate.sh
```

含 `tests/unit/sales_actions` + `tests/unit/eval/test_sales_action_eval.py` + 4 个集成测试 + fixture eval（`eval/run_sales_action_eval.py --mode fixture`）。`TEST_DATABASE_URL` 必须含 `test`，否则拒绝运行。
