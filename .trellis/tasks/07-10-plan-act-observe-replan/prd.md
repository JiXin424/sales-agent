# PRD: Plan → Act → Observe → Replan 销售动作闭环

## 概述

现有销售动作闭环停留在「提问→建议→建任务→完成」的线性开环。本功能将其升级为 Plan→Act→Observe→Replan 闭环：建动作时带成功信号（Plan），完成后捕捉结果信号（Observe），据结果自动修正、写客户记忆、提出受约束的下一步（Replan）。

**architecture**: Graph-native，所有智能件作为 Online Graph 节点

## 需求

1. **Plan**：用户表达推进目标时，Agent 给出带 `success_criteria`（成功信号）的动作建议
2. **Act**：用户确认后建 SalesActionCard（带 success_criteria + pursuit_goal），复用现有调度器提醒
3. **Observe**：动作完成后 Agent 主动追问结果，LLM 解析成结构化 outcome（tag/note/met_signal）
4. **Replan**：非 achieved 结果→写客户记忆+取消同目标旧动作+生成受约束的下一个动作建议（用户确认才建）
5. **Inaction 信号（v1）**：动作到期调度器发追问 reminder；自动 no_response Replan 放 v1.1

## 验收标准

- [ ] 1. 用户说推进目标 → Agent 给出带 `success_criteria` 的动作建议；确认后建卡带 success_criteria + pursuit_goal
- [ ] 2. 完成动作（含/不含结果）→ 系统捕获 outcome（tag/note/met_signal）写回 card
- [ ] 3. 非 achieved outcome → 系统写客户记忆 + 取消同目标 pending 旧动作 + 给出受约束下一动作建议（用户确认才建）
- [ ] 4. 动作到期未完成 → 调度器发追问 reminder
- [ ] 5. `pursuit_loop_enabled=false` 时行为与今天完全一致；翻 true 即启用
- [ ] 6. 三套 eval fixture（Observe 分类/Replan 约束尊重/Plan 信号质量）通过；dev prod2 stream 容器无 crash

## 约束

- v1 无任何自动副作用（建卡仍需用户确认）
- 新列全 nullable，向后兼容，可灰度
- 复用 Postgres checkpoint / memory / 调度器 / 单轮 eval / 幂等回调
- 钉钉卡片按钮接线推迟到 v1.1（v1 走 chat 追问）
- 多轮 eval runner 推迟到 v1.1

## 关联

- **Design**: `docs/superpowers/specs/2026-07-10-plan-act-observe-replan-design.md`
- **Existing infra**: `docs/superpowers/specs/2026-07-10-sales-action-cards-reminders-design.md`
