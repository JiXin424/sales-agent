# Design: Plan → Act → Observe → Replan

完整设计文档：`docs/superpowers/specs/2026-07-10-plan-act-observe-replan-design.md`

## 架构摘要

- **方案**: Graph-native（Plan/Observe/Replan 为 Online Graph 节点）
- **锚点**: SalesActionCard（动作闭环）
- **Plan**: 增强现有 `sales_action_suggestion_node` + command 路径 LLM 抽取，追加 success_criteria + pursuit_goal
- **Observe**: 新增 `sales_action_observe_node`（chat 追问双入口：内联+跨轮 pending），LLM 解析 outcome
- **Replan**: 新增 `sales_action_replan_node`（Observe 后同轮跑），写客户记忆 + cancel_siblings + suggest next
- **Eval**: 分阶段单轮 fixture（Observe 分类 / Replan 约束 / Plan 信号质量）
- **灰度**: `sales_actions.pursuit_loop_enabled` 子开关，翻 false 即回滚
