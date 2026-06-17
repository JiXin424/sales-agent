# 钉钉酷应用安装与快捷入口实现总结

## 最终成功方案

**JSAPI `requestAuthCode` + `exchange_auth_code` + v1.0 `batchSend`**

### 完整链路

```
用户点击"AI助手"快捷入口
  → H5 页面展示确认按钮
  → 点击按钮，调用 JSAPI dd.runtime.permission.requestAuthCode({corpId})
  → 获取 authCode
  → GET /api/dingtalk/whoami?code=xxx&action=ai_assistant&tenant_id=xxx
  → exchange_auth_code(code) → topapi/v2/user/getuserinfo → 返回 userid (staffId 格式)
  → send_single_chat_text(userid, "点击成功 ✅") → v1.0/robot/oToMessages/batchSend
  → 机器人向用户发送单聊消息
```

### 关键配置

| 配置项 | 位置 | 值 |
|--------|------|-----|
| 端内免登地址 | 钉钉开放平台 → 应用 → 安全设置 | `https://aijiaolian.com.cn` |
| 回调域名 | 钉钉开放平台 → 应用 → 安全设置 → 重定向URL | `https://aijiaolian.com.cn/api/dingtalk/oauth2/callback` |
| 快捷入口 | 钉钉 API `POST /v1.0/robot/plugins/set` | 见下方示例 |

快捷入口配置示例：
```json
{
  "robotCode": "dingzdqpllocwfuab27m",
  "pluginInfoList": [{
    "name": "{\"zh_CN\": \"AI助手\"}",
    "icon": "@lALPM3DGuznknlcwMA",
    "pcUrl": "https://aijiaolian.com.cn/api/dingtalk/quick?action=ai_assistant&tenant_id=xxx",
    "mobileUrl": "https://aijiaolian.com.cn/api/dingtalk/quick?action=ai_assistant&tenant_id=xxx"
  }]
}
```

### 涉及的文件

| 文件 | 变更 |
|------|------|
| `api/routers/dingtalk_cool_app.py` | quick entry H5 页面 + whoami/oauth2 回调 + ai_assistant 处理 |
| `api/core/channels/dingtalk/sdk/client.py` | exchange_oauth2_code、send_single_chat_text |
| `api/models/workflow.py` | 补齐 cool_app_code 字段 |
| `api/main.py` | 注册 dingtalk_cool_app、dingtalk_org 路由 |
| `scripts/cool_app_bundle/install.bundle.js` | dingtalk-jsapi esbuild 打包（batchInstallCoolApp） |
| `web/app/dingtalk/install/page.tsx` | Next.js 安装页面（备用） |
| Traefik `/root/code/TaishanXD/traefik/dynamic.yml` | 新增 omni-dingtalk-api-router 路由 + 修复 coach-admin-router 重复定义 bug |

---

## 走过的弯路

### 弯路 1：Cool App vs Quick Entry 混淆

**问题**：以为安装酷应用（batchInstallCoolApp）后会自动出现快捷入口按钮。

**真相**：这是两个独立功能——
- **酷应用安装** (`batchInstallCoolApp`)：安装后在聊天侧边栏 Tab 出现
- **快捷入口按钮**：通过 API `POST /v1.0/robot/plugins/set` 设置，出现在输入框上方

### 弯路 2：CDN dingtalk-jsapi 调用 batchInstallCoolApp

**问题**：用 CDN `<script src="dingtalk.open.js">` 加载 JSAPI，然后调用 `dd.biz.coolApp.batchInstallCoolApp()`，页面一直转圈不返回。

**真相**：`batchInstallCoolApp` 是 npm 模块 `dingtalk-jsapi/plugin/coolAppSdk` 的导出函数，在 CDN 版本中**不存在于 `dd` 全局对象上**。它被 webpack 打包为内部模块，外部无法直接调用。

**解决**：用 esbuild 把 npm 包打包成独立 JS 文件，内联到后端渲染的 H5 页面中。

### 弯路 3：OAuth2 → unionId/openId → send_single_chat_text 走不通

**问题**：OAuth2 授权码换回的字段只有 `openId` 和 `unionId`（如 `EmiizaaiP9Mpbo03AgQSSowAiEiE`），而 `v1.0/robot/oToMessages/batchSend` 的 `userIds` 只接受旧版 `senderStaffId` 格式（如 `022045516146-1529960953`）。

**尝试过的失败 API**：

| API | 结果 |
|-----|------|
| `oapi/topapi/v2/user/getbyunionid` (POST) | `不合法ApiName` — 接口已废弃 |
| `oapi/user/getbyunionid` (GET) | `404` — 接口不存在 |
| `v1.0/contact/users/unionIdToUserid` (POST) | `InvalidAction.NotFound` |
| `v1.0/contact/users/query` (POST) | `InvalidAction.NotFound` |
| `v1.0/contact/users/me` (GET) | ✅ 可用但只返回 openId/unionId，不返回 userId |
| `oapi/user/get?userid=unionId` (GET) | `找不到该用户` — userid 格式不对 |
| batchSend 用 `openIds` 参数 | `MissinguserIds` — userIds 是必填字段 |
| `oapi/asyncsend_v2` | `Invalid arguments:agent_id` — agent_id 获取困难 |

**结论**：OAuth2 无法获得旧版 userId，这条路彻底不通。

### 弯路 4：JSAPI 最初不工作

**问题**：页面卡在"正在验证身份..."一直转圈。`dd.ready()` 回调不触发，`dd.error()` 也不触发。

**原因**：
1. 最早页面在 `dd.ready` 回调里设置 `jsapiDone = true`，但如果 `requestAuthCode` 内部挂住，超时检查因为 `jsapiDone` 已为 true 而失效
2. 没有配置**端内免登地址**，钉钉客户端拒绝执行 JSAPI

**解决**：
- 页面改为立即显示按钮，用户手动点击时触发 JSAPI
- 配置端内免登地址 `https://aijiaolian.com.cn`

### 弯路 5：Traefik 路由缺失

**问题**：HTTPS 下访问 API 返回 502 Bad Gateway。

**原因**：
1. 没有 `/api/dingtalk/*` 的路由转发
2. Traefik `dynamic.yml` 中 `coach-admin-router` 重复定义，导致**整个配置文件加载失败**

**解决**：新增 `omni-dingtalk-api-router` 路由，同时修复 YAML 重复 key bug。

### 弯路 6：后端路由未注册

**问题**：`/api/dingtalk/cool-app-config` 返回 404。

**原因**：`api/main.py` 没有 `include_router(dingtalk_cool_app_router)`。

### 弯路 7：async_session 导入错误

**问题**：`ImportError: cannot import name 'async_session' from 'api.database'`。

**原因**：`_get_tenant_config` 中错误导入了不存在的 `async_session`，正确的是 `session_factory`。

### 弯路 8：async 上下文冲突

**问题**：`asyncio.run() cannot be called from a running event loop`。

**原因**：`_get_tenant_config` 是同步函数，内部用 ThreadPoolExecutor + asyncio.run() 调用异步数据库操作。FastAPI 的 async endpoint 已有 running event loop。

**解决**：改为 `async def _get_tenant_config` + 直接 `await session_factory()`。

---

## 经验教训

1. **先验证 API 可用性，再写代码**。不要假设旧 oapi 还能用——钉钉在逐步废弃旧接口。
2. **OAuth2 vs JSAPI 选 JSAPI**。`requestAuthCode` 一步拿到 userId，OAuth2 需要额外转换且各种接口已废弃。
3. **批量子代理调用极费 token**。这个任务跨越 3 个模型，上百轮对话。
4. **及时总结减少试错**。每验证一个 API 不可用就该记录，避免反复尝试。
5. **端内免登地址是 JSAPI 前提**。不加这个配置，JSAPI 永远不会工作。

---

> 最后更新：2026-06-04
