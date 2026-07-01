# Graph Debug 分支树（A3）：reactflow DAG 可视化

## Goal

把 checkpoint 时间轴从线性升级为**分支树（DAG）**,可视化 A2 fork 产生的分支,点节点看 state。

## Background

A2 fork 在同 thread 产生分支。probe 确认 `StateSnapshot.parent_config` 提供**精确父血缘**（`metadata.parents` 实测空,A2 design 的悲观判断被纠正）。A3 用 reactflow 画 DAG。

## Requirements

- **R1** 后端:`list_checkpoints` 的 `CheckpointSummary` 加 `parent_checkpoint_id`（从 `snapshot.parent_config`）。
- **R2** 前端:reactflow DAG —— 节点=checkpoint、边=parent→child、dagre 自动布局、自定义节点（step/node/ts/source,`source=='update'` 标 fork）。
- **R3** 点节点 → 复用 A1 `getCheckpointState` + `JsonNode` viewer 看详情。
- **R4** 替换 A1 的 `Steps` 时间轴区域（无 fork 退化为单链,视觉直觉不回归）。
- **R5** 复用 `debug:` 前缀校验（不改隔离）。

## Constraints

- 复用 `list_checkpoints`（仅加字段,向后兼容）;不改 A1/A2 端点形状。
- 新依赖:`reactflow` + dagre（A1/A2 零依赖破例,可视化升级代价,同 mermaid 性质）。
- reactflow 失败回退:Ant Design Tree（H1）。

## Out of Scope

- 跨 thread / 跨会话树;分支间 diff / 对比;祖先链高亮（可选,后续）。

## Acceptance Criteria

- [ ] `list_checkpoints` 返回的每项含 `parent_checkpoint_id`;fork 产生的子 checkpoint 指向 fork 点,根为 null。
- [ ] 前端 reactflow 渲染 DAG;无 fork 时为单链;有 fork 时显示分叉。
- [ ] 点节点展示该 checkpoint 的完整 state（复用 viewer）。
- [ ] `cd console && npm run build` 通过;reactflow 兼容当前 React。
- [ ] A1（只读时间轴）/ A2（fork）不回归（list 加字段向后兼容）。
