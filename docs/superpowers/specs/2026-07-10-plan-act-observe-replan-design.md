# Plan → Act → Observe → Replan 销售动作闭环

- **Status**: Draft（brainstorming 输出，待用户审阅）
- **Date**: 2026-07-10
- **Related**: `2026-07-10-sales-action-cards-reminders-design.md`（现有 sales-action 基建，本设计的复用基础）

## 1. 背景与目标

现有闭环停留在 **用户提问 → Agent 建议 → 创建任务 → 用户完成**——线性、开环。完成动作后系统什么都不记、什么都不学。

目标：升级为 **Plan → Act → Observe → Replan** 闭环：

> 理解推进目标 → 制定动作（带成功信号）→ 用户确认 → 执行/提醒 → 收集客户信号 → 判断商机变化 → 修正下一步

典型轨迹：
1. 用户：「我想推进张总这单。」
2. Agent（Plan）：「当前缺口是技术负责人尚未参与。建议明天下午发案例并争取一次技术交流，**成功信号=张总确认技术负责人和可沟通时间**。是否创建？」
3. 用户确认 → 建卡 + 提醒（Act，复用现有调度器）。
4. 用户完成后，Agent 主动追问结果（Observe）：「约到交流／有回复没推进／出现新异议／没回复」。
5. 用户答「他说最近预算冻结」→ Agent 更新商机判断、撤销旧策略、提出受约束的恢复动作（Replan）。

方向参考微软 Sales Opportunity Agent（汇总商机信号、识别风险、推荐下一步），有明确生产价值。

**设计原则**：最大化复用现有基建（LangGraph checkpoint / memory / 任务调度 / 钉钉卡片 / 幂等回调 / eval），不另起炉灶；v1 无任何自动副作用，所有动作创建仍需用户确认。

## 2. 关键决策（brainstorming 结论）

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| D1 | 闭环锚点 | **SalesActionCard 动作闭环** | 改动最小、复用最充分；数据模型（success_criteria + outcome）是更大盘子的子集，可向「商机闭环」演进 |
| D2 | Observe 触发 | **Chat 主动追问 + LLM 解析** | 走现有图管线，v1 自包含；卡片按钮接线（TODO 桩）作为后续基建 |
| D3 | 信号/结果结构 | **自由文本信号 + 小分类结果 + 命中判定** | 灵活贴合自然对话，Replan 可按标签分支，eval 可回归 |
| D4 | Replan 产出 | **建议下一动作 + 写客户记忆 + 撤销旧策略**（用户确认才建） | 完整复现「更新判断/撤销/重提」三步，且不自动替用户行动 |
| D5 | 架构 | **Graph-native**（Plan/Observe/Replan 为图节点） | 闭环状态与对话上下文同源，Observe/Replan 天然带上下文 |

## 3. 现状与复用地图（探索结论，带 file:line）

**可直接复用（ready）**
- Postgres checkpoint：线程键 `online:<tenant>:<agent>:<channel>:<session_user_id>`（按「销售员」非「客户」），跨轮状态自动持久化（`checkpoint_runtime.py:8`、`online_conversation.py:170`）。
- `SalesActionCard` 生命周期：`pending→done/cancelled`，幂等 + 行锁，状态机成熟（`repository.py:43,254-310`）。
- 提醒调度器：生产级（SKIP LOCKED 认领、退避、早晚 digest），worker 进程（`scheduler.py:143-210`）。
- 单轮 eval：EvalSuite/EvalCase/EvalRun + deepeval（`models/eval.py`）。

**闭环缺的 6 块（本设计填补前 5 块，第 6 块分阶段）**
1. Observe 无处落——完成是纯 flag 翻转，`action_completed` payload 仅 `{"event_id":...}`（`repository.py:298-310`）。
2. 建动作时无成功信号——无 `success_criteria`；`agent_advice` 恒空。
3. 无 Replan 钩子——`sales_action_suggestion_node` 只看当前消息（`nodes.py:888`）。
4. 无 per-customer 关联——仅自由文本 `customer_name`；memory 按销售员存（`profile_recall.py`）。
5. 钉钉卡片按钮未接线——`normalize_dingtalk_card_callback` 是 TODO 桩，卡片纯 markdown（`sales_action_callbacks.py:149-158`）。
6. 多轮 eval 不存在——EvalCase 单 input→单 output（`models/eval.py:47-82`）。

**最佳挂载点**（探索结论）：Plan 增强建议节点；Observe 插在 chat→enqueue_memory_candidate 之间；Replan 紧随 Observe 同轮跑；跨轮靠 checkpoint 的 `pending_observe_action_id`（同 `pending_clarification` 范式）。

## 4. 数据模型（闭环契约脊柱）

所有新增列 **nullable**，旧动作零影响，可灰度（靠 `sales_actions.enabled` + 新子开关）。

### `SalesActionCard` 新增列（新增 alembic revision）
| 列 | 写入者 | 含义 |
|---|---|---|
| `success_criteria` | Plan | 自由文本一句话，Observe 的评判基准 |
| `pursuit_goal` | Plan | 该动作服务的目标（如「推进张总这单」）；「撤销同目标旧动作」的分组键（v1 自由文本，不引入商机实体） |
| `outcome_tag` | Observe | `{achieved, partial, new_obstacle, no_response}` 之一 |
| `outcome_note` | Observe | 结果语义摘要（如「他说最近预算冻结」） |
| `outcome_met_signal` | Observe | bool，LLM 判是否命中 success_criteria |
| `outcome_captured_at` | Observe | 时间戳 |

### `AtomicMemory` 新增列
- `customer_scope`（nullable Text）：Replan「更新商机判断」= 写一条带 `customer_scope=客户名` 的事实；`profile_recall` 召回时按 customer 过滤。**复用现有表与召回逻辑，不新建表**。

### `SalesActionEvent`
- 复用现有 cancel 路径，新增 `reason_code="superseded_by_replan"`，区分「用户取消」与「Replan 撤销旧策略」。

## 5. 图与流程

核心洞察：Plan/Observe/Replan **不全是全新独立节点**。Plan 是「建议生成」的增强；Observe/Replan 复用现有 `sales_action_pending_clarification` 的「跨轮 pending」范式。

### Plan（生成带成功信号的推进建议）
- 不新增节点——**增强 `sales_action_suggestion_node`（post-chat）与 command 路径的 LLM 抽取**：除 title/time 外再生成 `success_criteria` + `pursuit_goal`，存入 `suggested_sales_action` state；用户确认建卡时 card 带上这两字段。
- chat 回答负责「诊断缺口」，建议节点负责「动作 + 成功信号」。

### Observe（收集结果）—— chat 追问，两种入口
| 入口 | 触发 | 行为 |
|---|---|---|
| 内联 | 完成时同消息给了结果（「打完了，他说预算冻结」） | complete 路径直接进 Observe 解析 |
| 延迟 | 完成但没给结果，或动作到期 | 设 `pending_observe_action_id`（跨轮存活），发追问卡片「约到／有回复没推进／新异议／没回复」；下轮 `normalize_turn` 识别后路由进 observe 节点 |

- 新增 `sales_action_observe_node`：LLM 把回复解析成 `outcome_tag/note/met_signal` 写回 card。
- 范式与现有 `pending_clarification` 完全一致（同样排除出 `TURN_SCOPED_DEFAULTS`）。

### Replan（据结果修正）—— Observe 之后同轮跑
新增 `sales_action_replan_node`，按 `outcome_tag` 分支：
- `achieved` → 记「赢」事实到客户记忆，轻收尾（可选建议下一阶段动作）。
- `partial / new_obstacle / no_response` → ① 把 `outcome_note` 写成带 `customer_scope` 的客户事实；② 取消同 `pursuit_goal` 仍 pending 的旧动作（`reason=superseded_by_replan`）；③ 生成**受新约束**的下一个动作建议（带新 `success_criteria`）追加到回复——**用户确认才建**。

### 路由增量
- `normalize_turn`：若 `pending_observe_action_id` 存在 → `flow_action="sales_action_observe"`。
- 新边：`sales_action_observe → sales_action_replan → log_control_response → END`（终态走 log，与现有 clarification 路径一致）。
- 新 state 字段：`pending_observe_action_id`、`replan_suggestion`、`replan_cancelled_ids`（+ 扩 `suggested_sales_action`）。

### 与调度器（inaction 信号）
- **v1**：动作过 `scheduled_at` 仍 pending → 调度器发一条追问 reminder（复用 reminder/delivery 表，新 kind `observe_prompt`）。
- **stretch（v1.1）**：追问后 N 天仍无回复 → 调度器自动写 `outcome_tag=no_response` 并触发 Replan（真正「不作为=信号」）。

## 6. LLM 契约

### Observe（observe_node，temp=0，结构化 JSON，schema 校验）
- **入**：用户回复 + 该动作 `success_criteria` + 动作/客户上下文。
- **出**：`outcome_tag ∈ {achieved, partial, new_obstacle, no_response}`、`outcome_note`、`met_signal: bool`、`confidence`。
- **鲁棒**：复用 evidence_router 的「2 次重试 + 确定性兜底」；解析失败 → 关键词启发式给 tag、`met_signal=false`。
- **可复现**：prompt 走 DB 版本化（与现有 prompt 版本管理一致），版本号进 state。

### Replan（replan_node，temp=0，结构化 JSON）
- **入**：动作（title/success_criteria/pursuit_goal/customer）+ outcome + 该客户 `customer_scope` 记忆（profile_recall 过滤）。
- **出**：`memory_fact`（待写客户事实或 null）、`cancel_siblings: bool`、`next_action: {title, action_type, success_criteria_new, suggested_time, rationale} | null`、`message`（给用户的诊断+建议文本）。
- 仅 `achieved` 可跳过 memory/cancel/next_action。

### 幂等与安全
- 全程在同一 turn 的 advisory lock 内；写 outcome 用「`outcome_tag IS NULL` 才 UPDATE」防重。
- memory 写复用 atomic_memory `normalized_key` 去重；cancel 复用幂等 `cancel_action`。
- **v1 无任何自动副作用**——Replan 只产出「建议」，建卡仍需用户确认。

## 7. Eval（多轮 eval 不存在 → v1 走「分阶段单轮」）

不发明多轮轨迹 runner，把三个智能件各做成**独立 fixture 套件**，复用 EvalSuite/EvalCase/EvalRunner + deepeval：

| 套件 | 输入 | 评判 | 类型 |
|---|---|---|---|
| **Observe 分类** | 回复 + success_criteria | `outcome_tag` 精确匹配 + `met_signal` 准确率 | 确定性回归，最高价值 |
| **Replan 约束尊重** | 动作 + 非achieved outcome + 客户记忆 | LLM-judge：next_action 是否真受新障碍约束（「预算冻结」就不该再假定有预算）+ must_not_include | LLM-judge |
| **Plan 信号质量** | 推进目标 + 客户上下文 | LLM-judge：success_criteria 是否具体、可证伪、对症缺口 | LLM-judge |

- Observe/Replan 走**节点级**直调（构造 state 直接调 node，最确定）；Plan 走触发该流的极简 turn。
- 这些 fixed suite 可直接喂 `sales-agent-iteration` MCP 做 prompt 迭代——闭环 eval 反哺 prompt。
- **stretch（v1.1）**：真正的多轮轨迹 runner（Plan→Act→Observe→Replan 整条评分）。

## 8. 灰度 / 回滚 / 验证

- **开关**：新增 `sales_actions.pursuit_loop_enabled`（默认 false），挂在已有 `sales_actions.enabled` 之下。关掉时：建议无 success_criteria、不路由 observe/replan → **完全等同今天行为**。
- **向后兼容**：新列全 nullable，旧卡零影响。`atomic_memory.customer_scope` 召回时 `WHERE customer_scope = ? OR customer_scope IS NULL`，旧 per-user 记忆照常召回。
- **上线**：先开 dev 本机 prod2（taishan+taishankaifa2，不碰 test/prod3），**查 stream 容器日志**确认 observe/replan 节点无 crash、连上 stream（生产入口验证铁律：HTTP 200 ≠ 健康）。
- **回滚**：翻 `pursuit_loop_enabled=false` 即可，**无需 migration 回滚**（列留着不用）；残留 `pending_observe_action_id` 在关时被 normalize_turn 忽略 → 落回 chat，安全。
- **风险半径**：LLM 误解析 outcome → Replan 建错议；但建卡要用户确认、无自动副作用，**最坏情况=一条可忽略的建议**，低半径。`cancel_siblings` 仅取消 `pursuit_goal` 精确文本匹配 + pending 的动作，每次 cancel 写 event 留审计。

## 9. 范围：v1 vs stretch

**v1（本设计落地）**
- 数据模型：6 新列 + `atomic_memory.customer_scope` + cancel reason。
- 图：Plan 增强 + observe/replan 两节点 + 路由/state 增量。
- Observe：chat 追问双入口（内联 + 跨轮 pending）。
- Replan：建议 + 写记忆 + 撤销旧策略（无自动副作用）。
- 调度器：到期发 `observe_prompt` 追问。
- eval：Observe/Replan/Plan 三套分阶段单轮 fixture。

**stretch（v1.1+，本设计不实现）**
- 钉钉卡片按钮接线（`normalize_dingtalk_card_callback` + 卡片模板配按钮）→ Observe 可走按钮直采。
- 调度器自动 `no_response` Replan（不作为=信号）。
- 多轮轨迹 eval runner。
- 商机实体（per-customer 长期跟踪）→ 从「动作闭环」迈向真正的「商机推进闭环」（Option B）。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM 误解析 outcome → 错误 Replan | 用户确认才建卡；无自动副作用；最坏=可忽略的建议 |
| `cancel_siblings` 误删动作 | 仅精确 `pursuit_goal` 文本匹配 + pending；每次写 event 审计；用户可在 replan 消息里看到并重建 |
| `pursuit_goal` 自由文本分组模糊 | v1 已知限制；stretch 引入商机实体后用结构化 ID 分组 |
| per-customer 记忆召回噪声 | `customer_scope` 过滤 + 现有 memory 置信度/时效排序 |
| checkpoint 线程按销售员而非客户 | 同一销售员多客户共享线程；靠 `pending_observe_action_id` 精确指向当前动作，不串扰 |

## 11. 验收标准（v1）

1. 用户说推进目标 → Agent 给出带 `success_criteria` 的动作建议；确认后建卡带 success_criteria + pursuit_goal。
2. 完成动作（含/不含结果）→ 系统捕获 outcome（tag/note/met_signal）写回 card。
3. 非 achieved outcome → 系统写客户记忆 + 取消同目标 pending 旧动作 + 给出受约束的下一动作建议（用户确认才建）。
4. 动作到期未完成 → 调度器发追问 reminder。
5. `pursuit_loop_enabled=false` 时行为与今天完全一致；翻 true 即启用。
6. 三套 eval fixture 通过；dev prod2 stream 容器无 crash。
