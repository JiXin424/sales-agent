# 图调试页 reactflow 重画设计

> 日期：2026-07-06
> 状态：设计已确认，待写实现计划
> 背景：图调试页「图结构」tab 当前用 mermaid 静态 svg 渲染，`theme:'default'` 默认皮肤（浅紫底 `#ececff`+紫边）与 antd 控制台风格冲突、字体不统一；节点全是默认圆角矩形无视觉层级；`.gd-mermaid svg` 用 `width/height:100%!important` 强行拉伸，少节点空旷、多节点挤一起；且是静态 svg 不能点。用户反馈「图太丑」，方向定为「调布局」。经 brainstorming 确认改用 reactflow 重画。

## 决策摘要（brainstorming 结论）

| 维度 | 决策 | 备选 |
|------|------|------|
| 画法 | **reactflow 重画**（依赖 `reactflow@11.11.4`+`dagre@0.8.5` 已在，`CheckpointDAG.tsx` 有现成模板） | mermaid TD（现状）/ mermaid LR |
| 节点样式 | **类型徽标 + 阴影**（B 方案） | 紧凑卡片 / 图标+prompt名 |
| 布局 | **LR 宽松**（`rankdir:LR, ranksep:80, nodesep:40`） | LR 紧凑 / TB |
| 节点点击 | **高亮 + 联动表**（选中节点高亮、上下游路径保留、其余变暗；右侧对照表滚到对应行） | 弹详情面板 / 双击跳转 / 仅 hover |
| 范围 | **只换主图区**，不碰 CheckpointDAG | 连 CheckpointDAG 一并统一 |

## 第 1 节：架构与组件边界

**目标**：把图调试页「图结构」tab 的 mermaid 静态 svg 换成 reactflow 交互图，解决 mermaid 默认皮肤廉价、被拉伸空旷/挤、不能点的问题。保留侧边栏+Header、执行轨迹可折叠、节点↔Prompt 对照表。

**新增组件**（都在 `console/src/pages/Agents/`）：

1. **`GraphFlow.tsx`** — reactflow 主图容器。职责：接收 `GraphInfo`（来自 `/graphs` API 的 `nodes`/`edges` 结构化字段），转成 reactflow 节点/边，dagre LR 布局，渲染 `<ReactFlowProvider>` + `<Background>` + `<Controls>`，管理选中节点 state，`onNodeClick` 回调上抛。
2. **`GraphNode.tsx`** — 自定义节点组件。职责：纯展示——类型徽标（LLM蓝/子图橙/函数灰小药丸）+ 节点名（加粗+类型色）+ 中文说明（灰小字）。选中态加蓝色 boxShadow 高亮。无业务逻辑，只读 props。

**复用**：`CheckpointDAG.tsx` 的 dagre 布局函数（`layoutGraph`）和 reactflow 配置（`fitView`/`minZoom`/`maxZoom`/`Background`/`Controls`）作为模板，方向改 LR、间距用 `ranksep:80 nodesep:40`。

**改动**：`GraphDebugPage.tsx` 的 `.gd-mermaid-wrap` 内容从 `mermaid.render` svg 换成 `<GraphFlow>`；删除 `mermaid` import 和 `mermaidSvg` state 及其 useEffect（mermaid 不再使用）。`NodeLegend` 保留（三色图例仍需要，配色与节点徽标一致）。

**不动**：后端 `graph_debug.py` 的 mermaid 生成逻辑（`/graphs` 已返回 `nodes`/`prompt_map` 结构化字段，reactflow 直接消费 `nodes`；只需新增 `edges` 字段，见第 2 节）。`CheckpointDAG.tsx`、右侧执行轨迹、输入框、对照表全部不动。

## 第 2 节：数据流

**数据源**：`/agents/{agent_id}/graph-debug/graphs` 已返回结构化 `GraphInfo`，reactflow 直接消费 `nodes`，**但需新增 `edges` 字段**（当前边信息只埋在 mermaid 字符串里，reactflow 需要显式边列表）。

**后端改动**（方案甲·单一事实源，已确认）：
- `graph_debug.py` 新增 `EdgeInfo` Pydantic model：`source: str` / `target: str`。
- `GraphInfo` 加 `edges: list[EdgeInfo] = []`。
- `list_graphs` 里从 `compiled.get_graph().edges` 取边填入（与 `nodes` 同源，`g.edges` 每条有 `.source`/`.target`）。

```python
class EdgeInfo(BaseModel):
    source: str
    target: str

class GraphInfo(BaseModel):
    id: str; name: str; mermaid: str
    node_count: int; edge_count: int
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []      # ← 新增
    prompt_map: list[PromptMapping] = []
```

**前端类型**（`console/src/api/types.ts`）同步加 `edges: EdgeInfo[]`。

**前端转换**（`GraphFlow.tsx` 内）：

```
GraphInfo.nodes  →  reactflow nodes  (id / data: {name, desc, type, calls_llm})
GraphInfo.edges  →  reactflow edges  (id / source / target)
                   →  dagre LR 布局算 x/y  →  填回 node.position
```

**`__start__`/`__end__` 处理**：`NodeInfo` 已排除它们（后端 `_build_node_infos` 跳过），但边会引用它们作 source/target。前端在 `GraphFlow` 里补两个虚拟节点（id=`__start__`/`__end__`，type 特殊标记），避免改后端 nodes 逻辑。虚拟节点用小椭圆样式（见第 3 节）。

**选中态联动**：`GraphFlow` 持 `selectedNodeId` state，`onNodeClick` 设置它 → 高亮该节点 + 调 `onSelect(nodeId)` 回调 → `GraphDebugPage` 收到后展开「节点↔Prompt 对照」Collapse 并 `scrollIntoView` 滚到对应行 + 该行背景高亮 2 秒。

## 第 3 节：节点视觉与高亮

**`GraphNode.tsx`** 三要素（B 方案·类型徽标+阴影）：

```
┌─────────────────────────────┐
│ [LLM]  context_resolution   │  ← 徽标(药丸) + 节点名(加粗+类型色)
│ 话题恢复/过期 + 上下文判定    │  ← 中文说明(灰小字 #595959)
└─────────────────────────────┘
```

**类型配色**（与 NodeLegend 图例一致，单一事实源）：

| 类型 | 徽标文字 | 徽标底/字 | 节点边框 | 节点名色 |
|------|---------|----------|---------|---------|
| LLM 节点 (`calls_llm`) | `LLM` | `#1677ff` / `#fff` | `#1677ff` 1.5px | `#0958d9` |
| 子图节点 (`type=subgraph`) | `子图` | `#ff9800` / `#fff` | `#ff9800` 1.8px | `#e65100` |
| 纯函数节点 | `函数` | `#8c8c8c` / `#fff` | `#d9d9d9` 1.2px | `#262626` |

节点底色统一 `#fff`，圆角 8px，padding `8px 12px`，微阴影 `0 1px 3px rgba(0,0,0,0.06)`。系统字体（`-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`，与 antd 一致）。

**判定优先级**：`calls_llm` 优先于 `type`——`if calls_llm → LLM` `else if type==subgraph → 子图` `else → 函数`。防御性写法（后端已保证一个节点不会同时是 LLM 和子图）。

**特殊节点**：`__start__`/`__end__` 用小椭圆（rx≈16, ry≈8），`#fafafa` 底 `#d9d9d9` 边，灰字 `START`/`END`，无徽标无说明。

**高亮规则**（高亮+联动表）：

- **选中节点**：边框加粗到 2.5px + `boxShadow: 0 0 0 3px rgba(22,119,255,0.2)`（蓝色光晕，与 CheckpointDAG 选中态一致）。
- **上下游路径**：选中后，沿边遍历找出该节点的所有祖先和后代节点 id 集合 → 这些节点保持正常不透明度，**其余节点 `opacity: 0.25`**；选中节点到祖先/后代的边保持原色加粗，其余边 `opacity: 0.15`。
- **联动表**：`GraphDebugPage` 收到 `onSelect(nodeId)` → 展开「节点↔Prompt 对照」Collapse（`defaultActiveKey` 受 highlightedNode 控制）→ 对应行加 `gd-prompt-row-highlight` class（黄色背景 2 秒淡出动画）。antd Table 无简单行 ref，不强制 scrollIntoView（Collapse 展开后高亮行通常在视口内；行靠后时用户可手动滚）。
- **取消选中**：点空白处或再点同一节点 → 清除高亮，联动表恢复。

**实现注意**：上下游遍历在 `GraphFlow` 内做（拿到 `nodes`/`edges` props，建邻接表正反向 BFS），结果传给 `GraphNode` 的 `dimmed` prop 控制透明度。只改 className/opacity，不触发 reactflow 全图重渲染。

## 第 4 节：布局与容器

**主图区**（替换 `.gd-mermaid-wrap` 的 mermaid svg）：

```tsx
<div className="gd-flow-wrap">           {/* flex:1, 撑满 tabpane 剩余 */}
  <GraphFlow graph={g} onSelect={handleSelectNode} />
</div>
```

`.gd-flow-wrap`：`flex:1; min-height:0; position:relative; border:1px solid #f0f0f0; border-radius:6px; overflow:hidden; background:#f7f9fc`。reactflow 需要明确高度的父容器（`position:relative` + 子 `ReactFlow` `style={{width:'100%',height:'100%'}}`），这是 reactflow 的硬约束（mermaid 不需要）。

**dagre 配置**（LR 宽松）：

```js
layoutGraph(nodes, edges) {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: 'LR', ranksep: 80, nodesep: 40, marginx: 20, marginy: 20 });
  // 节点尺寸: 普通节点 width 180 height 56; start/end 椭圆 width 40 height 24
  nodes.forEach(n => dagreGraph.setNode(n.id, { width, height }));
  edges.forEach(e => dagreGraph.setEdge(e.source, e.target));
  dagre.layout(dagreGraph);
  // 回填 position (中心点 → 左上角)
  nodes.forEach(n => {
    const { x, y } = dagreGraph.node(n.id);
    n.position = { x: x - width/2, y: y - height/2 };
  });
}
```

**reactflow 配置**（复用 CheckpointDAG 模式）：
- `nodesDraggable={false}` `nodesConnectable={false}` `elementsSelectable`（只读图，防误改）
- `fitView` `fitViewOptions={{ padding: 0.15 }}`（首次自适应居中，留 15% 边距）
- `minZoom={0.2}` `maxZoom={2}`（缩放范围）
- `<Background color="#e0e0e0" gap={16} variant="dots" />`（点状网格）
- `<Controls />`（右下缩放控件）
- `proOptions={{ hideAttribution: true }}`（隐藏 reactflow 水印）

**mermaid 退场**：
- `GraphDebugPage.tsx` 删 `import mermaid`、删 `mermaidSvg` state、删渲染 useEffect（行 213-230）。
- `mermaid.initialize`（行 51）删除。
- `.gd-mermaid-wrap` / `.gd-mermaid` / `.gd-mermaid svg` 三条 CSS 删除，换成 `.gd-flow-wrap`。
- `GraphInfo.mermaid` 字段后端**保留**（不删——改后端风险面大，且 `node_count`/`edge_count` 等仍正常用；只是前端不再消费它）。后续可单独清理。

**Tab 切换**：每个 graph_id tab 独立渲染一个 `<GraphFlow>`（key=graph_id），reactflow 实例随 tab 切换销毁重建。`useMemo` 缓存 dagre 布局结果（graph 不变不重算）。

## 第 5 节：测试与验证

**后端测试**（`tests/unit/graph/test_graph_debug.py`）：
- 新增 `TestEdges`：断言三个图的 `edges` 字段非空，且每条 edge 的 `source`/`target` 都在合法节点 id 集合里。合法集合 = `nodes` 的所有 `id` ∪ `{__start__, __end__}`（后端 `nodes` 排除了 start/end，但 edges 会引用它们，故测试集合需显式补上）。
- 断言 online 图 15 条边、chat 14 条、guided-flow 6 条（与现有 `test_counts_match_graph_object` 的 edge_count 一致，cross-check）。
- 现有 33 个测试不回归（`_annotate_node_labels`/`_decorate_mermaid`/`_build_node_infos` 都不动）。

**前端验证**：
- `cd console && npm run build`（tsc + vite）exit 0——重点查 reactflow 自定义节点的 TypeScript 类型（`NodeInfo` → reactflow `Node` 的 data 类型）。
- 不写前端单测（项目前端无 vitest 组件测试先例；reactflow 组件测试 ROI 低，靠 build + 手动验证）。

**手动验证清单**（部署后）：
1. 三个图 tab（chat/guided-flow/online）都能渲染 reactflow 图，dagre LR 布局，fitView 自适应。
2. 节点类型徽标配色正确（LLM 蓝/子图橙/函数灰），中文说明显示。
3. 点节点 → 高亮 + 上下游路径变暗其余变淡 + 右侧对照表滚到对应行高亮。
4. 缩放/拖拽画布正常，Controls 按钮工作。
5. 侧边栏+Header 保留，执行轨迹可折叠，折叠后图占满。
6. 浏览器硬刷新（Ctrl+Shift+R）确认无缓存旧 mermaid 残留。

**部署验证**（lesson #4 生产入口）：
- push → CI → 三台部署后，`docker logs <tenant>-stream` 确认 stream 容器 Up + 钉钉连接 + 无 crash（图调试是前端改动，stream 不受影响，但仍按惯例确认不回归）。

**风险点**：
- reactflow 自定义节点 `data` 的 TypeScript 类型——用 `type GraphNodeData = NodeInfo & { dimmed: boolean }`，`Node` 泛型 `Node<GraphNodeData>`。build 时验。
- dagre 在 reactflow 11 里是 peer 依赖外的手动集成（CheckpointDAG 已验证可行），照搬即可。
- `__start__`/`__end__` 虚拟节点的尺寸特殊处理——dagre setNode 时按椭圆尺寸给 width/height，渲染时 `GraphNode` 按 `id==='__start__'` 分支画椭圆。

## 涉及文件清单

**后端**（2 文件）：
- `src/sales_agent/api/routes/graph_debug.py` — 加 `EdgeInfo` model + `GraphInfo.edges` 字段 + `list_graphs` 填充 edges
- `tests/unit/graph/test_graph_debug.py` — 新增 `TestEdges`

**前端**（5 文件）：
- `console/src/pages/Agents/GraphFlow.tsx` — 新建，reactflow 主图容器
- `console/src/pages/Agents/GraphNode.tsx` — 新建，自定义节点组件
- `console/src/pages/Agents/GraphDebugPage.tsx` — 删 mermaid，换 `<GraphFlow>`，加 onSelect 联动
- `console/src/pages/Agents/GraphDebugPage.css` — 删 `.gd-mermaid*`，加 `.gd-flow-wrap` + 节点样式
- `console/src/api/types.ts` — `GraphInfo` 加 `edges: EdgeInfo[]` + `EdgeInfo` 类型

**不动**：`CheckpointDAG.tsx`、后端 mermaid 生成逻辑（`_annotate_node_labels`/`_decorate_mermaid`/`draw_mermaid`）、`node_metadata.py`、右侧执行轨迹/输入框/对照表。
