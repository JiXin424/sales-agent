# Sales Action Cards and Proactive Reminders Design

**Status:** Approved design draft
**Date:** 2026-07-10
**Target:** Production DingTalk Sales Agent
**Depends on:** Durable Online Graph, governed long-term memory, user profile memory recall

## 1. Goal

Add a proactive sales action layer: the Agent can create sales task cards, remind users at the right time through DingTalk, surface daily task summaries, and expose a read-only operations view.

This should upgrade the Agent from “answering when asked” to “helping salespeople follow through,” without becoming a CRM or polluting long-term memory with temporary work items.

## 2. Product Scope

### In scope

- explicit one-time reminders such as “半小时后提醒我给张总回电话”;
- sales action cards with customer/object, action type, scheduled time, source context, status, and Agent advice;
- LLM-based time/action extraction with deterministic validation;
- clarification for incomplete time or incomplete action;
- Agent-suggested action creation from conversation context, only after user confirmation;
- DingTalk proactive reminder cards;
- DingTalk card buttons: complete, snooze 30 minutes, cancel;
- morning digest at 09:00 and evening digest at 18:30;
- task list, completion, cancellation, snooze, and pending clarification through DingTalk;
- API endpoints for test, maintenance, and read access;
- simple read-only operations page for pending, reminded, done, cancelled, expired, failed, and retrying work;
- delivery records, idempotency, retry, and audit events;
- real DingTalk business-path tests with only public outbound delivery replaced.

### Out of scope

- recurring user-defined tasks;
- external calendar integration;
- CRM customer master data, opportunity amount, win probability, and pipeline ownership;
- admin editing of tasks in the UI;
- automatically sending messages to customers;
- storing tasks as long-term memory or profile facts;
- full workflow automation after a reminder fires.

## 3. Product Principles

1. Clear user commands create tasks directly.
2. Incomplete time or incomplete action always asks a clarification before creating a task.
3. Agent-inferred action items are suggestions only; user confirmation is required.
4. The creation response must restate the concrete action and scheduled time.
5. Tasks are temporary operational data and do not enter long-term memory or user profile projection.
6. User profile and long-term memory may shape reminder wording, but cannot create tasks silently.
7. All reads, writes, reminders, deliveries, cards, and operations views are scoped by tenant, Agent, and user.
8. Active reminder delivery must be idempotent across worker restarts and multiple workers.
9. Card button actions must be idempotent; duplicate clicks return the current status without side effects.
10. Maintenance visibility is required: operators must be able to see what is pending, sent, done, failed, and why.

## 4. User Experience

### 4.1 Explicit create

User:

```text
半小时后提醒我给张总回电话
```

Agent:

```text
已创建提醒：今天 15:30，提醒你给张总回电话。
```

The task is created immediately because both the action and time are clear.

### 4.2 Defaulted fuzzy time

User:

```text
明天上午记得给李总发方案
```

Agent:

```text
已创建任务：明天 09:30，提醒你给李总发方案。
```

“明天上午” defaults to 09:30 and the response explicitly states that concrete time.

### 4.3 Time clarification

User:

```text
这两天提醒我跟一下王总
```

Agent:

```text
可以，我理解事项是“跟进王总”，但“这两天”不够精确。你想设在明天上午、明天下午，还是后天上午？
```

No task is created until the user answers.

### 4.4 Action clarification

User:

```text
明天上午提醒我一下
```

Agent:

```text
可以，时间我按明天 09:30 理解。具体提醒你做什么？
```

The pending clarification stores the parsed time and waits for the action.

### 4.5 Agent-suggested action

User, inside a visit review:

```text
我明天得把报价再发他一次。
```

Agent:

```text
要不要我帮你创建一个任务：明天 09:30，给该客户补发报价？
```

If the user replies “可以”, the task is created. If the user ignores it, no task exists.

### 4.6 Due reminder card

DingTalk card:

```text
销售任务提醒

事项：给张总回电话
时间：今天 15:30
对象：张总
建议：先确认对方是否方便，再切入报价进展。

[已完成] [稍后提醒] [取消任务]
```

### 4.7 Daily digests

Morning digest at 09:00:

- today’s pending tasks;
- overdue tasks;
- top 1–3 recommended priorities;
- buttons for each task.

Evening digest at 18:30:

- today’s unfinished tasks;
- tomorrow’s scheduled tasks;
- potential follow-up risks;
- buttons for each task.

## 5. Architecture

```text
DingTalk user message
  ↓
Online Graph
  ↓
Sales Action Detector
  ├─ clear create/update/list command → service operation
  ├─ incomplete command → clarification response
  ├─ suggested action → user confirmation
  └─ ordinary message → existing Topic / Evidence / Chat path
  ↓
Sales Action Repository
  ↓
Scheduler Worker
  ├─ due one-time reminders
  ├─ morning digest
  └─ evening digest
  ↓
DingTalk Card Delivery
  ↓
Card callback: complete / snooze / cancel
  ↓
State transition + delivery record + audit event
```

The feature is a new service domain, recommended under:

```text
src/sales_agent/services/sales_actions/
```

It integrates with the Online Graph and DingTalk integration layer but does not live inside DingTalk-specific code. DingTalk is one channel implementation.

## 6. Data Model

### 6.1 `sales_action_cards`

Main task card table.

Required fields:

```text
id
tenant_id
agent_id
user_id
channel
dingtalk_user_id
conversation_id
topic_id
source_event_id
source_kind              # explicit_user | agent_suggested_confirmed | daily_summary_generated
title
description
customer_name
action_type              # call_back | send_proposal | follow_up_quote | visit_prepare | post_visit_review | send_material | other
scheduled_at
timezone
status                   # pending | reminded | done | cancelled | expired
priority                 # low | normal | high
context_snapshot_json
agent_advice
created_at
updated_at
```

`context_snapshot_json` stores only the minimum context needed for the reminder, such as the original user phrase, Topic ID, customer/object name, and a short source summary. It is not used for user profile projection.

### 6.2 `sales_action_reminders`

Reminder schedule table. One task may have multiple reminder rows, especially after snooze.

Required fields:

```text
id
action_id
tenant_id
agent_id
user_id
remind_at
reminder_type            # one_time | morning_digest | evening_digest | snooze
status                   # scheduled | sending | sent | failed | cancelled
attempts
next_attempt_at
last_error
idempotency_key
created_at
updated_at
```

`idempotency_key` is unique and prevents duplicate reminder deliveries.

### 6.3 `sales_action_deliveries`

Outbound delivery records.

Required fields:

```text
id
action_id
reminder_id
tenant_id
agent_id
user_id
channel
delivery_type            # due_reminder | morning_digest | evening_digest | card_action_ack
dingtalk_message_id
card_instance_id
rendered_text
status                   # success | failed
error
created_at
```

This table makes operations diagnosis clear: created-but-not-sent, sent-but-not-completed, failed, and retried states are distinguishable.

### 6.4 `sales_action_events`

Audit and state-transition table.

Required fields:

```text
id
action_id
tenant_id
agent_id
user_id
event_type               # created | suggested | clarification_requested | reminder_sent | done | cancelled | snoozed | failed
event_payload_json
created_at
```

## 7. State Machine

```text
pending
  ├─ due reminder sent successfully → reminded
  ├─ user completes → done
  ├─ user cancels → cancelled
  └─ exceeds expiry window → expired

reminded
  ├─ user completes → done
  ├─ user snoozes → pending + new snooze reminder
  ├─ user cancels → cancelled
  └─ remains unfinished → appears in evening digest

done / cancelled / expired
  └─ terminal; duplicate button clicks return current state
```

Default expiry: a task more than seven days past `scheduled_at` and still not done or cancelled becomes `expired`. This threshold is configurable.

## 8. Action Understanding

### 8.1 Intents

Closed action intent enum:

- `create_action`
- `complete_action`
- `cancel_action`
- `snooze_action`
- `list_actions`
- `suggest_action`
- `none`

### 8.2 Explicit command detection

Clear command phrases include:

- “提醒我”
- “记得提醒”
- “帮我记一下”
- “设个提醒”
- “到时候叫我”
- “帮我建个任务”

If an explicit command includes both a valid time and a concrete action, the system creates a task directly.

### 8.3 Suggested action detection

Statements such as “我明天得把方案发给李总” contain an actionable plan but not necessarily a command to the Agent. They become `suggest_action`; the Agent asks for confirmation and does not create a task until the user accepts.

### 8.4 Completion, cancellation, snooze, and list

Examples:

- “张总那个电话我打完了” → `complete_action`
- “取消明天给王总发资料的提醒” → `cancel_action`
- “半小时后再提醒我” → `snooze_action`
- “我今天还有哪些任务” → `list_actions`

Ambiguous references resolve against the user’s active pending/reminded tasks. Multiple matches require clarification.

## 9. Time Parsing

Time parsing uses LLM extraction plus deterministic validation.

The LLM returns structured JSON only:

```json
{
  "intent": "create_action",
  "explicit_create": true,
  "title": "给张总回电话",
  "customer_name": "张总",
  "action_type": "call_back",
  "time_text": "半小时后",
  "scheduled_at": "2026-07-10T15:30:00+08:00",
  "timezone": "Asia/Shanghai",
  "confidence": 0.92,
  "missing_fields": [],
  "needs_clarification": false,
  "clarification_question": null
}
```

Deterministic validation rules:

- `scheduled_at` cannot be in the past.
- `timezone` is required; default is `Asia/Shanghai`.
- `title` cannot be blank.
- `confidence < 0.75` requires clarification.
- any non-empty `missing_fields` requires clarification.
- “这两天”, “最近”, “有空”, “客户开完会后” require clarification.
- “明天上午” defaults to 09:30.
- “明天下午” defaults to 15:00.
- “明天晚上” defaults to 20:00.
- “下午” with today’s 15:00 already passed requires clarification in v1.
- parsed/defaulted time must be repeated in the user-facing reply.

The LLM never writes to storage. It proposes a structure; policy decides whether to create, clarify, or suggest.

## 10. Multi-turn Clarification

Incomplete task creation stores a pending action clarification in the existing Online Graph / Topic clarification mechanism.

Example shape:

```json
{
  "kind": "sales_action_create",
  "partial": {
    "scheduled_at": "2026-07-11T09:30:00+08:00",
    "timezone": "Asia/Shanghai",
    "title": null,
    "customer_name": null,
    "action_type": null
  },
  "event_id": "ding_xxx",
  "attempts": 1
}
```

The next user turn may complete the missing fields. The system must resolve that turn as action clarification, not as a new unrelated chat.

## 11. Online Graph Integration

Recommended Online Graph priority:

1. duplicate event;
2. reset;
3. guided-flow quick entry;
4. sales-action clarification completion or DingTalk card callback;
5. explicit sales-action command;
6. Topic / Context Resolution;
7. Evidence Routing;
8. Profile Recall;
9. Chat;
10. post-chat action suggestion.

`suggest_action` runs after normal response generation because it should not steal the main user request. It may append a short confirmation question when a likely follow-up task is discovered.

## 12. Scheduler Worker

The project should use an internal database-backed scheduler worker instead of OS cron.

Due-reminder scan:

```sql
SELECT *
FROM sales_action_reminders
WHERE status = 'scheduled'
  AND remind_at <= now()
ORDER BY remind_at
LIMIT 50
FOR UPDATE SKIP LOCKED;
```

Worker behavior:

1. claim reminder by moving `scheduled` → `sending`;
2. check idempotency key and prior successful delivery;
3. send DingTalk card;
4. write `sales_action_deliveries`;
5. move reminder to `sent` or `failed`;
6. on failure, increment attempts and set `next_attempt_at` with bounded backoff;
7. after max attempts, leave the reminder visible as `failed`.

Worker scan interval: 30 seconds by default.

## 13. Daily Digest

Daily digest is system-generated, not user-created recurring tasks.

Defaults:

- morning digest: 09:00;
- evening digest: 18:30;
- timezone: `Asia/Shanghai`;
- v1 sends every day, not only workdays.

Idempotency keys:

```text
morning_digest:{tenant_id}:{agent_id}:{user_id}:{yyyy-mm-dd}
evening_digest:{tenant_id}:{agent_id}:{user_id}:{yyyy-mm-dd}
```

Morning digest includes:

- today’s pending tasks;
- overdue tasks;
- top 1–3 recommended priorities.

Evening digest includes:

- unfinished tasks scheduled for today;
- tasks scheduled for tomorrow;
- a short risk reminder for delayed follow-up.

If a user has no relevant tasks, v1 should not send a digest by default. A tenant config may opt into “empty digest” messages later.

## 14. DingTalk Card Delivery

Due reminder card content:

```text
销售任务提醒

事项：给张总回电话
时间：今天 15:30
对象：张总
建议：先确认对方是否方便，再切入报价进展。

[已完成] [稍后提醒] [取消任务]
```

Supported button actions:

- complete;
- snooze 30 minutes;
- cancel.

Button behavior:

- complete sets task `done`, cancels unsent reminders, writes event, and returns an acknowledgement;
- snooze creates a new reminder 30 minutes later and returns task to `pending`;
- cancel sets task `cancelled`, cancels unsent reminders, writes event, and acknowledges;
- duplicate button events are no-ops with a friendly current-state response.

Custom snooze is supported through chat text such as “1小时后再提醒”, not through a v1 card button.

## 15. User Profile and Long-term Memory Boundary

Sales action cards may use user profile and long-term memory for style and context:

- concise preference → shorter reminder text;
- coaching goal → one sentence of coaching advice;
- product focus → better action type/advice selection.

But task cards do not become atomic memories. Temporary task details do not enter `agent_memories`, `user_memory_profiles`, or profile recall.

Reminder advice must not introduce new customer facts. It may use:

- task fields;
- `context_snapshot_json`;
- active Topic summary at creation time;
- user profile style preferences.

## 16. API

API supports maintenance, testing, and future internal tools.

Recommended endpoints:

```text
POST /api/v1/agents/{agent_id}/sales-actions
GET  /api/v1/agents/{agent_id}/sales-actions
GET  /api/v1/agents/{agent_id}/sales-actions/{action_id}
POST /api/v1/agents/{agent_id}/sales-actions/{action_id}/complete
POST /api/v1/agents/{agent_id}/sales-actions/{action_id}/cancel
POST /api/v1/agents/{agent_id}/sales-actions/{action_id}/snooze
GET  /api/v1/agents/{agent_id}/sales-actions/reminders
GET  /api/v1/agents/{agent_id}/sales-actions/deliveries
```

Authorization:

- user actions in DingTalk use the DingTalk identity path;
- API requires admin/ops permission;
- read-only operations page uses only read APIs;
- mutation APIs may exist for tests/internal tooling but are not exposed as buttons in the v1 operations page.

## 17. Read-only Operations Page

Path:

```text
/admin/sales-actions
```

Features:

- list task cards;
- filter by tenant, agent, user, status, action type, scheduled time range, reminder status;
- show task title, customer/object, user, status, scheduled time, latest reminder status, failure reason, source kind, created time;
- task detail drawer or page showing:
  - action fields;
  - reminders;
  - deliveries;
  - events;
  - context snapshot summary.

No editing in v1. Operations staff can inspect but not mutate tasks from this page.

## 18. Error Handling and Degradation

- LLM extraction failure: ask user to restate or fall back to no task creation.
- Time ambiguity: ask clarification, no task created.
- Repository write failure: tell user the task was not saved and ask them to retry.
- Scheduler failure: due reminder remains visible and retryable.
- DingTalk send failure: write delivery failure and retry with bounded backoff.
- Card callback duplicate: no side effect, return current status.
- User has no DingTalk mapping: mark reminder failed with a visible operational error.
- Profile recall failure: send reminder without personalization and mark trace/advice degraded.

## 19. Observability

Metrics:

- action created count by source kind;
- explicit create success rate;
- clarification rate;
- reminder due count;
- reminder sent success/failure count;
- duplicate delivery prevented count;
- card button complete/snooze/cancel count;
- digest sent count;
- overdue task count;
- failed reminder count.

Logs and traces must include:

- tenant ID;
- Agent ID;
- internal user ID;
- action ID;
- reminder ID;
- delivery ID;
- event ID;
- idempotency key;
- status transition.

Do not log sensitive full context snapshots in operational logs.

## 20. Tests

### Unit tests

- explicit create detection;
- suggested action detection;
- complete/cancel/snooze/list detection;
- time parsing:
  - “半小时后”;
  - “明天上午” → 09:30;
  - “周五下午三点”;
  - “这两天” → clarification;
  - past time → clarification;
- required field validation;
- pending action clarification merge;
- status transitions;
- duplicate button event idempotency;
- digest idempotency key creation;
- reminder retry backoff.

### Integration tests

- DingTalk user creates an explicit reminder through `handle_dingtalk_event()`;
- scheduler sends the due DingTalk card through the real delivery seam with public outbound captured;
- card complete button sets action to `done`;
- card snooze button creates a new reminder;
- card cancel button cancels unsent reminders;
- two workers scanning the same due reminders send only once;
- morning digest sends once per user/day;
- evening digest sends once per user/day;
- operations API returns pending, reminded, done, cancelled, failed, and delivery states.

### Evaluation scenarios

JSONL scenarios should cover:

1. clear one-time reminder creation;
2. fuzzy time requiring clarification;
3. missing action requiring clarification;
4. Agent-suggested task and user confirmation;
5. due reminder card delivery;
6. complete action through card;
7. snooze action through card;
8. cancel action through card;
9. morning digest;
10. evening digest;
11. duplicate scheduler claim;
12. task does not appear in long-term memory or profile recall.

## 21. Acceptance Criteria

- Clear reminder instructions create the correct action and scheduled time.
- Every creation response states the exact task and reminder time.
- Ambiguous time or missing action never creates a task without clarification.
- Agent-inferred tasks require user confirmation.
- Due reminders are sent exactly once despite worker concurrency.
- Card button operations are idempotent.
- Morning digest sends at most once per user per date.
- Evening digest sends at most once per user per date.
- Failed deliveries are visible with error and retry state.
- Operations page can inspect all task, reminder, delivery, and event states.
- Tasks never enter atomic long-term memory or user profile projection.
- Ordinary answering continues if action extraction, reminder personalization, or scheduler components degrade.
