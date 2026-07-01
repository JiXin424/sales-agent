# Implement — Graph Debug branch tree（A3）

## Phase A — 后端（扩展 list_checkpoints）

- [ ] A1. `CheckpointSummary` 加 `parent_checkpoint_id: str | None`;`list_checkpoints` 从 `snapshot.parent_config["configurable"]["checkpoint_id"]` 填（`parent_config` 为 None 时填 None）。

验证:进程内 probe（共享 InMemorySaver,fork 自非最新 checkpoint 制造分支）—— 子 checkpoint 的 `parent_checkpoint_id` 指向 fork 点;根为 None。

## Phase B — 前端（reactflow DAG）

- [ ] B1. 装 `reactflow` + dagre 布局包（确认包名 / 版本 / React 兼容）;更新 `console/package.json`。
- [ ] B2. `types.ts`:`CheckpointMeta` 加 `parent_checkpoint_id: string | null`。
- [ ] B3. `<CheckpointNode>` 自定义节点（step / node / ts / source,`source=='update'` 标 fork）。
- [ ] B4. `checkpoints` → reactflow `nodes`（id=checkpoint_id）+ `edges`（parent→child）+ dagre 布局 + 渲染;**替换 A1 的 `Steps` 时间轴区域**。
- [ ] B5. 点节点 → `getCheckpointState` → 复用 `JsonNode` viewer;选中高亮。

验证:`cd console && npm run build`。

## Review Gate

- [ ] `parent_checkpoint_id` probe 正确（指向 fork 点）;reactflow 与 React 兼容、build 通过、体积可接受;A1/A2 不回归（list 加字段向后兼容）;除 reactflow/dagre 外无新依赖;reactflow 失败有回退（Ant Design Tree）。

## Phase C — 验证

- [ ] 进程内:list 加字段 + parent 正确 + 根 None。
- [ ] 待环境:浏览器 reactflow DAG 渲染 + 点击节点看 state + fork 分支显示。

## 收尾

- [ ] README / changelog / lessons（parent_config 实证 + reactflow 集成） / design 同步。
- [ ] `task.py finish` + archive;**A1+A2+A3 代码一起建分支提交**。
