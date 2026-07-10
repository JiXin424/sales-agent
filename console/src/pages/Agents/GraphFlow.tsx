/** reactflow 主图容器 — 图调试「图结构」tab。
 *
 * 接收 GraphInfo（nodes + edges），补 __start__/__end__ 虚拟节点，
 * dagre LR 布局，渲染 reactflow。点节点高亮选中 + 上下游路径保留、
 * 其余变暗，并回调 onSelect(nodeId) 给父组件联动对照表。
 *
 * 复用 CheckpointDAG.tsx 的 dagre/reactflow 模板，方向改 LR。
 */

import { useMemo, useCallback, useState, useEffect, useRef } from 'react';
import ReactFlow, {
  Background,
  Controls,
  BackgroundVariant,
  useReactFlow,
  type Node,
  type Edge,
  Position,
  ReactFlowProvider,
} from 'reactflow';
import dagre from 'dagre';
import 'reactflow/dist/style.css';
import type { GraphInfo } from '@/api/types';
import { graphNodeTypes, inferKind, type GraphNodeData } from './GraphNode';

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
  const rf = useReactFlow();
  const wrapRef = useRef<HTMLDivElement>(null);

  // 画布尺寸变化时（折叠/展开执行轨迹、窗口 resize）重新 fitView，
  // 让图始终适配画布大小。fitView prop 只在初始跑一次，不跟容器变化。
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let t: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(() => rf.fitView({ padding: 0.15 }), 80);
    });
    ro.observe(el);
    return () => { clearTimeout(t); ro.disconnect(); };
  }, [rf]);

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
    <div ref={wrapRef} style={{ width: '100%', height: '100%' }}>
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
    </div>
  );
}

export default function GraphFlow(props: GraphFlowProps) {
  return (
    <ReactFlowProvider>
      <GraphFlowInner {...props} />
    </ReactFlowProvider>
  );
}
