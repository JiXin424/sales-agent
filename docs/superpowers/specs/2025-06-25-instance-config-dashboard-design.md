# Instance Config Dashboard 设计规格

## 概述

将 dedicated mode 下的运营面板（DashboardPage）改造为综合面板，同时展示运营指标和运行时环境配置（来自 `secrets/*.env`）。

## 范围

- 后端：新增 `GET /instance/config` 端点，返回完整运行时配置
- 前端：改造 `DashboardPage.tsx` 为 `/` 和 `/dashboard` 的实际渲染页（不再重定向到 AgentOverviewPage）
- 前端：新增环境配置展示卡片，支持分组折叠 + 敏感字段点击切换明文

## 不在范围

- 不修改 multi-tenant 模式的 DashboardPage 行为
- 不修改 Secrets 文件本身
- 不修改 AgentOverviewPage

---

## 后端设计

### 端点：`GET /instance/config`

**文件位置**：`src/sales_agent/api/routes/instance.py`（新建）

**返回结构**（JSON）：

```json
{
  "deployment": {
    "DEPLOYMENT_MODE": "dedicated",
    "TENANT_ID": "taishan",
    "TENANT_NAME": "泰山兄弟开发版"
  },
  "model": {
    "MODEL_PROVIDER": "openai_compatible",
    "MODEL_BASE_URL": "https://api.deepseek.com",
    "MODEL_CHAT_MODEL": "deepseek-chat",
    "MODEL_EMBEDDING_MODEL": "text-embedding-v3",
    "MODEL_API_KEY": { "value": "sk-2e2a...2242a", "sensitive": true },
    "EMBEDDING_API_KEY": { "value": "sk-9ce6...beac8", "sensitive": true },
    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1"
  },
  "storage": {
    "VECTOR_COLLECTION": "taishan",
    "DATA_DIR": "/data/taishan",
    "LOG_DIR": "/logs/taishan"
  },
  "dingtalk": {
    "DINGTALK_ENABLED": "true",
    "DINGTALK_MESSAGE_MODE": "stream",
    "DINGTALK_APP_KEY": "dingwfixvrzcdnaekder",
    "DINGTALK_APP_SECRET": { "value": "0wiB8B...bTH", "sensitive": true },
    "DINGTALK_CORP_ID": "ding439bebb99b6d535eacaaa37764f94726",
    "DINGTALK_ROBOT_CODE": "dingwfixvrzcdnaekder",
    "DINGTALK_STREAMING_ENABLED": "true",
    "DINGTALK_CARD_TEMPLATE_ID": "74793cfd-c44d-4c84-8370-2e89bd74cdb3.schema",
    "DINGTALK_STREAM_UPDATE_INTERVAL_MS": "300",
    "DINGTALK_STREAM_MIN_CHUNK_CHARS": "30",
    "DINGTALK_PUBLIC_URL": "https://aijiaolian.com.cn",
    "DINGTALK_ENCRYPT_TOKEN": { "value": "", "sensitive": true },
    "DINGTALK_AES_KEY": { "value": "", "sensitive": true }
  },
  "media": {
    "DINGTALK_MEDIA_ENABLED": "true",
    "DINGTALK_MEDIA_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "DINGTALK_MEDIA_API_KEY": { "value": "sk-9ce6...beac8", "sensitive": true },
    "DINGTALK_VISION_MODEL": "qwen-vl-plus",
    "DINGTALK_AUDIO_MODEL": "qwen-audio-turbo-latest"
  },
  "coach": {
    "DINGTALK_REGISTER_QUICK_ENTRY": "true",
    "DINGTALK_QUICK_ENTRY_CLEAR_FIRST": "true",
    "DINGTALK_QUICK_ENTRY_ENTRIES": "coach,small_win_appreciation,sales_block_breakthrough",
    "DINGTALK_QUICK_ENTRY_NAME": "教练模式"
  }
}
```

### 敏感字段判断规则

字段名匹配以下模式之一，标记 `sensitive: true`：
- `*_API_KEY`
- `*_SECRET`
- `*_TOKEN`
- `*_AES_KEY`
- `*_ENCRYPT_TOKEN`

### 数据来源

从 `TenantRuntime` 单例（`get_tenant_runtime()`）读取当前实例的环境变量。

`TenantRuntime` 需扩展：添加方法 `get_all_env_vars()` -> `dict[str, str]`，返回所有相关环境变量。

### 注册路由

在 `main.py` 中 `app.include_router(instance_router)`。

---

## 前端设计

### 路由变更

`App.tsx` 中：
- `/` → 渲染 `<DashboardPage />`（不再重定向到 InstanceEntry）
- `/dashboard` → 渲染 `<DashboardPage />`
- `/agents/:agentId/overview` → 保留 `<AgentOverviewPage />`

### DashboardPage 布局（从上到下）

```
┌─────────────────────────────────────────────────┐
│  运营面板                                         │
│  系统运行状态与配置总览                              │
├─────────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐           │
│  │对话总量│ │好评率 │ │P50延迟│ │模型调用│           │
│  │ 1,234 │ │ 85%  │ │ 420ms│ │ 5,678 │           │
│  └──────┘ └──────┘ └──────┘ └──────┘           │
├─────────────────────────────────────────────────┤
│  近期任务类型分布    │  模型调用状态概览              │
├─────────────────────────────────────────────────┤
│  环境配置                          [全部展开/折叠] │
│  ▼ 部署信息   dedicated · taishan                 │
│  ▼ 模型配置   openai_compatible · deepseek-chat   │
│  ▶ 存储配置   taishan · /data/taishan             │
│  ▶ 钉钉集成   已启用 · stream 模式                 │
│  ▶ 媒体理解   qwen-vl-plus · qwen-audio           │
│  ▶ 教练快捷   已启用 · 3个入口                     │
└─────────────────────────────────────────────────┘
```

### ConfigCard 子组件

每个分组：
- **折叠标题行**：分组名 + 关键摘要（2-3个关键值预览）
- **展开内容**：`Descriptions` 组件，每行一个字段
- **敏感字段**：
  - 默认：圆点掩码 `●●●●●●●●●●`
  - 右侧 👁 图标按钮
  - 点击切换：显示截断值（`sk-2e2a...2242a`）+ 📋 复制按钮
  - 明文模式 5 秒后自动恢复掩码（前端 timer）

### 新增文件

| 文件 | 说明 |
|------|------|
| `console/src/api/instance.ts` | `getInstanceConfig()` API 函数 |
| `console/src/components/ConfigCard.tsx` | 环境配置卡片组件 |
| `console/src/components/SensitiveField.tsx` | 敏感字段渲染组件（点击切换） |

### 修改文件

| 文件 | 变更 |
|------|------|
| `console/src/api/index.ts` | 导出 `instance.ts` |
| `console/src/pages/Dashboard/DashboardPage.tsx` | 加载配置数据 + 渲染 ConfigCard |
| `console/src/App.tsx` | 修改 `/` 和 `/dashboard` 路由 |
| `src/sales_agent/core/tenant_runtime.py` | 新增 `get_all_env_vars()` 方法 |
| `src/sales_agent/api/routes/instance.py` | 新建端点 |
| `src/sales_agent/main.py` | 注册 instance router |

### 状态处理

| 状态 | 展示 |
|------|------|
| 加载中 | 运营指标区域显示 LoadingState；配置区域显示骨架屏 |
| 配置加载失败 | 配置卡片显示 ErrorState（不影响运营指标） |
| 运营指标加载失败 | 指标区域 ErrorState（不影响配置卡片） |
| 空配置（无 env 数据） | ConfigCard 显示 EmptyState "暂无环境配置数据" |

---

## 验证

- [ ] `GET /instance/config` 返回 200 + 正确分组数据
- [ ] 敏感字段标记 `sensitive: true`
- [ ] 前端 DashboardPage 正常渲染运营指标 + 配置卡片
- [ ] 敏感字段默认掩码，点击 👁 切换明文
- [ ] 明文模式 5 秒后自动恢复掩码
- [ ] 复制按钮正常工作
- [ ] 配置加载失败不阻塞运营指标展示
- [ ] `/` 和 `/dashboard` 路由正确渲染 DashboardPage
