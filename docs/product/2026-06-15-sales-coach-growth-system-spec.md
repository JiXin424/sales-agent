# Sales Coach Growth System - Specification

**Created:** 2026-06-15
**Ambiguity score:** 0.16 (gate: <= 0.20)
**Requirements:** 16 locked

## Goal

Add a Sales Coach Growth System to the existing sales Agent. The system should turn daily sales conversations into credible long-term coaching data: six-dimensional competency scores, evidence-backed daily observations, iceberg diagnosis, milestones, ranks, levels, rewards, and coach reports. It also adds a realtime coaching line that naturally blends lightweight guidance into normal Agent replies without exposing internal scores unless the user asks for a report.

The target product experience has two lines:

```text
Realtime line:
Daily sales conversation -> coach_observe -> coach_guidance -> guidance blended into reply

Offline line:
Daily scheduled evaluation at 23:00 -> six-dimensional deltas -> scores -> milestones -> rank/level -> rewards -> coach report
```

## Background

The current project already has:

- Agent Instance support with `tenant_id + agent_id` scoping.
- `ChatPipeline` with validation, tenant/Agent resolution, context loading, task routing, prompt resolution, retrieval, generation, risk checking, logging, run tracing, and latency stats.
- DingTalk single-chat integration.
- `conversation_messages`, `conversations`, and runtime logs with `agent_id` fields.
- A `conversation_scoring` prompt for one-off conversation assessment.
- A worker role skeleton where background schedulers can be added.

The project does not yet have a persistent user growth account, daily scoring, iceberg diagnosis, milestone/reward progression, or a dedicated coach report intent.

This system must be added as a Coach subsystem, not as a replacement for the existing Agent pipeline.

## Product Principles

1. **Scores must be behavior-based.** Scores are derived from real conversation evidence, not self-assessment or one-time quizzes.
2. **Realtime coaching must feel natural.** Normal replies may include 1-2 coaching sentences, but must not expose internal scores, levels, or backend labels.
3. **Detailed reports require explicit user intent.** Users see scores, iceberg analysis, milestones, and rewards only when they ask for them.
4. **Offline evaluation is authoritative.** The daily job is the source of long-term scores, milestones, levels, and rewards.
5. **Every judgment needs evidence.** Non-zero score changes and iceberg blocks must include short evidence quotes from the user's conversations.
6. **Tenant and Agent scope are mandatory.** All coach data is scoped by `tenant_id`, `agent_id`, and `user_id`.
7. **Failure must not affect chat.** Coach failures should be logged and surfaced to admin views, but should not break normal sales Agent replies.

## Scope

### In scope

- Six-dimensional sales competency model.
- Daily 23:00 evaluation job and manual admin trigger.
- Daily `-3..+3` competency deltas and cumulative `0..100` scores.
- Evidence-backed observations.
- Iceberg model analysis.
- Coach report intent and DingTalk report rendering.
- Milestones, ranks, levels, and reward records.
- Realtime coach observation and guidance blending.
- Agent-scoped coach admin APIs and console views.
- Tests for scoring, idempotency, report routing, milestone unlock, and realtime guidance suppression.

### Out of scope

- Real payment/red-packet transfer integration.
- Full text-to-speech implementation for voice rewards.
- Full RBAC/SSO implementation.
- CRM integration.
- Replacing existing conversation scoring task.
- Making coaching mandatory for every Agent. It should be configurable.

## Competency Model

The system evaluates six sales competencies:

| Dimension | Key | Meaning |
|---|---|---|
| 客户识别 | `customer_identification` | 识别客户角色、背景、真实需求和决策链。 |
| 需求挖掘 | `needs_discovery` | 通过追问探索客户深层需求、痛点和约束。 |
| 价值传递 | `value_delivery` | 把产品功能转译为客户收益、业务价值和风险降低。 |
| 信任建立 | `trust_building` | 建立可信、专业、稳定的客户关系。 |
| 交易推进 | `deal_advancement` | 推动下一步行动、成交决策、预算和时间表。 |
| 复盘反思 | `review_reflection` | 对沟通过程进行总结、反思和持续改进。 |

Each dimension has a current score in `0..100`. New users start at `50`, meaning “insufficient observation, baseline level.” Scores are updated only by daily evaluations with integer deltas in `-3..+3`.

## Daily Delta Rubric

Each dimension receives one integer daily delta:

| Delta | Meaning |
|---:|---|
| +3 | 突出表现，例如首次使用 SPIN 追问、帮助客户算账、清晰推动决策。 |
| +2 | 明显进步行为。 |
| +1 | 基础正向行为。 |
| 0 | 信号不明显或证据不足。 |
| -1 | 轻微退步行为。 |
| -2 | 明显违反销售方法论。 |
| -3 | 严重问题，例如高风险承诺、强推、明显误导或严重失控。 |

Rules:

- Delta must be an integer.
- Delta outside `-3..+3` invalidates the evaluation result.
- Any non-zero delta must include `reason`, `evidence_quotes`, and `confidence`.
- If evidence is insufficient for a dimension, that dimension delta must be `0`.
- If the whole day has insufficient data, the daily evaluation is `skipped` and scores do not change.

## Iceberg Model

Scores answer “how much.” The iceberg model answers “where and why the user is stuck.”

### Surface blocks

Visible behavior layer. Maximum 5 blocks per analysis.

Allowed types:

- `customer_block`: 客户卡点
- `needs_block`: 需求卡点
- `value_block`: 价值卡点
- `trust_advancement_block`: 信任推进卡点
- `action_rhythm_block`: 行动节奏卡点

### Deep blocks

Mindset layer. Maximum 4 blocks per analysis.

Allowed types:

- `motivation_block`: 目标动力卡点
- `confidence_block`: 信心卡点
- `belief_block`: 信念卡点
- `emotional_pressure_block`: 情绪压力卡点

Each block contains:

- `type`
- `severity`: `low`, `medium`, `high`
- `description`
- `evidence_quotes`
- `source_conversation_ids`

If data is insufficient, the report should say that more sales conversation data is needed.

## Data Model

All new tables must include `tenant_id`, `agent_id`, and `user_id` unless explicitly marked as a definition table.

### `coach_user_profiles`

A user's coach growth account under one Agent.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `enabled`
- `total_points`
- `rank`: `bronze`, `silver`, `samurai`, `master`, `king`
- `level`: `0..10`
- `last_evaluated_date`
- `report_preferences_json`
- `created_at`, `updated_at`

### `coach_competency_scores`

Current score by dimension.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `dimension`
- `score`: `0..100`
- `milestone_level`
- `last_delta`
- `last_evaluation_id`
- `last_evaluated_at`
- `created_at`, `updated_at`

Constraints:

- Unique: `(tenant_id, agent_id, user_id, dimension)`.

### `coach_competency_observations`

Evidence-backed daily score movement records.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `evaluation_id`
- `evaluation_date`
- `dimension`
- `delta`
- `old_score`
- `new_score`
- `reason`
- `evidence_quotes_json`
- `source_conversation_ids_json`
- `confidence`
- `created_at`

### `coach_daily_evaluations`

One user/day evaluation record.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `evaluation_date`
- `status`: `success`, `skipped`, `failed`, `dry_run`
- `conversation_count`
- `user_message_count`
- `input_summary`
- `result_json`
- `score_deltas_json`
- `iceberg_json`
- `points_delta`
- `model_config_json`
- `latency_ms`
- `error_json`
- `replaces_evaluation_id`
- `created_at`, `updated_at`

Constraints:

- Default unique success row: `(tenant_id, agent_id, user_id, evaluation_date)`.
- Recompute must be explicit and reversible or revisioned.

### `coach_iceberg_analyses`

Latest and historical iceberg diagnoses.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `evaluation_id`
- `analysis_date`
- `surface_blocks_json`
- `deep_blocks_json`
- `evidence_json`
- `data_sufficiency`: `sufficient`, `insufficient`
- `summary`
- `created_at`

### `coach_milestones`

Definition table. May be seeded from code constants.

Fields:

- `id`
- `scope`: `dimension`, `all_dimensions`
- `dimension`: nullable for all-dim milestones
- `threshold`
- `level_index`
- `name`
- `description`
- `badge_key`
- `created_at`, `updated_at`

Seed count:

- 72 dimension milestones: 6 dimensions x 12 thresholds.
- 12 all-dimension milestones.

Thresholds:

```text
5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100
```

### `coach_user_milestones`

Unlocked milestones.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `milestone_id`
- `unlocked_at`
- `trigger_score`
- `source_evaluation_id`
- `created_at`

Constraints:

- Unique: `(tenant_id, agent_id, user_id, milestone_id)`.

### `coach_rewards`

Reward records.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `reward_type`: `text_milestone`, `voice_encouragement`, `badge`, `red_packet_reminder`
- `status`: `pending`, `delivered`, `failed`, `suggested`
- `message`
- `related_milestone_id`
- `source_evaluation_id`
- `delivery_channel`
- `delivery_target`
- `delivered_at`
- `error_json`
- `created_at`, `updated_at`

### `coach_realtime_observations`

Realtime observe/guidance logs.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `conversation_id`
- `scene_hint`
- `confidence`
- `observed_signals_json`
- `dimension_focus`
- `guidance_level`: `specific`, `directional`, `suppressed`
- `guidance_text`
- `applied_to_reply`
- `suppressed_reason`
- `created_at`

### `coach_report_requests`

Report request audit log.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `user_id`
- `report_type`: `scores`, `level`, `iceberg`, `milestones`, `rewards`, `full`
- `query_text`
- `rendered_summary`
- `created_at`

### `coach_settings`

Agent-level coach configuration.

Fields:

- `id`
- `tenant_id`
- `agent_id`
- `realtime_enabled`
- `daily_evaluation_enabled`
- `daily_evaluation_time`: default `23:00`
- `timezone`: default tenant timezone or server timezone
- `minimum_user_messages`: default `3`
- `daily_realtime_guidance_limit`: default `3`
- `daily_reward_notification_limit`: default `3`
- `initial_score`: default `50`
- `allow_negative_delta`: default `true`
- `voice_rewards_enabled`: default `false`
- `red_packet_reminders_enabled`: default `false`
- `evidence_quote_max_chars`: default `160`
- `created_at`, `updated_at`

## Offline Daily Evaluation

### Triggering

The worker role should start a coach scheduler.

Default behavior:

- Runs every day at `23:00` per configured timezone.
- Finds Agents with daily coach evaluation enabled.
- Finds users with enough conversation activity for that date.

Manual trigger:

```text
POST /agents/{agent_id}/coach/admin/run_daily
```

Request fields:

- `user_id` optional
- `date` optional, defaults to today/yesterday according to timezone
- `dry_run` optional
- `force_recompute` optional

### Evaluation flow

1. Resolve Agent and tenant ownership.
2. Load coach settings.
3. Find users with sales conversations that day.
4. Exclude help/reset/report-only messages.
5. Aggregate user messages, assistant summaries, task types, channel, and conversation IDs.
6. If input is too long, summarize while preserving quoteable evidence.
7. Call LLM once per user/day using a strict JSON schema.
8. Validate schema and value ranges.
9. If `dry_run`, return result without mutating scores.
10. If insufficient data, create `skipped` evaluation and stop.
11. If successful, update scores, observations, iceberg analysis, points, milestones, rewards.
12. Send reward notifications only when needed and within limits.
13. Commit atomically per user/day.

### LLM output schema

The daily evaluator should request JSON with this shape:

```json
{
  "data_sufficiency": "sufficient",
  "summary": "string",
  "dimensions": {
    "customer_identification": {
      "delta": 1,
      "reason": "string",
      "evidence_quotes": ["string"],
      "source_conversation_ids": ["string"],
      "confidence": 0.8
    }
  },
  "iceberg": {
    "surface_blocks": [
      {
        "type": "value_block",
        "severity": "high",
        "description": "string",
        "evidence_quotes": ["string"],
        "source_conversation_ids": ["string"]
      }
    ],
    "deep_blocks": []
  },
  "points": {
    "conversation_points": 10,
    "topic_points": 5,
    "quality_signal_points": 2,
    "reason": "string"
  },
  "next_growth_suggestion": "string"
}
```

The implementation must require all six dimensions. Missing dimensions invalidate the result.

### Idempotency and recompute

Default behavior:

- If a successful evaluation already exists for `(tenant_id, agent_id, user_id, date)`, return existing and do not add deltas again.

`force_recompute=true` behavior:

- Preferred: create a replacement evaluation and reverse prior deltas before applying new deltas.
- Acceptable first version: block recompute unless `dry_run=true`, and expose a clear message.

No implementation may silently apply deltas twice.

### Failure handling

- LLM call failure: record `failed`, do not update scores.
- JSON parse failure: retry once; if still failing, record `failed`.
- Schema validation failure: record `failed`.
- Insufficient data: record `skipped`, do not update scores.
- Partial evidence missing: force that dimension delta to `0`, or reject the evaluation if evidence requirements cannot be normalized safely.

## Coach Report Intent

Add `CoachIntentRouter` before normal task routing in `ChatPipeline`, after help/reset handling and Agent resolution.

Trigger phrases include:

- `我的评分`
- `评分`
- `我的能力`
- `能力报告`
- `教练报告`
- `我的等级`
- `我的段位`
- `里程碑`
- `奖励`
- `冰山`
- `我卡在哪`
- `哪里需要提升`

Report type rules:

| Trigger | Report type |
|---|---|
| 冰山 / 卡在哪 / 深层问题 | `iceberg` |
| 评分 / 能力 | `scores` |
| 等级 / 段位 | `level` |
| 里程碑 | `milestones` |
| 奖励 / 徽章 / 红包 | `rewards` |
| 教练报告 / 完整报告 | `full` |

When a coach report intent matches:

1. Resolve `agent_id`.
2. Call `CoachReportService.render_report(...)`.
3. Return a `PipelineResult` directly.
4. Log the conversation with `task_type="coach_report"`.
5. Write `coach_report_requests`.
6. Do not run normal sales task routing, RAG, generation, or realtime coach guidance.

## Report Types

### `scores`

Shows:

- Six current scores.
- Last delta for each dimension.
- One concise explanation.
- Latest evidence highlight.
- Insufficient data message when needed.

### `level`

Shows:

- Total points.
- Rank.
- Level.
- Distance to next rank/level.
- 1-3 recently unlocked milestones.

### `iceberg`

Shows:

- Latest surface blocks, up to 5.
- Latest deep blocks, up to 4.
- Severity and short evidence.
- Insufficient data message when needed.

### `milestones`

Shows:

- Unlocked count.
- Recent milestones.
- Next nearest milestones.

### `rewards`

Shows:

- Recent reward records.
- Delivered, suggested, pending, and failed states.

### `full`

Shows:

- Six-score overview.
- Rank and level.
- Recent milestones.
- Iceberg blocks.
- 1-2 next growth suggestions.

## Realtime Coach Guidance

### Pipeline integration

Add two conceptual steps to `ChatPipeline`:

```text
routing -> coach_observe -> path/retrieval -> coach_guidance -> generation -> risk_check
```

`coach_observe` should run after normal sales task routing. `coach_guidance` should run before `execute_agent`, so guidance can be blended into the generated answer and still pass risk checks.

### `coach_observe`

First version should be rule-based.

Inputs:

- `tenant_id`
- `agent_id`
- `user_id`
- current message
- recent history
- task type
- conversation ID

Outputs:

- `scene_hint`
- `confidence`
- `observed_signals`
- `should_generate_guidance`
- `reason`

Scene hints:

- `visit_preparation`
- `pain_point_discovery`
- `customer_feedback`
- `product_demo`
- `post_visit`
- `next_step`
- `closing_or_pricing`
- `dormant_customer`
- `frustration`
- `teaching_others`

### `coach_guidance`

Inputs:

- scene hint
- current six scores
- weak dimensions
- task type
- current message
- daily realtime guidance count
- dependency rules

Guidance bands:

- Score `<40`: specific method and example wording.
- Score `40..70`: directional reminder.
- Score `>70`: suppress by default.

Dependency example:

- If `needs_discovery` is weak and `customer_identification` is also weak, guide the user to strengthen customer identification first.

Suppression rules:

- Help/reset commands.
- Coach report requests.
- Pure knowledge Q&A unrelated to sales action.
- Daily realtime guidance limit exceeded.
- High score with no obvious risk or coaching need.
- Reply is already long or guidance would be repetitive.

### Blending into reply

Extend `execute_agent` or its prompt context to accept `coach_guidance`.

Prompt instruction:

```text
## 教练融合
如存在“教练引导”，请在回答末尾自然融入 1-2 句销售建议。
不要暴露用户评分、等级、内部维度名或后台分析字段。
不要使用“系统检测到”这类表达。
```

The final user-facing reply should sound natural:

```text
另外，见客户前建议你先把对方的角色、目标和决策链补全，再决定主推哪一类价值点。这样后面的提问会更稳。
```

Realtime guidance must be logged in `coach_realtime_observations` whether applied or suppressed.

## Milestones, Rank, Level, Rewards

### Milestones

Dimension milestones:

- 6 dimensions x 12 thresholds = 72.
- Unlock when `old_score < threshold <= new_score`.
- Unlock only once.

All-dimension milestones:

- 12 thresholds.
- Unlock when all six scores are greater than or equal to threshold.

### Rank

Based on total points:

| Rank | Points |
|---|---:|
| 青铜 | 0+ |
| 白银 | 100+ |
| 武士 | 300+ |
| 大师 | 600+ |
| 王者 | 1000+ |

Points sources:

- Effective sales conversation: +10.
- Effective sales topic: +5.
- Quality behavior signal: +1..+5.
- Daily point cap: 50.

Milestone unlocks should not add points in the first version to avoid double incentives.

### Level

Based on unlocked milestone count:

| Milestones | Level |
|---:|---:|
| 0 | 0 |
| 1-5 | 1 |
| 6-12 | 2 |
| 13-20 | 3 |
| 21-30 | 4 |
| 31-40 | 5 |
| 41-50 | 6 |
| 51-60 | 7 |
| 61-70 | 8 |
| 71-79 | 9 |
| 80+ | 10 |

### Rewards

Reward triggers:

| Reward type | Probability | First-version behavior |
|---|---:|---|
| `text_milestone` | 100% | Create record and send DingTalk text. |
| `voice_encouragement` | 30% | Create `suggested` record only. |
| `badge` | 20% | Create badge reward record. |
| `red_packet_reminder` | 10% | Create admin reminder record only. |

Rules:

- Maximum 3 reward notifications per user/day.
- Merge multiple same-day milestone messages.
- Maximum 1 red packet reminder per user/day.
- Insufficient data does not trigger rewards.
- Negative deltas do not remove unlocked milestones.
- Rank should not downgrade in first version; competency scores can decline.

## Admin and Console Views

Add Agent-level coach pages:

```text
/agents/:agentId/coach
/agents/:agentId/coach/users/:userId
/agents/:agentId/coach/evaluations
/agents/:agentId/coach/rewards
/agents/:agentId/coach/settings
```

### Coach Dashboard

Shows team-level coaching status:

- Evaluated users today.
- Weekly active sales users.
- Average six-dimensional score radar.
- Score trends.
- Top improving users.
- Low-score users.
- Most common surface blocks.
- Most common deep blocks.
- Weekly milestone unlock count.
- Reward delivery count.
- Evaluation skipped/failed count.

### User Coach Profile

Shows individual growth profile:

- User identity.
- Rank, level, total points.
- Six current scores.
- 7/30 day trends.
- Latest observations.
- Latest iceberg analysis.
- Unlocked milestones.
- Recent rewards.
- Linked recent conversations.
- Suggested next training task.

### Daily Evaluations

Shows operational job records:

- Date.
- User.
- Status.
- Conversation count.
- Delta summary.
- Parse/schema status.
- Error reason.
- Manual rerun/dry-run actions.

### Rewards

Shows reward operations:

- User.
- Reward type.
- Related milestone.
- Status.
- Channel.
- Created/delivered time.
- Failure reason.
- Admin processing state for red packet reminders.

### Coach Settings

First version can use defaults and environment/config values, but API and page should eventually expose:

- Realtime coach enabled.
- Daily evaluation enabled.
- Evaluation time.
- Minimum sample count.
- Realtime guidance daily limit.
- Reward notification daily limit.
- Initial score.
- Voice reward enabled.
- Red packet reminder enabled.
- Evidence quote max length.

## Backend API

Add `sales_agent/api/routes/coach.py`.

User/report APIs:

```text
GET /agents/{agent_id}/coach/users/{user_id}
GET /agents/{agent_id}/coach/users/{user_id}/scores
GET /agents/{agent_id}/coach/users/{user_id}/iceberg
GET /agents/{agent_id}/coach/users/{user_id}/milestones
GET /agents/{agent_id}/coach/users/{user_id}/rewards
GET /agents/{agent_id}/coach/users/{user_id}/observations
GET /agents/{agent_id}/coach/users/{user_id}/report?type=full
```

Admin APIs:

```text
GET /agents/{agent_id}/coach/dashboard
GET /agents/{agent_id}/coach/users
GET /agents/{agent_id}/coach/users/{user_id}/trend
GET /agents/{agent_id}/coach/evaluations
POST /agents/{agent_id}/coach/admin/run_daily
POST /agents/{agent_id}/coach/evaluations/{evaluation_id}/rerun
GET /agents/{agent_id}/coach/rewards
PATCH /agents/{agent_id}/coach/rewards/{reward_id}
GET /agents/{agent_id}/coach/settings
PATCH /agents/{agent_id}/coach/settings
```

Security expectations:

- Agent ownership must be verified on every route.
- The route must enforce `(tenant_id, agent_id)` filtering.
- Sales users should only access their own reports unless admin authorization exists.
- If RBAC is not yet implemented, route signatures should still preserve `viewer_user_id`/admin boundary rather than baking in permanent open access.

## DingTalk Behavior

DingTalk single-chat handling should support:

- User says `我的评分`: return scores report.
- User says `冰山`: return iceberg report.
- User says `教练报告`: return full report.
- Milestone text rewards are sent as private messages when unlocked.
- Report-only conversations are logged with `task_type="coach_report"` and excluded from daily scoring input.

DingTalk report rendering can be Markdown text in the first version.

## Implementation Phases

### Phase 1: Coach data foundation and daily scoring MVP

Deliver:

- Coach models/tables.
- Six-dimension constants.
- Daily evaluation service.
- Strict LLM rubric prompt and JSON validation.
- Manual daily evaluation endpoint.
- Score updates and observation records.
- Basic `scores` report.
- DingTalk `我的评分` support.

Acceptance:

- A user with at least 3 valid sales messages can be evaluated manually.
- Six deltas are generated and validated.
- Scores start from 50, clamp to `0..100`, and update once per day.
- Non-zero deltas have reasons and evidence.
- Re-running the same day does not double-apply deltas.
- `我的评分` returns a six-score report.

### Phase 2: Iceberg and full reports

Deliver:

- Iceberg analysis persistence.
- `CoachIntentRouter`.
- `iceberg` and `full` reports.
- ChatPipeline early interception for coach reports.
- DingTalk support for `冰山`, `我的能力`, `教练报告`.

Acceptance:

- `冰山` returns latest surface/deep blocks.
- Surface blocks are capped at 5, deep blocks at 4.
- Blocks include severity and evidence.
- Insufficient data is handled cleanly.
- `教练报告` includes scores, basic level state, iceberg, and next suggestions.

### Phase 3: Milestones, rank, level, rewards

Deliver:

- Seed 84 milestones.
- Points, rank, and level updates.
- User milestone unlock service.
- Reward creation service.
- DingTalk text milestone rewards.
- Suggested records for voice/badge/red packet.

Acceptance:

- Crossing a threshold unlocks a milestone once.
- All-dimension thresholds unlock correctly.
- Daily points are capped.
- Rank updates by points.
- Level updates by unlocked milestone count.
- Multiple same-day milestone notifications are merged.

### Phase 4: Realtime coach guidance

Deliver:

- `coach_observe` service.
- `coach_guidance` service.
- Current score loading.
- Guidance blending into `execute_agent` context/prompt.
- Realtime observation logging.
- Suppression and daily limit rules.

Acceptance:

- Sales scenes produce scene hints.
- Low-score users receive specific suggestions.
- Mid-score users receive directional reminders.
- High-score users are usually suppressed.
- Replies do not expose internal scores or dimension labels.
- Guidance goes through existing risk checks.
- Daily guidance limit is enforced.

### Phase 5: Coach admin console

Deliver:

- Coach Dashboard page.
- User Coach Profile page.
- Daily Evaluations page.
- Rewards page.
- Coach Settings page or config API.
- Agent route integration.

Acceptance:

- Admin can see team average six scores.
- Admin can inspect an individual user profile.
- Admin can view 7/30 day trends.
- Admin can view daily evaluation successes/skips/failures.
- Admin can view and update reward records.
- Admin can configure basic coach settings.

## Testing Requirements

Unit tests:

- Competency dimension constants.
- Daily delta validation.
- Score clamp logic.
- Idempotency logic.
- Milestone unlock logic.
- Rank and level calculation.
- Coach intent routing.
- Realtime guidance suppression.

Integration tests:

- Manual daily run creates scores and observations.
- Re-running same date does not double score.
- Insufficient data creates skipped evaluation.
- Coach report request bypasses normal sales task routing.
- Agent scoping prevents cross-Agent score leakage.
- DingTalk report trigger returns report text.

Regression tests:

- Existing `/agent/chat` behavior still works for normal sales tasks.
- Existing conversation scoring task still works.
- Existing Agent instance APIs continue to pass.

## Acceptance Criteria

- [ ] Coach data tables exist and are scoped by `tenant_id`, `agent_id`, and `user_id`.
- [ ] Six competency dimensions are defined with stable keys and Chinese labels.
- [ ] Manual daily evaluation can score a user/day from real conversation data.
- [ ] Daily evaluation is idempotent and cannot double-apply score deltas.
- [ ] Scores start from 50 and clamp to `0..100`.
- [ ] Deltas are integers in `-3..+3`.
- [ ] Non-zero deltas require reasons and evidence quotes.
- [ ] Iceberg analysis stores surface and deep blocks with evidence.
- [ ] Coach report intent supports `scores`, `level`, `iceberg`, `milestones`, `rewards`, and `full`.
- [ ] DingTalk users can request `我的评分`, `冰山`, and `教练报告`.
- [ ] Milestones unlock only once and include 72 dimension + 12 all-dimension definitions.
- [ ] Rank, level, and reward records update after successful evaluations.
- [ ] Realtime coaching can blend 1-2 natural suggestions into normal replies.
- [ ] Realtime coaching does not expose scores, levels, internal dimension names, or backend labels.
- [ ] Coach failures do not break normal chat replies.
- [ ] Admin APIs expose dashboard, user profile, evaluations, rewards, and settings data.
- [ ] Tests cover scoring, reports, milestones, realtime guidance, idempotency, and Agent isolation.

## Open Decisions

1. Whether `force_recompute=true` reverses old deltas in Phase 1 or is blocked until revision support exists. Recommendation: block non-dry-run recompute in Phase 1.
2. Whether rank can downgrade. Recommendation: no downgrade in first version.
3. Whether cloned Agents should copy coach history. Recommendation: no; coach history is runtime/user data and must not be cloned.
4. Whether daily evaluation should run by server timezone or tenant timezone. Recommendation: use coach settings timezone, fallback to tenant/server timezone.
5. Whether voice rewards should use TTS in first version. Recommendation: record suggested voice rewards only.

## Worker Handoff Summary

Implement this as a new Coach subsystem. Start with Phase 1 and Phase 2 before building realtime guidance or UI. The first useful deliverable is: daily manual evaluation, persisted six scores with evidence, and DingTalk `我的评分` / `冰山` / `教练报告` responses. Keep all data Agent-scoped and keep normal chat behavior stable.

---

*Phase: sales-coach-growth-system*
*Spec created: 2026-06-15*
*Next step: create an implementation plan for Phase 1 and Phase 2, then execute with tests before adding milestones, realtime guidance, and console pages.*
