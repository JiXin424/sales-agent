/** Lightweight recursive collapsible JSON viewer / editor.

No external deps — used by GraphDebugPage to render checkpoint state `values`.
Mirrors the minimal UX of react-json-view: objects/arrays are collapsible
triangles with key labels; primitives are inline.

When `editable` is true (default false — read-only behavior preserved), leaf
primitives (string / number / boolean) render as inputs; the user edits them
and every change is bubbled up via `onChange` as the updated subtree value.
The parent component (GraphDebugPage) owns the root `values` object and the
Submit / Cancel buttons.
*/

import { useState, type ReactNode } from 'react';

interface JsonNodeProps {
  /** Value to render (any JSON-compatible). */
  value: unknown;
  /** Key label (omitted for root). */
  label?: string;
  /** Whether this node should default to collapsed. */
  defaultCollapsed?: boolean;
  /** Enable editing of leaf primitives. Default false (read-only). */
  editable?: boolean;
  /** Called with the updated subtree value whenever a descendant leaf changes.
   *  Only invoked when `editable` is true. */
  onChange?: (newValue: unknown) => void;
}

const TYPE_COLOR: Record<string, string> = {
  string: '#52c41a',
  number: '#1677ff',
  boolean: '#722ed1',
  null: '#8c8c8c',
};

function typeOf(v: unknown): 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null' {
  if (v === null || v === undefined) return 'null';
  if (Array.isArray(v)) return 'array';
  return typeof v as 'object' | 'string' | 'number' | 'boolean';
}

function primitiveLabel(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'string') return JSON.stringify(v);
  return String(v);
}

/** Coerce a raw string input back into the original primitive type.
 *  Numbers parse to number (NaN falls back to original); booleans parse
 *  strictly; everything else stays a string. null stays null. */
function coercePrimitive(raw: string, prev: unknown): unknown {
  if (prev === null || prev === undefined) return raw;
  if (typeof prev === 'number') {
    if (raw.trim() === '') return 0;
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  if (typeof prev === 'boolean') {
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    return Boolean(raw);
  }
  return raw;
}

function JsonNode({
  value,
  label,
  defaultCollapsed = false,
  editable = false,
  onChange,
}: JsonNodeProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const t = typeOf(value);

  // Objects & arrays — collapsible.
  if (t === 'object' || t === 'array') {
    const isArr = t === 'array';
    const arr = isArr ? (value as unknown[]) : null;
    const entries = arr
      ? arr.map((v, i) => [String(i), v] as [string, unknown])
      : Object.entries(value as Record<string, unknown>);
    const count = isArr ? (value as unknown[]).length : entries.length;

    const arrow = (
      <span
        className="json-toggle"
        onClick={e => {
          e.stopPropagation();
          setCollapsed(c => !c);
        }}
      >
        {collapsed ? '▶' : '▼'}
      </span>
    );

    const keyLabel = label != null ? (
      <span className="json-key">{label}:</span>
    ) : null;

    const summary = (
      <span className="json-summary">
        {isArr ? `Array(${count})` : `{${count}}`}
      </span>
    );

    if (count === 0) {
      return (
        <div className="json-node">
          <span className="json-key">{label != null ? `${label}: ` : ''}</span>
          <span className="json-empty">{isArr ? '[]' : '{}'}</span>
        </div>
      );
    }

    /** Replace a single child value (by key / index) and bubble up via onChange. */
    const updateChild = (key: string, newChild: unknown) => {
      if (!onChange) return;
      if (isArr) {
        const idx = Number(key);
        const next = arr!.slice();
        next[idx] = newChild;
        onChange(next);
      } else {
        onChange({ ...(value as Record<string, unknown>), [key]: newChild });
      }
    };

    return (
      <div className="json-node">
        <div className="json-row">
          {arrow}
          {keyLabel} {collapsed ? summary : isArr ? '[' : '{'}
        </div>
        {!collapsed && (
          <div className="json-children">
            {entries.map(([k, v]) => (
              <JsonNode
                key={k}
                value={v}
                label={k}
                editable={editable}
                onChange={editable ? (newChild: unknown) => updateChild(k, newChild) : undefined}
              />
            ))}
          </div>
        )}
        {!collapsed && (
          <div className="json-row">{isArr ? ']' : '}'}</div>
        )}
      </div>
    );
  }

  // Primitive — editable input when editable && onChange provided (and not null).
  if (editable && onChange && t !== 'null') {
    return (
      <div className="json-node">
        <div className="json-row">
          {label != null && <span className="json-key">{label}: </span>}
          <input
            className="json-edit-input"
            value={primitiveLabel(value)}
            onChange={e => onChange(coercePrimitive(e.target.value, value))}
            style={{
              color: TYPE_COLOR[t],
              border: '1px solid #d9d9d9',
              borderRadius: 4,
              padding: '0 4px',
              fontSize: 'inherit',
              fontFamily: 'inherit',
              minWidth: 80,
            }}
          />
        </div>
      </div>
    );
  }

  // Primitive (read-only).
  return (
    <div className="json-node">
      <div className="json-row">
        {label != null && <span className="json-key">{label}: </span>}
        <span style={{ color: TYPE_COLOR[t] }}>{primitiveLabel(value)}</span>
      </div>
    </div>
  );
}

export default JsonNode;
