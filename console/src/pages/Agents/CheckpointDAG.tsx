/** Checkpoint branch-tree DAG (A3) — reactflow visualization.

Replaces the A1 linear `Steps` timeline. Each checkpoint becomes a node;
each `parent_checkpoint_id → checkpoint_id` pair becomes an edge. dagre lays
them out top-to-bottom so a fork-free run degenerates into a vertical chain
(no visual regression), and A2 forks fan out as side branches.

Clicking a node calls `onSelect(meta)`. The parent component owns the state
viewer (reuses A1's `getCheckpointState` + `JsonNode`).

Fork detection: a checkpoint is flagged as a fork point if either
  - the backend `source === 'update'` hint is set (preferred, when present), or
  - it has more than one child (structural — multiple child checkpoints share
    this checkpoint as their parent, which only happens when an A2 fork
    branches off it).
The structural fallback keeps fork markers correct even if the backend omits
`source`, since the DAG shape itself reveals the divergence.
*/

import { useMemo, useCallback, type CSSProperties } from 'react';
import ReactFlow, {
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Position,
  ReactFlowProvider,
} from 'reactflow';
import dagre from 'dagre';
import 'reactflow/dist/style.css';
import { Tag } from 'antd';
import type { CheckpointMeta } from '@/api/types';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 72;

/** Data carried by each reactflow node — the original meta plus derived flags. */
interface CheckpointNodeData {
  meta: CheckpointMeta;
  isSelected: boolean;
  isForkPoint: boolean;
  isForkChild: boolean;
}

/** dagre layout pass: assigns x/y to every node, returns positioned nodes+edges. */
function layoutGraph(nodes: Node[], edges: Edge[], direction: 'TB' | 'LR' = 'TB') {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: direction, nodesep: 40, ranksep: 60, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach(n => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  edges.forEach(e => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map(n => {
    const pos = g.node(n.id);
    return { ...n, position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 } };
  });
}

/** Format an ISO timestamp into a short HH:MM:SS string (best-effort; falls
 *  back to the raw string on parse failure so we never hide data). */
function shortTs(ts: string | null): string {
  if (!ts) return '-';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  } catch {
    return ts;
  }
}

/** Custom reactflow node — renders step / node / ts + fork badges. */
function CheckpointNode({ data }: NodeProps<CheckpointNodeData>) {
  const { meta, isSelected, isForkPoint, isForkChild } = data;

  const style: CSSProperties = {
    width: NODE_WIDTH,
    minHeight: NODE_HEIGHT,
    border: `2px solid ${isSelected ? '#1677ff' : isForkPoint ? '#fa8c16' : '#d9d9d9'}`,
    borderRadius: 8,
    background: isSelected ? '#e6f4ff' : isForkChild ? '#fff7e6' : '#fff',
    padding: '6px 10px',
    fontSize: 11,
    cursor: 'pointer',
    boxShadow: isSelected ? '0 0 0 2px rgba(22,119,255,0.2)' : 'none',
    boxSizing: 'border-box',
  };

  return (
    <div style={style} className="gd-dag-node">
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        <Tag color="blue" style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
          step {meta.step != null ? meta.step : '-'}
        </Tag>
        {isForkPoint && (
          <Tag color="orange" style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
            fork
          </Tag>
        )}
        {isForkChild && !isForkPoint && (
          <Tag color="gold" style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
            branch
          </Tag>
        )}
      </div>
      <div style={{ fontWeight: 600, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {meta.node ?? '-'}
      </div>
      <div style={{ color: '#8c8c8c', fontSize: 10 }}>{shortTs(meta.ts)}</div>
    </div>
  );
}

const nodeTypes = { checkpoint: CheckpointNode };

interface CheckpointDAGProps {
  checkpoints: CheckpointMeta[];
  selectedCheckpointId: string | null;
  onSelect: (meta: CheckpointMeta) => void;
}

function CheckpointDAGInner({ checkpoints, selectedCheckpointId, onSelect }: CheckpointDAGProps) {
  // Build child counts so we can flag fork points structurally (a checkpoint
  // with >1 child is the divergence point of an A2 fork).
  const childCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const cp of checkpoints) {
      if (cp.parent_checkpoint_id) {
        counts.set(
          cp.parent_checkpoint_id,
          (counts.get(cp.parent_checkpoint_id) ?? 0) + 1,
        );
      }
    }
    return counts;
  }, [checkpoints]);

  // Map checkpoints → reactflow nodes + edges, then run dagre layout.
  const { nodes, edges } = useMemo(() => {
    const rawNodes: Node<CheckpointNodeData>[] = checkpoints.map(cp => {
      const childCount = childCounts.get(cp.checkpoint_id) ?? 0;
      const sourceIsUpdate = cp.source === 'update';
      // Fork point: backend `source=update` hint, or structurally >1 child.
      const isForkPoint = sourceIsUpdate || childCount > 1;
      const isForkChild =
        cp.parent_checkpoint_id != null &&
        (childCounts.get(cp.parent_checkpoint_id) ?? 0) > 1;
      return {
        id: cp.checkpoint_id,
        type: 'checkpoint',
        position: { x: 0, y: 0 }, // dagre assigns the real position
        data: {
          meta: cp,
          isSelected: cp.checkpoint_id === selectedCheckpointId,
          isForkPoint,
          isForkChild,
        },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
      };
    });

    const rawEdges: Edge[] = checkpoints
      .filter(cp => cp.parent_checkpoint_id)
      .map(cp => ({
        id: `${cp.parent_checkpoint_id}->${cp.checkpoint_id}`,
        source: cp.parent_checkpoint_id!,
        target: cp.checkpoint_id,
        // Fork edges get a warmer color so the branch is visually distinct.
        animated: (childCounts.get(cp.parent_checkpoint_id!) ?? 0) > 1,
        style: {
          stroke: (childCounts.get(cp.parent_checkpoint_id!) ?? 0) > 1 ? '#fa8c16' : '#bfbfbf',
          strokeWidth: 1.5,
        },
      }));

    const positioned = layoutGraph(rawNodes, rawEdges, 'TB');
    return { nodes: positioned, edges: rawEdges };
  }, [checkpoints, selectedCheckpointId, childCounts]);

  const onNodeClick = useCallback(
    (_evt: React.MouseEvent, node: Node<CheckpointNodeData>) => {
      onSelect(node.data.meta);
    },
    [onSelect],
  );

  return (
    <div className="gd-dag-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
        maxZoom={2}
      >
        <Background color="#e8e8e8" gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** Wrap in ReactFlowProvider so hooks have access to the internal store
 *  even if the parent later wraps this in another flow context. */
export default function CheckpointDAG(props: CheckpointDAGProps) {
  return (
    <ReactFlowProvider>
      <CheckpointDAGInner {...props} />
    </ReactFlowProvider>
  );
}
