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

## PC 端成功方案（2026-06-17，OAuth2 网页免登）— sales-agent / qiyelongxia

> 本节为 **sales-agent** 项目（域名 `qiyelongxia.com.cn`、机器人 `dingwfixvrzcdnaekder`、模块 `src/sales_agent/integrations/dingtalk/`）。手机端仍走上方「最终成功方案」的 JSAPI `requestAuthCode`，本节专门解决 **PC 端**。

### 背景：为什么 PC 不能复用 JSAPI

PC 钉钉点机器人快捷入口**强制跳系统浏览器**（Chrome/Edge）打开 H5，浏览器里没有钉钉 JSAPI 桥（console 报 `[DINGTALK-JSAPI] ERROR 4040 notInDingTalk` + `5010 JsBridge initialization failed`，且出现 Vue devtools 的 `installHook.js` 即铁证在普通浏览器里），`dd.config`/`requestAuthCode` 全部不可用。试过的无效方案：

- `dd.config` 鉴权 → 浏览器里 JSAPI 桥根本不存在，`dd.config` 直接炸（不是签名/域名问题）。
- pcUrl 用 `dingtalk://dingtalkclient/page/link?url=...` 想强制侧边栏内嵌 → 钉钉 API 接受（`plugins/set` 返 true）但 **PC 客户端不认，照样跳浏览器**。

→ 浏览器里识别用户**只能走 OAuth2 网页扫码登录**。

### 完整链路

```
PC 钉钉点「教练模式」快捷入口
  → 钉钉跳系统浏览器打开 cocah.html
  → 点「访前准备教练」→ JS 检测 UA 非 DingTalk
  → 跳转 https://login.dingtalk.com/oauth2/auth
       ?redirect_uri=https://qiyelongxia.com.cn/integrations/dingtalk/oauth2-callback
       &client_id=<AppKey>  &response_type=code  &scope=openid  &prompt=consent
       &state=pre_visit_prepare:taishan
  → 用户扫码/登录（已登录钉钉网页则自动）
  → 钉钉回跳 GET /integrations/dingtalk/oauth2-callback?authCode=xxx&state=pre_visit_prepare:taishan
  → 后端 get_userid_by_oauth2_code(authCode):
       1. authCode    → userAccessToken  POST  api.dingtalk.com/v1.0/oauth2/userAccessToken   {clientId,clientSecret,code,grantType=authorization_code}
       2. userAccessToken → unionId       GET   api.dingtalk.com/v1.0/contact/users/me         Header: x-acs-dingtalk-access-token=<userAccessToken>
       3. unionId     → staffId userId   POST  oapi.dingtalk.com/topapi/user/getbyunionid     {unionid}   （用「应用级」access_token）
  → _fulfill_quick_action(userId) → send_text(普通入口) / start_session(多轮入口)
  → 机器人向用户单聊发消息
  → 浏览器返回极简 ✅ 页，加载即 `window.close()` + 跳 `dingtalk://dingtalkclient` 弹回钉钉
```

> **成功页 UX（实测）**：`dingtalk://dingtalkclient`（**不带外链 url**）能可靠置前钉钉客户端。⚠️ 切勿用 `dingtalk://dingtalkclient/page/link?url=<https外链>`——钉钉会把那个 url 当外链甩到**系统浏览器新开标签**。`window.close()` 在 Chrome 里关不掉"非脚本打开"的标签（钉钉拉起的 Chrome 标签关不掉），故 ✅ 页标签会留存、需手动关；失败时仍返回带错误信息的页面供排查。

### 权限点（两个，缺一不可，且是不同权限）

| 步骤 | 接口 | token 类型 | 所需权限点 | 缺时报错 |
|------|------|-----------|-----------|---------|
| 2 | `contact/users/me` | **用户级** userAccessToken | **`Contact.User.Read`**（通讯录个人信息读）| `403 Forbidden.AccessDenied.AccessTokenPermissionDenied`，body 里 `requiredScopes:["Contact.User.Read"]` |
| 3 | `topapi/user/getbyunionid` | **应用级** access_token | `qyapi_get_member`（成员信息读）| `errcode 88 / subcode 60011` |

> **关键认知**：用户级 token 和应用级 token 的权限是**两套**。`contact/users/me` 用用户级 token 调、认 `Contact.User.Read`；`getbyunionid` 用应用级 token 调、认 `qyapi_get_member`。只开一个会分别 403 / 60011。
> **排查技巧**：`contact/users/me` 403 时务必把响应体完整抛出，`accessdenieddetail.requiredScopes` 会直接点名缺哪个 scope，不用猜。

### 关键配置

| 配置项 | 位置 | 值 |
|--------|------|-----|
| 权限 `qyapi_get_member` | 开发者后台 → 应用 → 权限管理 → 通讯录管理 | 成员信息读（应用级 token 用）|
| 权限 `Contact.User.Read` | 同上 | 通讯录个人信息读（用户级 token 用）|
| OAuth2 回调域名 | 开发者后台 → 应用 → 登录配置 → 重定向 URL | `https://qiyelongxia.com.cn/integrations/dingtalk/oauth2-callback` |
| env | `secrets/taishan.env` | `DINGTALK_APP_KEY/SECRET`（=OAuth2 的 client_id/client_secret）|

### 涉及的文件（sales-agent）

| 文件 | 作用 |
|------|------|
| `src/sales_agent/integrations/dingtalk/quick_entry.py` | `GET /oauth2-callback`（authCode→发消息，返回 HTML 结果页）+ `_fulfill_quick_action`（whoami 与 oauth2-callback 共用收尾）+ `_parse_oauth_state`/`_oauth_result_page`；`/quick` 渲染时下发 `__APP_KEY__`（=client_id，AppKey 非机密）|
| `src/sales_agent/integrations/dingtalk/message_sender.py` | `DingTalkMessageSender.get_userid_by_oauth2_code(authCode)`：上面三步换出 staffId userId，每步错误都把钉钉响应体带进异常 |
| `static/cocah.html`、`static/quick_trigger.html` | UA 检测分流：非 DingTalk → `redirectToOAuth2`；DingTalk → JSAPI 流程 |
| `tests/unit/dingtalk/test_quick_entry_oauth2.py` | `_parse_oauth_state` / `_oauth_result_page` 单测（7 例）|

### 前端 UA 分流（手机/PC 共用一份 H5）

```js
// 点击时
if (!/DingTalk/i.test(navigator.userAgent)) {
  redirectToOAuth2(action);   // PC 浏览器 → 跳 login.dingtalk.com
  return;
}
// 钉钉容器（手机端 webview）→ JSAPI requestAuthCode 流程（不变）
```

### 部署

改 `quick_entry.py` / `message_sender.py` / 两个 HTML 后：
```
docker build -t sales-agent:latest .
docker compose --profile taishan-split up -d --force-recreate taishan-api
```
（HTML 与 `/oauth2-callback` 端点都由 `taishan-api` 提供，`taishan-stream` 不涉及。容器代码是 COPY 进镜像、非挂载，只 restart 不生效。）

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

> **❌ 2026-06-17 纠正：这个结论是错的，OAuth2 能走通。** 当时是三重误判叠加：
> 1. **端点名拼错**：试的是 `topapi/v2/user/getbyunionid`（多了 `/v2/`）、`user/getbyunionid`（缺 `/topapi/`）、`getuseridbyunionid`（旧名）——**正确端点是 `topapi/user/getbyunionid`（无 `/v2/`）**，errcode=0、往返一致（已实测）。
> 2. **缺权限**：`contact/users/me`（OAuth2 拿 unionId 那步）要的是 `Contact.User.Read`（用户级 token），不是 `qyapi_get_member`（应用级 token），当时两个都没开。
> 3. 把"端点名错 + 缺权限"导致的报错误判成了"接口废弃"。
>
> 正确的 PC 端 OAuth2 完整链路见上方「**PC 端成功方案（2026-06-17）**」，已实测跑通。

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
