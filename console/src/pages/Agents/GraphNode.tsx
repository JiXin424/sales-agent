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
