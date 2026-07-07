# 图调试页 reactflow 重画 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把图调试页「图结构」tab 的 mermaid 静态 svg 换成 reactflow 交互图（类型徽标节点 + dagre LR 布局 + 点节点高亮联动对照表），解决 mermaid 默认皮肤廉价、被拉伸空旷/挤、不能点的问题。

**Architecture:** 后端 `/graphs` API 新增 `edges` 结构化字段（从 `compiled.get_graph().edges` 取，与 `nodes` 同源）。前端新建 `GraphFlow.tsx`（reactflow 容器 + dagre 布局）和 `GraphNode.tsx`（自定义节点），替换 `GraphDebugPage.tsx` 里的 mermaid 渲染。复用 `CheckpointDAG.tsx` 的 dagre/reactflow 模板，方向改 LR、间距 `ranksep:80 nodesep:40`。点节点高亮选中 + 上下游路径保留、其余变暗，并联动右侧「节点↔Prompt 对照表」滚动高亮。

**Tech Stack:** reactflow `^11.11.4`、dagre `^0.8.5`（均已在前端依赖）、antd `^5.22.0`、TypeScript、Vite。后端 FastAPI + Pydantic。

## Global Constraints

- 后端 Python 用 `uv run` 执行（`uv run pytest ...`、`uv run python -c '...'`）。
- 前端命令在 `console/` 目录执行：`cd /root/code/sales-agent/console && npm run build`（含 `tsc -b && vite build`）。
- 节点类型配色单一事实源：LLM=`#1677ff`/`#0958d9`、子图=`#ff9800`/`#e65100`、函数=`#d9d9d9`/`#262626`（与 `NodeLegend` 图例一致）。
- dagre 配置固定：`rankdir:'LR', ranksep:80, nodesep:40, marginx:20, marginy:20`。
- reactflow 配置：`nodesDraggable={false} nodesConnectable={false} fitView minZoom:0.2 maxZoom:2 proOptions={{hideAttribution:true}}`。
- 不动 `CheckpointDAG.tsx`、后端 mermaid 生成逻辑（`_annotate_node_labels`/`_decorate_mermaid`/`draw_mermaid`）、`node_metadata.py`、右侧执行轨迹/输入框。
- `GraphInfo.mermaid` 后端字段**保留**（前端不再消费，但不删后端字段，降低风险）。
- 系统字体：`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`（与 antd 一致）。
- 三个图真实结构：online=11 节点 15 边、chat=12 节点 14 边、guided-flow=5 节点 6 边（来自 `tests/unit/graph/test_graph_debug.py::test_counts_match_graph_object`）。
- 三个图的 `nodes`（`NodeInfo[]`）**不含** `__start__`/`__end__`（后端 `_build_node_infos` 跳过），但 `edges` 会引用它们作 source/target。

---

### Task 1: 后端 GraphInfo 加 edges 字段

**Files:**
- Modify: `src/sales_agent/api/routes/graph_debug.py:55-72`（Pydantic models）
- Modify: `src/sales_agent/api/routes/graph_debug.py:296-329`（`list_graphs` 填充 edges）
- Test: `tests/unit/graph/test_graph_debug.py`（新增 `TestEdges` 类）

**Interfaces:**
- Produces: `EdgeInfo` Pydantic model（`source: str` / `target: str`）；`GraphInfo.edges: list[EdgeInfo]` 字段。前端 Task 3 消费。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/graph/test_graph_debug.py` 末尾追加（先 `from sales_agent.api.routes.graph_debug import EdgeInfo` 到文件顶部 import 块）：

```python
class TestEdges:
    """edges 字段从 compiled graph 取，source/target 必须是合法节点 id。"""

    @pytest.mark.parametrize("graph_id,expected_edges", [
        ("online", 15),
        ("chat", 14),
        ("guided-flow", 6),
    ])
    def test_edge_count_matches_graph_object(self, graph_id, expected_edges):
        """edge 数量与 len(g.edges) 一致（cross-check test_counts_match_graph_object）。"""
        g = _graph(graph_id)
        # 直接调 list_graphs 需要 GRAPH_REGISTRY + async；改用 _build 辅助：
        # edges 在 list_graphs 里从 g.edges 取，这里等价校验 g.edges 数量。
        assert len(g.edges) == expected_edges

    def test_edges_source_target_are_valid_node_ids(self):
        """每条 edge 的 source/target 必须在 nodes ∪ {__start__,__end__} 里。"""
        from sales_agent.api.routes.graph_debug import list_graphs
        import asyncio
        resp = asyncio.run(list_graphs("test-agent"))
        for graph in resp.graphs:
            valid_ids = {n.id for n in graph.nodes} | {"__start__", "__end__"}
            assert graph.edges, f"{graph.id} edges 为空"
            for e in graph.edges:
                assert e.source in valid_ids, f"{graph.id}: edge source {e.source} 不在 {valid_ids}"
                assert e.target in valid_ids, f"{graph.id}: edge target {e.target} 不在 {valid_ids}"

    def test_edge_info_model_fields(self):
        """EdgeInfo 有且仅有 source/target 两个 str 字段。"""
        e = EdgeInfo(source="a", target="b")
        assert e.source == "a"
        assert e.target == "b"
```

注意：`list_graphs` 是 `async def`，用 `asyncio.run` 调。它内部 `GRAPH_REGISTRY` 不依赖 DB，可直调。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_graph_debug.py::TestEdges -v`
Expected: FAIL — `ImportError: cannot import name 'EdgeInfo'`（后端还没定义）。

- [ ] **Step 3: 实现 EdgeInfo model + GraphInfo.edges 字段**

在 `src/sales_agent/api/routes/graph_debug.py` 的 `NodeInfo` class（行 44-53）之后、`PromptMapping`（行 55）之前，插入 `EdgeInfo`：

```python
class EdgeInfo(BaseModel):
    """图的一条边（取自 compiled.get_graph().edges）。"""
    source: str
    target: str
```

修改 `GraphInfo`（行 65-72），加 `edges` 字段：

```python
class GraphInfo(BaseModel):
    id: str
    name: str
    mermaid: str
    node_count: int
    edge_count: int
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []
    prompt_map: list[PromptMapping] = []
```

- [ ] **Step 4: list_graphs 填充 edges**

在 `list_graphs`（行 296-329）里，`node_infos, prompt_map = _build_node_infos(graph_id, g)`（行 314）之后，加 edges 构建。在 `except` 分支的 `node_infos, prompt_map = [], []`（行 319）旁也加 `edge_infos = []`。

try 分支（行 314 后加）：

```python
            node_infos, prompt_map = _build_node_infos(graph_id, g)
            edge_infos = [
                EdgeInfo(source=e.source, target=e.target) for e in g.edges
            ]
```

except 分支（行 319 改）：

```python
            node_infos, prompt_map, edge_infos = [], [], []
```

`GraphInfo` 构造（行 321-329）加 `edges=edge_infos`：

```python
        results.append(GraphInfo(
            id=graph_id,
            name=entry["name"],
            mermaid=mermaid,
            node_count=nodes,
            edge_count=edges,
            nodes=node_infos,
            edges=edge_infos,
            prompt_map=prompt_map,
        ))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/graph/test_graph_debug.py::TestEdges -v`
Expected: PASS（4 个测试全过）。

- [ ] **Step 6: 跑全量 graph_debug 测试确认无回归**

Run: `uv run pytest tests/unit/graph/test_graph_debug.py -q`
Expected: 33（原有）+ 4（新增）= 37 passed。

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/api/routes/graph_debug.py tests/unit/graph/test_graph_debug.py
git commit -m "feat(graph-debug): GraphInfo 加 edges 字段 (reactflow 重画前置)"
```

---

### Task 2: 前端 types.ts 加 EdgeInfo

**Files:**
- Modify: `console/src/api/types.ts:1-30`（加 `EdgeInfo` interface + `GraphInfo.edges`）

**Interfaces:**
- Consumes: 后端 Task 1 的 `EdgeInfo{source,target}` / `GraphInfo.edges`。
- Produces: 前端 `EdgeInfo` interface + `GraphInfo.edges: EdgeInfo[]`。Task 4 `GraphFlow` 消费。

- [ ] **Step 1: 加 EdgeInfo interface + GraphInfo.edges 字段**

在 `console/src/api/types.ts` 的 `NodeInfo` interface（行 4-11）之后加 `EdgeInfo`：

```typescript
/** 图的一条边（取自后端 compiled.get_graph().edges）。 */
export interface EdgeInfo {
  source: string;
  target: string;
}
```

修改 `GraphInfo`（行 22-30），加 `edges` 字段：

```typescript
export interface GraphInfo {
  id: string;
  name: string;
  mermaid: string;
  node_count: number;
  edge_count: number;
  nodes: NodeInfo[];
  edges: EdgeInfo[];
  prompt_map: PromptMapping[];
}
```

- [ ] **Step 2: 验证类型不报错**

Run: `cd /root/code/sales-agent/console && npx tsc -b --noEmit`
Expected: 无报错（exit 0）。注意：`console/package.json` 的 build 是 `tsc -b && vite build`，单独类型检查用 `npx tsc -b --noEmit` 或直接 `npm run build`。

- [ ] **Step 3: Commit**

```bash
git add console/src/api/types.ts
git commit -m "feat(graph-debug): 前端 types.ts 加 EdgeInfo"
```

---

### Task 3: GraphNode.tsx 自定义节点组件

**Files:**
- Create: `console/src/pages/Agents/GraphNode.tsx`

**Interfaces:**
- Consumes: `NodeInfo`（from `@/api/types`）。
- Produces: `GraphNode` reactflow 自定义节点组件 + `GraphNodeData` 类型 + `nodeTypes` 导出。Task 4 `GraphFlow` 消费。

**节点视觉规格（来自 spec 第 3 节）：**
- 三要素：类型徽标（药丸）+ 节点名（加粗+类型色）+ 中文说明（灰小字）。
- 配色：LLM 徽标 `#1677ff`/`#fff`、边框 `#1677ff` 1.5px、名色 `#0958d9`；子图 徽标 `#ff9800`/`#fff`、边框 `#ff9800` 1.8px、名色 `#e65100`；函数 徽标 `#8c8c8c`/`#fff`、边框 `#d9d9d9` 1.2px、名色 `#262626`。
- 底色 `#fff`，圆角 8px，padding `8px 12px`，阴影 `0 1px 3px rgba(0,0,0,0.06)`。
- 选中态：边框 2.5px + `boxShadow: 0 0 0 3px rgba(22,119,255,0.2)`。
- `dimmed`（上下游高亮时其余节点）：`opacity: 0.25`。
- `__start__`/`__end__`：小椭圆，`#fafafa` 底 `#d9d9d9` 边，灰字 `START`/`END`，无徽标无说明。
- 判定优先级：`calls_llm` > `type==='subgraph'` > 函数。

- [ ] **Step 1: 创建 GraphNode.tsx**

创建 `console/src/pages/Agents/GraphNode.tsx`：

```typescript
/** reactflow 自定义节点 — 图调试主图的节点视觉。
 *
 * 三要素：类型徽标（LLM蓝/子图橙/函数灰药丸）+ 节点名（加粗+类型色）+
 * 中文说明（灰小字）。选中态蓝色光晕高亮；dimmed 态降低透明度（上下游
 * 高亮时其余节点变暗）。__start__/__end__ 用小椭圆特殊渲染。
 *
 * 配色与 GraphDebugPage 的 NodeLegend 图例一致（单一事实源）。
 */

import { type CSSProperties } from 'react';
import type { NodeProps } from 'reactflow';

/** 节点类型标签 + 配色（与 NodeLegend 图例一致）。 */
type NodeKind = 'llm' | 'subgraph' | 'function' | 'terminal';

interface KindStyle {
  badgeText: string;
  badgeBg: string;
  border: string;
  borderWidth: number;
  nameColor: string;
}

const KIND_STYLE: Record<NodeKind, KindStyle> = {
  llm:      { badgeText: 'LLM',  badgeBg: '#1677ff', border: '#1677ff', borderWidth: 1.5, nameColor: '#0958d9' },
  subgraph: { badgeText: '子图', badgeBg: '#ff9800', border: '#ff9800', borderWidth: 1.8, nameColor: '#e65100' },
  function: { badgeText: '函数', badgeBg: '#8c8c8c', border: '#d9d9d9', borderWidth: 1.2, nameColor: '#262626' },
  terminal: { badgeText: '',     badgeBg: '',        border: '#d9d9d9', borderWidth: 1,   nameColor: '#8c8c8c' },
};

/** reactflow node.data 载荷。 */
export interface GraphNodeData {
  /** 节点名（= id）。__start__/__end__ 也走这个。 */
  name: string;
  /** 中文功能说明（terminal 节点为空）。 */
  desc: string;
  /** 节点类型（terminal 用于 __start__/__end__）。 */
  kind: NodeKind;
  /** 是否选中（点节点高亮）。 */
  isSelected: boolean;
  /** 是否变暗（上下游高亮时，非路径上的节点）。 */
  dimmed: boolean;
}

/** 从 name + calls_llm + type 推断 NodeKind（calls_llm 优先于 type）。 */
export function inferKind(name: string, calls_llm: boolean, type: 'function' | 'subgraph'): NodeKind {
  if (name === '__start__' || name === '__end__') return 'terminal';
  if (calls_llm) return 'llm';
  if (type === 'subgraph') return 'subgraph';
  return 'function';
}

const NODE_FONT = `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`;

/** 自定义 reactflow 节点。 */
export function GraphNode({ data }: NodeProps<GraphNodeData>) {
  const { name, desc, kind, isSelected, dimmed } = data;
  const ks = KIND_STYLE[kind];

  // __start__/__end__: 小椭圆，无徽标无说明。
  if (kind === 'terminal') {
    const terminalStyle: CSSProperties = {
      width: 56, height: 28, borderRadius: 14,
      border: `1px solid ${ks.border}`, background: '#fafafa',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: NODE_FONT, fontSize: 11, color: ks.nameColor,
      opacity: dimmed ? 0.25 : 1,
      boxShadow: isSelected ? '0 0 0 3px rgba(22,119,255,0.2)' : 'none',
    };
    return <div style={terminalStyle}>{name === '__start__' ? 'START' : 'END'}</div>;
  }

  const style: CSSProperties = {
    width: 180, minHeight: 48, borderRadius: 8,
    border: `${isSelected ? 2.5 : ks.borderWidth}px solid ${ks.border}`,
    background: '#fff', padding: '8px 12px',
    fontFamily: NODE_FONT, cursor: 'pointer', boxSizing: 'border-box',
    boxShadow: isSelected
      ? '0 0 0 3px rgba(22,119,255,0.2)'
      : '0 1px 3px rgba(0,0,0,0.06)',
    opacity: dimmed ? 0.25 : 1,
  };

  const badgeStyle: CSSProperties = {
    background: ks.badgeBg, color: '#fff', fontSize: 10, fontWeight: 600,
    padding: '1px 6px', borderRadius: 8, lineHeight: '16px',
  };

  return (
    <div style={style} className="gd-flow-node">
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
        <span style={badgeStyle}>{ks.badgeText}</span>
        <span style={{ fontWeight: 600, fontSize: 13, color: ks.nameColor }}>{name}</span>
      </div>
      {desc && <div style={{ fontSize: 11, color: '#595959' }}>{desc}</div>}
    </div>
  );
}

/** reactflow nodeTypes 映射（GraphFlow 用）。 */
export const graphNodeTypes = { graphNode: GraphNode };
```

- [ ] **Step 2: 验证类型不报错**

Run: `cd /root/code/sales-agent/console && npx tsc -b --noEmit`
Expected: 无报错。若 `NodeProps` 导入报错，确认 `reactflow` 已装（`package.json` 有 `reactflow: ^11.11.4`）。

- [ ] **Step 3: Commit**

```bash
git add console/src/pages/Agents/GraphNode.tsx
git commit -m "feat(graph-debug): GraphNode 自定义节点组件 (类型徽标+阴影)"
```

---

### Task 4: GraphFlow.tsx reactflow 主图容器

**Files:**
- Create: `console/src/pages/Agents/GraphFlow.tsx`

**Interfaces:**
- Consumes: `GraphInfo`/`NodeInfo`/`EdgeInfo`（from `@/api/types`）、`GraphNode`/`GraphNodeData`/`graphNodeTypes`/`inferKind`（from `./GraphNode`，Task 3）。
- Produces: `GraphFlow` 组件（props: `{ graph: GraphInfo; onSelect: (nodeId: string) => void }`）。Task 5 `GraphDebugPage` 消费。

**布局规格（spec 第 4 节）：**
- dagre：`rankdir:'LR', ranksep:80, nodesep:40, marginx:20, marginy:20`。
- 节点尺寸：普通节点 width 180 height 48（与 GraphNode 一致）；start/end 椭圆 width 56 height 28。
- reactflow：`fitView fitViewOptions={{padding:0.15}} nodesDraggable={false} nodesConnectable={false} elementsSelectable minZoom:0.2 maxZoom:2 proOptions={{hideAttribution:true}}`。
- `<Background color="#e0e0e0" gap={16} variant="dots" />` + `<Controls showInteractive={false} />`。
- 上下游高亮：选中节点后，BFS 正反向找祖先+后代 id 集合 → 这些节点 `dimmed=false`，其余 `dimmed=true`；选中节点到路径节点的边 `opacity:1`，其余 `opacity:0.15`。
- `__start__`/`__end__`：`GraphInfo.nodes` 不含，但 `edges` 引用 → 前端补两个虚拟节点（kind='terminal'）。

- [ ] **Step 1: 创建 GraphFlow.tsx**

创建 `console/src/pages/Agents/GraphFlow.tsx`：

```typescript
/** reactflow 主图容器 — 图调试「图结构」tab。
 *
 * 接收 GraphInfo（nodes + edges），补 __start__/__end__ 虚拟节点，
 * dagre LR 布局，渲染 reactflow。点节点高亮选中 + 上下游路径保留、
 * 其余变暗，并回调 onSelect(nodeId) 给父组件联动对照表。
 *
 * 复用 CheckpointDAG.tsx 的 dagre/reactflow 模板，方向改 LR。
 */

import { useMemo, useCallback, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  BackgroundVariant,
  type Node,
  type Edge,
  Position,
  ReactFlowProvider,
} from 'reactflow';
import dagre from 'dagre';
import 'reactflow/dist/style.css';
import type { GraphInfo } from '@/api/types';
import { GraphNode, graphNodeTypes, inferKind, type GraphNodeData } from './GraphNode';

const NODE_WIDTH = 180;
const NODE_HEIGHT = 48;
const TERMINAL_WIDTH = 56;
const TERMINAL_HEIGHT = 28;

interface GraphFlowProps {
  graph: GraphInfo;
  onSelect: (nodeId: string) => void;
}

/** dagre LR 布局：回填 node.position（中心点 → 左上角）。 */
function layoutGraph(nodes: Node<GraphNodeData>[], edges: Edge[]): Node<GraphNodeData>[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 80, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach(n => {
    const isTerminal = n.data.kind === 'terminal';
    g.setNode(n.id, {
      width: isTerminal ? TERMINAL_WIDTH : NODE_WIDTH,
      height: isTerminal ? TERMINAL_HEIGHT : NODE_HEIGHT,
    });
  });
  edges.forEach(e => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map(n => {
    const pos = g.node(n.id);
    const isTerminal = n.data.kind === 'terminal';
    const w = isTerminal ? TERMINAL_WIDTH : NODE_WIDTH;
    const h = isTerminal ? TERMINAL_HEIGHT : NODE_HEIGHT;
    return { ...n, position: { x: pos.x - w / 2, y: pos.y - h / 2 } };
  });
}

/** BFS 找 selectedId 的所有祖先 + 后代（含自身）。 */
function computeReachable(
  selectedId: string,
  nodes: Node<GraphNodeData>[],
  edges: Edge[],
): Set<string> {
  const ids = new Set(nodes.map(n => n.id));
  if (!ids.has(selectedId)) return new Set([selectedId]);

  const adj = new Map<string, string[]>();   // 正向（后代）
  const radj = new Map<string, string[]>();  // 反向（祖先）
  for (const n of nodes) { adj.set(n.id, []); radj.set(n.id, []); }
  for (const e of edges) {
    adj.get(e.source)?.push(e.target);
    radj.get(e.target)?.push(e.source);
  }

  const reachable = new Set<string>([selectedId]);
  const bfs = (start: string, graph: Map<string, string[]>) => {
    const queue = [start];
    const seen = new Set<string>([start]);
    while (queue.length) {
      const cur = queue.shift()!;
      for (const next of graph.get(cur) ?? []) {
        if (!seen.has(next)) {
          seen.add(next);
          reachable.add(next);
          queue.push(next);
        }
      }
    }
  };
  bfs(selectedId, adj);    // 后代
  bfs(selectedId, radj);   // 祖先
  return reachable;
}

function GraphFlowInner({ graph, onSelect }: GraphFlowProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // nodes: 补 __start__/__end__ 虚拟节点 + 转 reactflow Node。
  const allNodes: Node<GraphNodeData>[] = useMemo(() => {
    const real = graph.nodes.map(n => ({
      id: n.id,
      type: 'graphNode',
      position: { x: 0, y: 0 },
      data: {
        name: n.id,
        desc: n.desc,
        kind: inferKind(n.id, n.calls_llm, n.type),
        isSelected: false,
        dimmed: false,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }));
    // 补 __start__/__end__（若 edges 引用了它们）。
    const referenced = new Set<string>();
    graph.edges.forEach(e => { referenced.add(e.source); referenced.add(e.target); });
    const terminals: Node<GraphNodeData>[] = [];
    for (const tid of ['__start__', '__end__']) {
      if (referenced.has(tid) && !real.some(n => n.id === tid)) {
        terminals.push({
          id: tid,
          type: 'graphNode',
          position: { x: 0, y: 0 },
          data: { name: tid, desc: '', kind: 'terminal' as const, isSelected: false, dimmed: false },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
        });
      }
    }
    return [...real, ...terminals];
  }, [graph]);

  const allEdges: Edge[] = useMemo(() => (
    graph.edges.map((e, i) => ({
      id: `e${i}-${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      style: { stroke: '#bfbfbf', strokeWidth: 1.2 },
    }))
  ), [graph]);

  // 选中后计算可达集，标记 dimmed。
  const reachable = useMemo(
    () => (selectedId ? computeReachable(selectedId, allNodes, allEdges) : null),
    [selectedId, allNodes, allEdges],
  );

  const positionedNodes = useMemo(() => {
    const decorated = allNodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        isSelected: n.id === selectedId,
        dimmed: reachable ? !reachable.has(n.id) : false,
      },
    }));
    return layoutGraph(decorated, allEdges);
  }, [allNodes, allEdges, selectedId, reachable]);

  const decoratedEdges = useMemo(() => (
    allEdges.map(e => ({
      ...e,
      style: {
        ...e.style,
        opacity: reachable && !reachable.has(e.source) && !reachable.has(e.target) ? 0.15 : 1,
      },
    }))
  ), [allEdges, reachable]);

  const onNodeClick = useCallback(
    (_evt: React.MouseEvent, node: Node<GraphNodeData>) => {
      setSelectedId(prev => {
        // 再点同一节点 → 取消选中。
        if (prev === node.id) {
          onSelect('');
          return null;
        }
        onSelect(node.id);
        return node.id;
      });
    },
    [onSelect],
  );

  // 点空白处取消选中。
  const onPaneClick = useCallback(() => {
    setSelectedId(null);
    onSelect('');
  }, [onSelect]);

  return (
    <ReactFlow
      nodes={positionedNodes}
      edges={decoratedEdges}
      nodeTypes={graphNodeTypes}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      maxZoom={2}
    >
      <Background color="#e0e0e0" gap={16} variant={BackgroundVariant.Dots} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

export default function GraphFlow(props: GraphFlowProps) {
  return (
    <ReactFlowProvider>
      <GraphFlowInner {...props} />
    </ReactFlowProvider>
  );
}
```

- [ ] **Step 2: 验证类型不报错**

Run: `cd /root/code/sales-agent/console && npx tsc -b --noEmit`
Expected: 无报错。常见报错：`BackgroundVariant` 导入名（reactflow 11 用 `BackgroundVariant.Dots`；若报错改用字符串 `'dots'`）。

- [ ] **Step 3: Commit**

```bash
git add console/src/pages/Agents/GraphFlow.tsx
git commit -m "feat(graph-debug): GraphFlow reactflow 主图容器 (dagre LR + 高亮联动)"
```

---

### Task 5: GraphDebugPage 集成 GraphFlow + mermaid 退场

**Files:**
- Modify: `console/src/pages/Agents/GraphDebugPage.tsx`（删 mermaid import/state/useEffect，换 `<GraphFlow>`，加 onSelect 联动对照表）
- Modify: `console/src/pages/Agents/GraphDebugPage.css`（删 `.gd-mermaid*`，加 `.gd-flow-wrap`）

**Interfaces:**
- Consumes: `GraphFlow`（Task 4）、`GraphInfo`（含 `edges`，Task 2）。

- [ ] **Step 1: GraphDebugPage.tsx 删 mermaid + 加 GraphFlow**

在 `console/src/pages/Agents/GraphDebugPage.tsx`：

1. 删 mermaid import（行 23 `import mermaid from 'mermaid';`）。
2. 删 `mermaid.initialize(...)`（行 51）。
3. 删 `mermaidSvg` state（行 181 `const [mermaidSvg, setMermaidSvg] = useState<Record<string, string>>({});`）。
4. 删 mermaid 渲染 useEffect（行 213-230 整个 `useEffect(() => { ... mermaid.render ... })`）。
5. 加 GraphFlow import，在 `import JsonNode from './JsonNode';`（行 44）后加：
   ```typescript
   import GraphFlow from './GraphFlow';
   ```
6. 加 onSelect 联动 state + handler。在组件内（`traceCollapsed` state 附近）加：
   ```typescript
   // 点 GraphFlow 节点 → 展开对照表 + 滚动到对应行高亮。
   const promptTableRef = useRef<HTMLDivElement>(null);
   const [highlightedNode, setHighlightedNode] = useState<string | null>(null);
   const handleSelectNode = useCallback((nodeId: string) => {
     if (!nodeId) { setHighlightedNode(null); return; }
     setHighlightedNode(nodeId);
     // 2 秒后清除高亮（保留滚动位置）。
     window.setTimeout(() => setHighlightedNode(null), 2000);
   }, []);
   ```
   注意顶部需 `useRef` 已在 import（行 10 `import { useState, useRef, useEffect, useCallback } from 'react';` 已有，无需加）。

- [ ] **Step 2: 替换 mermaid 渲染区为 GraphFlow**

在 render 的 Tabs children 里（约行 556-568），把：
```tsx
                    <div className="gd-mermaid-wrap">
                      {mermaidSvg[g.id] ? (
                        <div
                          className="gd-mermaid"
                          dangerouslySetInnerHTML={{ __html: mermaidSvg[g.id] }}
                        />
                      ) : (
                        <Spin tip="渲染图中..." />
                      )}
                    </div>
```
替换为：
```tsx
                    <div className="gd-flow-wrap">
                      <GraphFlow graph={g} onSelect={handleSelectNode} />
                    </div>
```

- [ ] **Step 3: 对照表加 ref + 行高亮**

在「节点↔Prompt 对照」Collapse（约行 569-578），把 `items={[{ key: 'prompt-map', label: ..., children: <NodePromptTable rows={g.prompt_map ?? []} /> }]}` 改为带 ref + 高亮 props：

先给 Collapse 加 `ref`（用 `defaultActiveKey` 控制，选中时展开）。修改为：
```tsx
                    <Collapse
                      size="small"
                      ref={promptTableRef}
                      defaultActiveKey={highlightedNode ? ['prompt-map'] : []}
                      style={{ marginTop: 8 }}
                      items={[{
                        key: 'prompt-map',
                        label: `节点 ↔ Prompt 对照（${g.prompt_map?.length ?? 0} 行）`,
                        children: (
                          <NodePromptTable
                            rows={g.prompt_map ?? []}
                            highlightedNode={highlightedNode}
                          />
                        ),
                      }]}
                    />
```

修改 `NodePromptTable` 函数（约行 81-131）签名加 `highlightedNode` prop + 行高亮 + scrollIntoView：

```typescript
function NodePromptTable({ rows, highlightedNode }: { rows: PromptMapping[]; highlightedNode: string | null }) {
  const columns: TableColumnsType<PromptMapping> = [
    {
      title: '节点',
      dataIndex: 'node',
      key: 'node',
      width: 160,
      render: (node: string, row) => (
        <span>
          {row.calls_llm ? (
            <Tag color="blue" className="llm-tag">LLM</Tag>
          ) : null}
          {node}
        </span>
      ),
    },
    {
      title: '对应 Prompt',
      dataIndex: 'prompt_name',
      key: 'prompt_name',
      render: (name: string, row) =>
        name === '—' ? (
          <span style={{ color: '#8c8c8c' }}>—（纯函数，无 prompt）</span>
        ) : (
          <span>
            <code>{name}</code>
            {row.note ? <span style={{ color: '#8c8c8c', marginLeft: 6 }}>({row.note})</span> : null}
          </span>
        ),
    },
    {
      title: '来源',
      dataIndex: 'prompt_source',
      key: 'prompt_source',
      width: 260,
      render: (src: string) =>
        src ? <code style={{ fontSize: 11, color: '#8c8c8c' }}>{src}</code> : null,
    },
  ];
  return (
    <div className="gd-node-prompt-table">
      <Table
        size="small"
        pagination={false}
        columns={columns}
        dataSource={rows}
        rowKey={(r, i) => `${r.node}-${r.prompt_name}-${i}`}
        rowClassName={(row) => row.node === highlightedNode ? 'gd-prompt-row-highlight' : ''}
      />
    </div>
  );
}
```

注意：`scrollIntoView` 由 CSS `animation` + 浏览器自然行为处理——高亮行通过 `gd-prompt-row-highlight` class 闪烁，Collapse 因 `defaultActiveKey={highlightedNode?['prompt-map']:[]}` 自动展开。无需手动 scrollIntoView（antd Table 无简单 ref 到具体行）。

- [ ] **Step 4: GraphDebugPage.css 删 mermaid 样式 + 加 flow 样式**

在 `console/src/pages/Agents/GraphDebugPage.css`：

删 `.gd-mermaid-wrap`（约行 65-73）、`.gd-mermaid`（约行 75-81）、`.gd-mermaid svg`（约行 86-90）三条规则。

在原 `.gd-mermaid-wrap` 位置加：
```css
.gd-flow-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  overflow: hidden;
  background: #f7f9fc;
}

/* reactflow 自定义节点不需要额外 CSS（样式全在 GraphNode.tsx 内联），
   此 class 仅用于 devtools 定位。 */
.gd-flow-node {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* 对照表高亮行：黄色背景 + 2 秒淡出。 */
.gd-prompt-row-highlight {
  background: #fffbe6;
  animation: gd-row-fade 2s ease-out forwards;
}

@keyframes gd-row-fade {
  from { background: #fff7b3; }
  to { background: transparent; }
}
```

- [ ] **Step 5: 验证 build 通过**

Run: `cd /root/code/sales-agent/console && npm run build`
Expected: exit 0，vite built。若 tsc 报 `mermaid` 未使用残留引用，grep 确认 `GraphDebugPage.tsx` 无 `mermaid` 字样：`grep -n mermaid console/src/pages/Agents/GraphDebugPage.tsx`（应空）。

- [ ] **Step 6: 手动验证（启动 dev server）**

Run: `cd /root/code/sales-agent/console && npm run dev`（后台跑），浏览器打开图调试页（需后端 `/graphs` 返回数据）。
验证清单：
1. 三个图 tab 都渲染 reactflow 图，dagre LR 布局，fitView 自适应。
2. 节点类型徽标配色正确（LLM 蓝/子图橙/函数灰），中文说明显示。
3. 点节点 → 高亮 + 上下游路径变暗其余变淡 + 对照表展开滚到对应行高亮。
4. 缩放/拖拽画布正常，Controls 按钮工作。
5. 侧边栏+Header 保留，执行轨迹可折叠。

（若 dev server 无后端数据，跳过手动验证，靠部署后验证。）

- [ ] **Step 7: Commit**

```bash
git add console/src/pages/Agents/GraphDebugPage.tsx console/src/pages/Agents/GraphDebugPage.css
git commit -m "feat(graph-debug): 集成 GraphFlow 替换 mermaid + 点节点联动对照表"
```

- [ ] **Step 8: 更新 README + changelog + push 部署**

更新 `README.md`「产品文档对照」节加新条目：
```
| [2026-07-06](changelog/2026-07-06.md) | **图调试页 reactflow 重画**: mermaid 静态 svg 换 reactflow 交互图（类型徽标节点+dagre LR 布局+点节点高亮联动对照表）。后端 GraphInfo 加 edges 字段（单一事实源）。解决 mermaid 默认皮肤廉价/被拉伸/不能点。前端 build exit 0 |
```

在 `changelog/2026-07-06.md` 末尾追加新章节（改动对象/类型/影响范围/改动明细/原因/验证 五段式，参照已有格式）。

Commit + push：
```bash
git add README.md changelog/2026-07-06.md
git commit -m "docs: 图调试 reactflow 重画 changelog + README"
git push origin main
```

- [ ] **Step 9: 监控 CI + 三台部署验证**

push 后查主控 Gitea action_run（lesson #18：本机=prod2，主控=47.120.55.219）：
```bash
ssh root@172.25.186.210 "docker exec gitea sqlite3 /data/gitea/gitea.db \"SELECT id,status,substr(commit_sha,1,7) FROM action_run ORDER BY id DESC LIMIT 1;\""
```
等 status=2（success，约 5 分钟）。

验证三台容器 tag + stream 健康（lesson #4 生产入口）：
```bash
# 本机 prod2
docker ps --format '{{.Names}}\t{{.Image}}' | grep sales-agent | head
docker logs --tail 3 sales-agent-taishan-stream
# 主控 prod3
ssh root@172.25.186.210 "docker ps --format '{{.Names}}\t{{.Image}}' | grep sales-agent | grep stream; docker logs --tail 3 \$(docker ps --format '{{.Names}}' | grep sales-agent | grep stream | head -1)"
# test
ssh root@47.118.16.235 "docker ps --format '{{.Names}}\t{{.Image}}' | grep sales-agent | grep stream"
```
Expected: 三台 stream 容器 tag = 新 commit，Up + 钉钉连接 + 无 crash。

恢复本机 ci-fanout stash（lesson #32）：
```bash
git stash list | head -1   # 确认是 WIP on main: <新sha>
git stash pop stash@{0}
```
