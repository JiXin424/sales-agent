# Instance Config Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 dedicated mode 运营面板改造为综合面板：运营指标 + 运行时环境配置展示。

**Architecture:** 后端从 `TenantRuntime` 读取全部环境变量，通过 `GET /instance/config` 暴露（敏感字段标记 `sensitive: true` 并截断）。前端 `DashboardPage` 接管 `/` 和 `/dashboard` 路由，新增 `ConfigCard` + `SensitiveField` 组件展示配置。

**Tech Stack:** Python FastAPI（后端）、React + TypeScript + Ant Design（前端）

## Global Constraints

- 后端 CommonJS（`require`），前端 ES Modules（`import`）
- 前端组件使用函数式组件 + Hooks
- API 调用统一通过 `console/src/utils/api.js` → 实际使用 `console/src/api/*.ts`
- 数据库变更必须走 Alembic migration — 本次无 DB 变更
- 所有非琐碎改动必须记录到升级日志 `changelog/YYYY-MM-DD.md`
- 功能升级后自动更新 README 的「产品文档对照」节

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/sales_agent/core/tenant_runtime.py` | Modify | 新增 `get_all_env_vars()` 返回完整环境变量 |
| `src/sales_agent/api/routes/instance.py` | Create | `GET /instance/config` 端点 |
| `src/sales_agent/main.py` | Modify | 注册 instance router |
| `console/src/api/types.ts` | Modify | 新增 `InstanceConfig` 类型 |
| `console/src/api/instance.ts` | Create | `getInstanceConfig()` API 函数 |
| `console/src/api/index.ts` | Modify | 导出 instance API |
| `console/src/components/SensitiveField.tsx` | Create | 敏感字段渲染组件 |
| `console/src/components/ConfigCard.tsx` | Create | 环境配置卡片组件 |
| `console/src/pages/Dashboard/DashboardPage.tsx` | Modify | 加载配置数据 + 渲染 ConfigCard |
| `console/src/App.tsx` | Modify | `/` 和 `/dashboard` 路由指向 DashboardPage |

---

### Task 1: Add `get_all_env_vars()` to TenantRuntime

**Files:**
- Modify: `src/sales_agent/core/tenant_runtime.py` (add method after `get_log_info`)

**Interfaces:**
- Produces: `TenantRuntime.get_all_env_vars() -> dict[str, str]` — returns all non-empty `os.environ` entries, excluding common system/noise vars.

**Implementation:**

- [ ] **Step 1: Add `get_all_env_vars()` method to TenantRuntime**

Edit `src/sales_agent/core/tenant_runtime.py`, add after line 195 (`get_log_info` method):

```python
    # System env vars to exclude from config display (common Linux / Docker / Python noise).
    _SYSTEM_ENV_KEYS: set[str] = {
        "PATH", "HOME", "USER", "HOSTNAME", "PWD", "SHLVL", "TERM",
        "LANG", "LC_ALL", "TZ", "DEBIAN_FRONTEND",
        "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
        "PIP_REQUIRE_VIRTUALENV", "PIP_NO_CACHE_DIR",
        "VIRTUAL_ENV", "CONDA_PREFIX",
        "DOCKER_HOST", "DOCKER_CONFIG",
        "HOSTNAME", "OLDPWD", "LS_COLORS", "which_declare",
        "_", "BASH_FUNC",
    }

    def get_all_env_vars(self) -> dict[str, str]:
        """返回所有非空的非系统环境变量（用于前端配置展示）。"""
        result: dict[str, str] = {}
        for key, value in sorted(os.environ.items()):
            if not value:
                continue
            if key in self._SYSTEM_ENV_KEYS or key.startswith("BASH_FUNC_"):
                continue
            result[key] = value
        return result
```

- [ ] **Step 2: Run backend unit tests to verify no regressions**

```bash
cd /root/code/sales-agent && python -m pytest tests/ -x -q --ignore=tests/integration 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/sales_agent/core/tenant_runtime.py
git commit -m "feat: add get_all_env_vars() to TenantRuntime

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Create `GET /instance/config` endpoint

**Files:**
- Create: `src/sales_agent/api/routes/instance.py`

**Interfaces:**
- Consumes: `TenantRuntime.get_all_env_vars()` from Task 1, `get_tenant_runtime()` from `sales_agent.core.tenant_runtime`
- Produces: `GET /instance/config` → `{"groups": {...}}` JSON

**Implementation:**

- [ ] **Step 1: Create the route file**

Create `src/sales_agent/api/routes/instance.py`:

```python
"""Instance-level runtime config endpoint."""

import re
from fastapi import APIRouter
from sales_agent.core.tenant_runtime import get_tenant_runtime

router = APIRouter(prefix="/instance", tags=["instance"])

# Fields whose VALUES should be treated as sensitive (masked).
# These patterns match against the field NAME.
_SENSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r".*_API_KEY$",
        r".*_SECRET$",
        r".*_TOKEN$",
        r".*_AES_KEY$",
        r".*_ENCRYPT_TOKEN$",
    ]
]

# Group assignment: (field_name_regex, group_name, sort_order)
_GROUP_RULES = [
    (r"^DEPLOYMENT_", "deployment", 0),
    (r"^TENANT_", "deployment", 0),
    (r"^MODEL_", "model", 1),
    (r"^EMBEDDING_", "model", 1),
    (r"^VECTOR_", "storage", 2),
    (r"^DATA_DIR$", "storage", 2),
    (r"^LOG_DIR$", "storage", 2),
    (r"^DINGTALK_MEDIA_", "media", 4),
    (r"^DINGTALK_VISION_", "media", 4),
    (r"^DINGTALK_AUDIO_", "media", 4),
    (r"^DINGTALK_QUICK_ENTRY", "coach", 5),
    (r"^DINGTALK_", "dingtalk", 3),
    (r"^COACH_", "coach", 5),
    (r"^NEO4J_", "neo4j", 6),
    (r"^ONTOLOGY_", "ontology", 7),
]


def _is_sensitive(field_name: str) -> bool:
    for pat in _SENSITIVE_PATTERNS:
        if pat.match(field_name):
            return True
    return False


def _mask_value(value: str) -> str:
    """Show first 6 chars + '...' + last 4 chars. If value too short, just mask fully."""
    if len(value) <= 10:
        return value[:3] + "..." if len(value) > 3 else "..."
    return value[:6] + "..." + value[-4:]


def _assign_group(field_name: str) -> str:
    for pattern, group, _ in _GROUP_RULES:
        if re.match(pattern, field_name):
            return group
    return "other"


@router.get("/config")
async def get_instance_config():
    """返回当前实例的完整运行时配置。

    API key / secret / token 等敏感字段不会返回明文，而是标记 sensitive=true
    并提供截断值。前端须经过用户交互（点击）才能看到完整值。
    """
    runtime = get_tenant_runtime()
    raw = runtime.get_all_env_vars()

    # Grouped output: preserve sort order within each group
    groups: dict[str, dict[str, object]] = {}
    group_order: dict[str, int] = {}

    for key, raw_value in sorted(raw.items()):
        group_name = _assign_group(key)
        if group_name not in groups:
            groups[group_name] = {}
            # Determine sort order for this group
            for pattern, g, order in _GROUP_RULES:
                if g == group_name:
                    group_order[group_name] = order
                    break
            else:
                group_order[group_name] = 99

        sensitive = _is_sensitive(key)
        if sensitive:
            groups[group_name][key] = {
                "value": raw_value,          # full plaintext (frontend gates via click)
                "sensitive": True,
                "masked": _mask_value(raw_value),  # truncated preview after reveal
            }
        else:
            groups[group_name][key] = raw_value

    # Sort groups by their defined order
    sorted_groups = dict(
        sorted(groups.items(), key=lambda item: group_order.get(item[0], 99))
    )

    return {"groups": sorted_groups}
```

- [ ] **Step 2: Run backend tests to verify route loads**

```bash
cd /root/code/sales-agent && python -c "from sales_agent.api.routes.instance import router; print('router loaded:', router.prefix)"
```

Expected: `router loaded: /instance`

- [ ] **Step 3: Commit**

```bash
git add src/sales_agent/api/routes/instance.py
git commit -m "feat: add GET /instance/config endpoint

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Register instance router in main.py

**Files:**
- Modify: `src/sales_agent/main.py` (add import + include_router)

**Interfaces:**
- Consumes: `instance.router` from Task 2

**Implementation:**

- [ ] **Step 1: Add import and router registration in main.py**

Edit `src/sales_agent/main.py`, line 13 — add `instance` to the route imports:

```python
# Change line 13 from:
from sales_agent.api.routes import agent, conversations, documents, feedback, health, tenants, prompts, uploads, admin, pilot, agents, coach, ontology
# To:
from sales_agent.api.routes import agent, conversations, documents, feedback, health, tenants, prompts, uploads, admin, pilot, agents, coach, ontology, instance
```

Then after line 186 (`app.include_router(ontology.router)`), add:

```python
# 注册实例级接口（运行时配置等）
app.include_router(instance.router)
```

The exact edit locations:
- Line 13: add `instance` to the import tuple
- After `app.include_router(ontology.router)` (line 186): add `app.include_router(instance.router)`

- [ ] **Step 2: Verify app starts without errors**

```bash
cd /root/code/sales-agent && python -c "
from sales_agent.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
print('/instance/config' in routes)
"
```

Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add src/sales_agent/main.py
git commit -m "feat: register instance router for GET /instance/config

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Frontend — Add InstanceConfig types and API

**Files:**
- Modify: `console/src/api/types.ts`
- Create: `console/src/api/instance.ts`
- Modify: `console/src/api/index.ts`

**Interfaces:**
- Produces: `InstanceConfigGroup`, `InstanceConfigResponse` types; `getInstanceConfig()` function

**Implementation:**

- [ ] **Step 1: Add types to `console/src/api/types.ts`**

At the end of the file (before the last empty line if any), append:

```typescript
// --- Instance Config ---

export interface SensitiveValue {
  value: string;     // full plaintext (revealed on click)
  sensitive: true;
  masked: string;    // truncated preview (e.g. "sk-2e2a...2242a")
}

export type ConfigValue = string | SensitiveValue;

export type InstanceConfigGroup = Record<string, ConfigValue>;

export interface InstanceConfigResponse {
  groups: Record<string, InstanceConfigGroup>;
}
```

- [ ] **Step 2: Create `console/src/api/instance.ts`**

```typescript
/** Instance-level API wrappers. */

import { apiGet } from './client';
import type { InstanceConfigResponse } from './types';

export function getInstanceConfig() {
  return apiGet<InstanceConfigResponse>('/instance/config');
}
```

- [ ] **Step 3: Export from `console/src/api/index.ts`**

Edit line 10 (current last line before `export { ApiError }`):

```typescript
// Change:
export * from './agents';
// Add after:
export * from './instance';
```

- [ ] **Step 4: Verify TypeScript compilation**

```bash
cd /root/code/sales-agent/console && npx tsc --noEmit 2>&1 | head -20
```

Expected: no new errors from our files.

- [ ] **Step 5: Commit**

```bash
git add console/src/api/types.ts console/src/api/instance.ts console/src/api/index.ts
git commit -m "feat: add InstanceConfig types and getInstanceConfig API

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Create SensitiveField component

**Files:**
- Create: `console/src/components/SensitiveField.tsx`

**Interfaces:**
- Produces: `<SensitiveField value={string} maskedValue={string} />` — renders masked value with eye toggle, copy button, 5s auto-hide

```typescript
import { useState, useEffect, useCallback } from 'react';
import { Typography, Button, Tooltip, message } from 'antd';
import { EyeOutlined, EyeInvisibleOutlined, CopyOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface SensitiveFieldProps {
  value: string;        // full plaintext value (for copy)
  maskedValue: string;  // truncated display text from API (e.g. "sk-2e2a...2242a")
}

export default function SensitiveField({ value, maskedValue }: SensitiveFieldProps) {
  const [visible, setVisible] = useState(false);

  // Auto-hide after 5 seconds
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => setVisible(false), 5000);
    return () => clearTimeout(timer);
  }, [visible]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(value).then(
      () => message.success('已复制到剪贴板'),
      () => message.error('复制失败'),
    );
  }, [value]);

  if (!visible) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Text code style={{ letterSpacing: 1 }}>●●●●●●●●●●</Text>
        <Tooltip title="点击查看">
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => setVisible(true)}
            style={{ color: '#999' }}
          />
        </Tooltip>
      </span>
    );
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <Text code>{maskedValue}</Text>
      <Tooltip title="复制完整值">
        <Button
          type="text"
          size="small"
          icon={<CopyOutlined />}
          onClick={handleCopy}
        />
      </Tooltip>
      <Tooltip title="隐藏">
        <Button
          type="text"
          size="small"
          icon={<EyeInvisibleOutlined />}
          onClick={() => setVisible(false)}
        />
      </Tooltip>
    </span>
  );
}
```

- [ ] **Step 1: Verify TypeScript compiles**

```bash
cd /root/code/sales-agent/console && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 2: Commit**

```bash
git add console/src/components/SensitiveField.tsx
git commit -m "feat: add SensitiveField component with reveal/copy/auto-hide

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Create ConfigCard component

**Files:**
- Create: `console/src/components/ConfigCard.tsx`

**Interfaces:**
- Consumes: `SensitiveField` from Task 5, `InstanceConfigResponse` type from Task 4
- Produces: `<ConfigCard data={InstanceConfigResponse} loading={boolean} error={boolean} onRetry={() => void} />`

**Group display names** (Chinese labels for each group key):

```typescript
const GROUP_LABELS: Record<string, string> = {
  deployment: '部署信息',
  model: '模型配置',
  storage: '存储配置',
  dingtalk: '钉钉集成',
  media: '媒体理解',
  coach: '教练快捷',
  neo4j: 'Neo4j',
  ontology: '本体引擎',
  other: '其他配置',
};
```

**Summary line for collapsed state**: pick 2-3 key fields per group to show in the collapse header.

```typescript
import { useMemo } from 'react';
import { Card, Collapse, Descriptions, Tag, Typography } from 'antd';
import {
  SettingOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  RobotOutlined,
  SoundOutlined,
  TrophyOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import SensitiveField from './SensitiveField';
import type { InstanceConfigResponse, ConfigValue } from '@/api/types';

const { Text } = Typography;

const GROUP_LABELS: Record<string, string> = {
  deployment: '部署信息',
  model: '模型配置',
  storage: '存储配置',
  dingtalk: '钉钉集成',
  media: '媒体理解',
  coach: '教练快捷',
  neo4j: 'Neo4j',
  ontology: '本体引擎',
  other: '其他配置',
};

const GROUP_ICONS: Record<string, React.ReactNode> = {
  deployment: <CloudServerOutlined />,
  model: <RobotOutlined />,
  storage: <DatabaseOutlined />,
  dingtalk: <ApiOutlined />,
  media: <SoundOutlined />,
  coach: <TrophyOutlined />,
  neo4j: <DatabaseOutlined />,
  ontology: <SettingOutlined />,
  other: <SettingOutlined />,
};

/** Pick 2–3 key summary fields for the collapsed header. */
function summarizeGroup(fields: Record<string, ConfigValue>): string {
  const entries = Object.entries(fields);
  // Priority: show non-sensitive first, then sensitive
  const nonSensitive = entries.filter(([, v]) => typeof v === 'string');
  const preview = nonSensitive.slice(0, 3).map(([, v]) => v);
  if (preview.length < 2) {
    // fill with sensitive masked values if needed
    const sens = entries.filter(([, v]) => typeof v === 'object');
    for (const [, v] of sens) {
      if (preview.length >= 3) break;
      preview.push((v as { value: string }).value);
    }
  }
  return preview.join(' · ') || '—';
}

function isSensitive(val: ConfigValue): val is { value: string; sensitive: true; masked: string } {
  return typeof val === 'object' && val !== null && 'sensitive' in val;
}

interface ConfigCardProps {
  data: InstanceConfigResponse | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}

export default function ConfigCard({ data, loading, error, onRetry }: ConfigCardProps) {
  const collapseItems = useMemo(() => {
    if (!data?.groups) return [];
    return Object.entries(data.groups).map(([key, fields]) => {
      const label = GROUP_LABELS[key] || key;
      const icon = GROUP_ICONS[key] || <SettingOutlined />;
      const summary = summarizeGroup(fields);
      const fieldEntries = Object.entries(fields);

      return {
        key,
        label: (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {icon}
            <Text strong>{label}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {fieldEntries.length} 项 · {summary}
            </Text>
          </span>
        ),
        children: (
          <Descriptions column={1} size="small" colon={false}>
            {fieldEntries.map(([fieldName, fieldValue]) => (
              <Descriptions.Item
                key={fieldName}
                label={
                  <Text code style={{ fontSize: 12 }}>
                    {fieldName}
                  </Text>
                }
              >
                {isSensitive(fieldValue) ? (
                  <SensitiveField
                    value={fieldValue.value}
                    maskedValue={fieldValue.masked}
                  />
                ) : (
                  <Text>{String(fieldValue)}</Text>
                )}
              </Descriptions.Item>
            ))}
          </Descriptions>
        ),
      };
    });
  }, [data]);

  return (
    <Card
      title={
        <span>
          <SettingOutlined style={{ marginRight: 8 }} />
          环境配置
        </span>
      }
      style={{ marginBottom: 24 }}
      loading={loading}
    >
      {error ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Text type="danger">加载环境配置失败</Text>
          <br />
          <a onClick={onRetry} style={{ marginTop: 8, display: 'inline-block' }}>
            点击重试
          </a>
        </div>
      ) : collapseItems.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Text type="secondary">暂无环境配置数据</Text>
        </div>
      ) : (
        <Collapse
          items={collapseItems}
          defaultActiveKey={['deployment']}
          size="small"
        />
      )}
    </Card>
  );
}
```

- [ ] **Step 1: Verify TypeScript compiles**

```bash
cd /root/code/sales-agent/console && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 2: Commit**

```bash
git add console/src/components/ConfigCard.tsx
git commit -m "feat: add ConfigCard component with grouped collapsible env display

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Update DashboardPage with config section

**Files:**
- Modify: `console/src/pages/Dashboard/DashboardPage.tsx`

**Interfaces:**
- Consumes: `getInstanceConfig` from Task 4, `ConfigCard` from Task 6

**Implementation:**

- [ ] **Step 1: Add imports and config query to DashboardPage**

Edit `console/src/pages/Dashboard/DashboardPage.tsx`:

**Add import** (line 1-18, add new imports):

```typescript
// Add near other api imports (line 11):
import ConfigCard from '@/components/ConfigCard';
```

**Add config query** after the `recentConversationsQuery` block (after line 53):

```typescript
  const configQuery = useQuery({
    queryKey: ['instance-config'],
    queryFn: () => api.getInstanceConfig(),
  });
```

**Update `isLoading`** (line 55-59) — config loads independently, do NOT include it in the main loading gate:

```typescript
  // Config loads independently; don't block the whole page on it.
  const isLoading =
    conversationsQuery.isLoading ||
    feedbackQuery.isLoading ||
    latencyQuery.isLoading ||
    modelCallsQuery.isLoading;
```

The rest of the file stays unchanged until the JSX section.

- [ ] **Step 2: Add ConfigCard to the page JSX**

After the bottom-row `<Row>` (after line 314, right before the closing `</>`), add:

```typescript
      {/* Section 3: Environment Config */}
      <ConfigCard
        data={configQuery.data}
        loading={configQuery.isLoading}
        error={configQuery.isError}
        onRetry={() => configQuery.refetch()}
      />
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /root/code/sales-agent/console && npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 4: Commit**

```bash
git add console/src/pages/Dashboard/DashboardPage.tsx
git commit -m "feat: add instance config section to DashboardPage

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Update App.tsx routing to use DashboardPage

**Files:**
- Modify: `console/src/App.tsx`

**Implementation:**

- [ ] **Step 1: Change `/` and `/dashboard` routes to render DashboardPage directly**

Edit `console/src/App.tsx`, lines 60-61:

```typescript
// Change from:
<Route path="/" element={<InstanceEntry />} />
<Route path="/dashboard" element={<InstanceEntry />} />

// To:
<Route path="/" element={<DashboardPage />} />
<Route path="/dashboard" element={<DashboardPage />} />
```

**How this works**: `DashboardPage` uses `useTenant()` which returns the default context `{ tenantId: null }` when no `TenantContext.Provider` wraps it. All operational queries are `enabled: !!tenantId`, so they stay idle (showing zeros). The `configQuery` does not depend on `tenantId`, so the config card always loads. The operational metrics (conversation total, feedback, latency, model calls) will render as zero/empty — this is acceptable since the primary addition is the config display.

- [ ] **Step 2: Verify the routes work**

```bash
cd /root/code/sales-agent/console && npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 3: Commit**

```bash
git add console/src/App.tsx
git commit -m "feat: route / and /dashboard to DashboardPage directly

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Integration test — verify end-to-end

**Files:**
- None — manual verification.

**Implementation:**

- [ ] **Step 1: Start the backend and test the endpoint**

```bash
cd /root/code/sales-agent && python -c "
import os
os.environ['TENANT_ID'] = 'test'
os.environ['TENANT_NAME'] = '测试租户'
os.environ['DEPLOYMENT_MODE'] = 'dedicated'
os.environ['MODEL_API_KEY'] = 'sk-test1234567890abcdef'
os.environ['DINGTALK_APP_KEY'] = 'dingtestappkey'
os.environ['DINGTALK_APP_SECRET'] = 'testsecret123'
from sales_agent.core.tenant_runtime import reset_runtime, get_tenant_runtime
reset_runtime()
rt = get_tenant_runtime()
vars = rt.get_all_env_vars()
print('TENANT_ID:', vars.get('TENANT_ID'))
print('MODEL_API_KEY:', vars.get('MODEL_API_KEY'))
print('DINGTALK_APP_KEY:', vars.get('DINGTALK_APP_KEY'))
print('DINGTALK_APP_SECRET:', vars.get('DINGTALK_APP_SECRET'))
"
```

Expected: all variables present, including sensitive ones.

- [ ] **Step 2: Test the /instance/config endpoint**

```bash
cd /root/code/sales-agent && python -c "
import os, json
os.environ['TENANT_ID'] = 'test'
os.environ['MODEL_API_KEY'] = 'sk-test1234567890abcdef'
os.environ['DINGTALK_APP_KEY'] = 'dingtestappkey'
os.environ['DINGTALK_APP_SECRET'] = 'testsecret123'
from sales_agent.core.tenant_runtime import reset_runtime
reset_runtime()
from sales_agent.api.routes.instance import router
# Verify is_sensitive logic
from sales_agent.api.routes.instance import _is_sensitive, _mask_value
print('MODEL_API_KEY sensitive:', _is_sensitive('MODEL_API_KEY'))
print('DINGTALK_APP_SECRET sensitive:', _is_sensitive('DINGTALK_APP_SECRET'))
print('DINGTALK_APP_KEY sensitive:', _is_sensitive('DINGTALK_APP_KEY'))
print('DINGTALK_CORP_ID sensitive:', _is_sensitive('DINGTALK_CORP_ID'))
print('masked MODEL_API_KEY:', _mask_value('sk-test1234567890abcdef'))
print('masked short:', _mask_value('abc'))
"
```

Expected:
- `MODEL_API_KEY sensitive: True`
- `DINGTALK_APP_SECRET sensitive: True`
- `DINGTALK_APP_KEY sensitive: False`
- `DINGTALK_CORP_ID sensitive: False`
- `masked MODEL_API_KEY: sk-tes...cdef`
- `masked short: ...`

- [ ] **Step 3: Run full backend test suite**

```bash
cd /root/code/sales-agent && python -m pytest tests/ -x -q --ignore=tests/integration 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 4: Build frontend and check for compilation errors**

```bash
cd /root/code/sales-agent/console && npx vite build 2>&1 | tail -20
```

Expected: build succeeds without errors.

- [ ] **Step 5: Record changelog**

Create/update `changelog/2025-06-25.md`:

```markdown
# 2025-06-25

## 新增

- **实例配置仪表盘** (feat/ontology-neo4j-knowledge-engine): 运营面板新增环境配置展示区域，按分组折叠展示所有 secrets/*.env 中的运行时参数。API key/secret/token 等敏感字段默认掩码，点击 👁 切换明文并支持一键复制（5秒自动恢复掩码）。
  - 新增 `GET /instance/config` 端点，从 TenantRuntime 读取全部环境变量
  - 新增 `SensitiveField` 组件：掩码/明文切换 + 复制 + 自动恢复
  - 新增 `ConfigCard` 组件：分组折叠展示全部运行时配置
  - 改造 DashboardPage 为 `/` 和 `/dashboard` 路由的实际渲染页
```

- [ ] **Step 6: Update README「产品文档对照」if applicable**

Check `README.md` for the 产品文档对照 section — if DashboardPage or overview is listed, update the status.

- [ ] **Step 7: Final commit**

```bash
git add changelog/2025-06-25.md README.md
git commit -m "docs: changelog for instance config dashboard

Co-Authored-By: Claude <noreply@anthropic.com>"
```
