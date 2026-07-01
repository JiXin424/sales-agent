# Design — Graph Debug branch tree (A3): reactflow DAG

## 决策（grilling + probe）

- **可视化**:**H3 — reactflow DAG**（用户选定）。分支树本质是 DAG,图库比 Ant Design Tree（列表状）或自写 git-log 连线更贴切。
- **数据**:**`parent_checkpoint_id`** —— probe 确认 `StateSnapshot.parent_config["configurable"]["checkpoint_id"]` 是**精确父血缘**（根 checkpoint 为 `None`）。**纠正 A2 design 的悲观判断**:不需 step+source 启发式重建,`parent_config` 直接给。

## 关键事实（probe 实证）

- `snapshot.parent_config` = `{"configurable":{"thread_id","checkpoint_ns","checkpoint_id": <父>}}`,根为 `None`。
- `checkpointer.alist` 的 `CheckpointTuple` 也带 `parent_config`（备用数据源）。
- `metadata.parents` 实测空（A2 已记 #21），**不用**。
- fork（`update_state` from 非最新 checkpoint）产生分支:子 checkpoint 的 `parent_config` 指向 fork 点。

## 数据流

```
list_checkpoints (加 parent_checkpoint_id) → 前端 checkpoint[] (每个含 id + parent_id)
  → reactflow nodes (id=checkpoint_id) + edges (parent_id → id)
  → dagre 自动布局 → DAG 渲染
  → 点节点 → 复用 A1 getCheckpointState + JsonNode viewer
```

## 后端（扩展现有端点,不新增）

- `list_checkpoints` 的 `CheckpointSummary` 加字段:
  `parent_checkpoint_id: str | None` = `(snapshot.parent_config or {}).get("configurable", {}).get("checkpoint_id")`。
- 不改其他端点 / 形状（加字段,向后兼容）。复用 `_checkpoint_node_label` / `_ensure_debug_thread` / `_compile_with_checkpointer`。

## 前端（reactflow DAG,替换 A1 Steps 时间轴区域）

- **新依赖**:`reactflow` + dagre 布局（`@reactflow/dagre` 或 `dagre` + 手工;实现时确认包名 / 版本,加 `console/package.json`）。A1/A2 零依赖的破例,可视化升级的合理代价（同 mermaid）。
- 自定义节点 `<CheckpointNode>`:显示 `step` / `node` / `ts` / `source`（`source=='update'` 标记 fork 点）。
- 建图:`checkpoints` → `nodes`（id=checkpoint_id）+ `edges`（`parent_checkpoint_id` → `checkpoint_id`）。
- 布局:dagre 自动（TB 或 LR）。
- 交互:点节点 → 调 `getCheckpointState` → 复用现有 `JsonNode` viewer 显示 `values`;选中节点高亮（祖先链高亮可选）。
- **替换 A1 的 `Steps` 时间轴区域**（默认）;无 fork 时 reactflow 退化为单链（不回归视觉直觉）。
- `types.ts`:`CheckpointMeta` 加 `parent_checkpoint_id: string | null`。

## 依赖与风险

- **新依赖 reactflow + dagre**:需确认与当前 React 版本兼容、构建体积可接受、license（reactflow MIT）。失败回退:Ant Design Tree（H1）。
- DAG 布局:dagre 自动;节点极多时可能挤（调试场景量少,可接受）。
- 兼容:`list_checkpoints` 加字段向后兼容（旧前端不读仍工作）。
- 回滚:reactflow 集中在前端新组件,后端仅加一字段;`git revert` + 卸 reactflow 即可。

## 兼容性

- `list_checkpoints` 加字段（向后兼容）。A1/A2 端点形状 / fork 端点不变。

## A3 实现结果（probe + build 实证，2026-07-02）

1. **`parent_checkpoint_id` 精确血缘**：`snapshot.parent_config["configurable"]["checkpoint_id"]` 直接给（根 `None`）。进程内 probe 确认 fork 子的 parent 指向 fork 点。**纠正上文「A3 需从 checkpoint 链重建血缘」——实际 `parent_config` 精确，非启发式**。
2. **reactflow@11 + dagre** 集成成功（React 18 兼容，未触发回退）；bundle +68kB gzip。
3. **fork 检测双重**：结构化（`childCounts>1`）+ `source=='update'`，不依赖单一来源（后端目前不发 source，结构化兜底已正确）。
4. **替换 A1 `Steps`** 为 DAG；A1 state viewer + A2 fork 编辑流程保留不回归。build / tsc / vitest（11）全过。
