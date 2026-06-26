# 钉钉快捷入口 — PC 端 OAuth2 网页免登方案（完整流程）

> **项目**：sales-agent（域名 `qiyelongxia.com.cn`，单聊机器人 `dingwfixvrzcdnaekder`，模块 `src/sales_agent/integrations/dingtalk/`）
> **日期**：2026-06-17　**状态**：已实测跑通
> **适用**：PC 钉钉点机器人快捷入口 → 跳浏览器 → 识别用户 → 发消息。手机端走 JSAPI（见 `anniu.md`），本文件只讲 PC。

---

## 1. 为什么 PC 不能复用手机端 JSAPI

| 端 | 点快捷入口后 | JSAPI 桥 | requestAuthCode |
|----|------------|---------|----------------|
| 手机 | 钉钉**手机端 webview** 打开 H5 | ✅ 有 | ✅ 可用 |
| PC | 钉钉**强制跳系统浏览器**（Chrome/Edge）打开 H5 | ❌ 无 | ❌ 不可用 |

PC 浏览器里没有钉钉 JSAPI 桥，铁证（console）：
- `[DINGTALK-JSAPI] ERROR 4040: Do not support the current environment：'notInDingTalk'`
- `5010: JsBridge initialization failed and "dd.config" failed to call`
- 出现 Vue devtools 的 `installHook.js`（钉钉内嵌 webview 不可能有浏览器扩展）

试过的无效方案（别再试）：
- ❌ `dd.config` 鉴权 → 浏览器里桥根本不存在，`dd.config` 直接炸（不是签名/域名问题）。
- ❌ pcUrl 用 `dingtalk://dingtalkclient/page/link?url=...` 想强制侧边栏 → 钉钉 API 接受（`plugins/set` 返 true）但 **PC 客户端不认，照样跳浏览器**。

**结论**：浏览器里识别用户只能走 **OAuth2 网页扫码登录**。

---

## 2. 完整链路

```
PC 钉钉点「教练模式 / 小赢欣赏 / 卡点破框」快捷入口
  → 钉钉跳系统浏览器打开 H5（cocah.html 或 quick_trigger.html）
  → 点击按钮 → JS 检测 navigator.userAgent 非 DingTalk
  → 跳转 https://login.dingtalk.com/oauth2/auth
       ?redirect_uri=https://qiyelongxia.com.cn/integrations/dingtalk/oauth2-callback
       &client_id=<AppKey>
       &response_type=code
       &scope=openid
       &prompt=consent
       &state=<action>:<tenant_id>          例：pre_visit_prepare:taishan
  → 用户扫码 / 登录（已登录钉钉网页则自动）
  → 钉钉回跳 GET /integrations/dingtalk/oauth2-callback?authCode=xxx&state=pre_visit_prepare:taishan
  → 后端 get_userid_by_oauth2_code(authCode)（三步换 userId）：
       1. authCode        → userAccessToken  POST  api.dingtalk.com/v1.0/oauth2/userAccessToken   {clientId,clientSecret,code,grantType=authorization_code}
       2. userAccessToken → unionId          GET   api.dingtalk.com/v1.0/contact/users/me         Header: x-acs-dingtalk-access-token=<userAccessToken>
       3. unionId         → staffId userId    POST  oapi.dingtalk.com/topapi/user/getbyunionid     {unionid}   （用「应用级」access_token）
  → _fulfill_quick_action(userId)
       · 普通入口（访前/访后）→ send_text 发引导提问
       · 多轮入口（小赢欣赏/卡点破框）→ start_session 落库建会话 + 发首轮提问
  → 机器人向用户单聊发消息
  → 浏览器返回极简 ✅ 页 → 加载即 window.close() + 跳 dingtalk://dingtalkclient 弹回钉钉
```

> **关键**：OAuth2 只能拿到 `unionId`，而发消息的 `batchSend` 要 staffId 格式 `userId`，必须用 `topapi/user/getbyunionid` 转换。

---

## 3. 权限点（两个，缺一不可，且是不同权限）

| 步骤 | 接口 | token 类型 | 所需权限点 | 缺时报错 |
|------|------|-----------|-----------|---------|
| 2 | `contact/users/me` | **用户级** userAccessToken | **`Contact.User.Read`**（通讯录个人信息读） | `403 Forbidden.AccessDenied.AccessTokenPermissionDenied`，body 里 `accessdenieddetail.requiredScopes:["Contact.User.Read"]` |
| 3 | `topapi/user/getbyunionid` | **应用级** access_token | `qyapi_get_member`（成员信息读） | `errcode 88 / subcode 60011` |

> ⚠️ **用户级 token 和应用级 token 的权限是两套**。`contact/users/me` 用用户级 token 调、认 `Contact.User.Read`；`getbyunionid` 用应用级 token 调、认 `qyapi_get_member`。**只开一个会分别 403 / 60011。**
> 排查技巧：`contact/users/me` 403 时务必把响应体完整抛出，`accessdenieddetail.requiredScopes` 会直接点名缺哪个 scope，不用猜。

---

## 4. 后台配置（必做，一次性）

开发者后台 → 应用 → 找到机器人应用：

1. **权限管理 → 通讯录管理**，申请开通两个权限点：
   - `qyapi_get_member`（成员信息读）—— 给应用级 token 用（`getbyunionid`）。
   - `Contact.User.Read`（通讯录个人信息读）—— 给用户级 token 用（`contact/users/me`）。
   - （可选）`通讯录权限范围` 设为「全部成员」或包含目标用户。
2. **登录配置 → 重定向 URL**，注册回调域名：
   ```
   https://qiyelongxia.com.cn/integrations/dingtalk/oauth2-callback
   ```
   （必须与前端 OAuth2 跳转里的 `redirect_uri` 完全一致）

> 企业内部应用权限一般即时生效，无需审核。

---

## 5. 环境变量（`secrets/taishan.env`）

OAuth2 复用现有 AppKey/AppSecret（= OAuth2 的 client_id / client_secret）：

```env
DINGTALK_APP_KEY=ding...            # = OAuth2 client_id（前端 __APP_KEY__ 下发，非机密）
DINGTALK_APP_SECRET=...             # = OAuth2 client_secret（机密，仅后端）
DINGTALK_CORP_ID=ding...
DINGTALK_ROBOT_CODE=dingwfixvrzcdnaekder
DINGTALK_PUBLIC_URL=https://qiyelongxia.com.cn
```

> AppKey 非 机密，会下发前端作为 OAuth2 `client_id`（标准做法）。AppSecret 严格只在后端。

---

## 6. 涉及文件（sales-agent）

| 文件 | 作用 |
|------|------|
| `src/sales_agent/integrations/dingtalk/quick_entry.py` | `GET /oauth2-callback`（authCode → 发消息，返回 HTML 结果页）+ `_fulfill_quick_action`（whoami 与 oauth2-callback 共用收尾）+ `_parse_oauth_state` / `_oauth_result_page` / `_oauth_success_page`；`/quick` 渲染时下发 `__APP_KEY__`（=client_id） |
| `src/sales_agent/integrations/dingtalk/message_sender.py` | `DingTalkMessageSender.get_userid_by_oauth2_code(authCode)`：三步换 staffId userId，**每步错误都把钉钉响应体带进异常**（便于排错） |
| `src/sales_agent/integrations/dingtalk/static/cocah.html`、`quick_trigger.html` | UA 检测分流：非 DingTalk → `redirectToOAuth2`；DingTalk → JSAPI 流程 |
| `tests/unit/dingtalk/test_quick_entry_oauth2.py` | `_parse_oauth_state` / `_oauth_result_page` 单测（7 例） |

---

## 7. 前端 UA 分流（手机/PC 共用一份 H5）

`cocah.html` / `quick_trigger.html` 点击时：

```js
if (!/DingTalk/i.test(navigator.userAgent)) {
  redirectToOAuth2(action);   // PC 浏览器 → 跳 login.dingtalk.com
  return;
}
// 钉钉容器（手机端 webview）→ JSAPI requestAuthCode 流程（不变）
```

`redirectToOAuth2(action)`：

```js
var clientId = '__APP_KEY__';                       // /quick 渲染时由后端填入 AppKey
var redirectUri = encodeURIComponent(
  window.location.origin + '/integrations/dingtalk/oauth2-callback'
);
var state = action + ':' + tenantId;                // 例 pre_visit_prepare:taishan
window.location.href = 'https://login.dingtalk.com/oauth2/auth'
  + '?redirect_uri=' + redirectUri
  + '&client_id=' + encodeURIComponent(clientId)
  + '&response_type=code&scope=openid&prompt=consent'
  + '&state=' + encodeURIComponent(state);
```

---

## 8. 成功页：自动弹回钉钉（实测）

登录 + 发送成功后，`/oauth2-callback` 返回极简 ✅ 页，`<body onload>` 立即：

```js
function goback() {
  var dl = 'dingtalk://dingtalkclient';      // ⚠️ 不带外链 url
  try { window.opener=null; window.open('','_self'); window.close(); } catch(e){}
  setTimeout(function(){ try { window.location.href = dl; } catch(e){}}, 300);
}
```

- ✅ **`dingtalk://dingtalkclient`（不带外链 url）**：可靠置前钉钉客户端（Chrome 先弹「打开钉钉?」确认 → 点是 → 钉钉到最前，能看到刚发的消息）。
- ⚠️ **切勿**用 `dingtalk://dingtalkclient/page/link?url=<https 外链>`——钉钉会把那个 url 当外链甩到**系统浏览器新开标签**。
- ⚠️ `window.close()` 在 Chrome 里**关不掉「非脚本打开」的标签**（钉钉拉起的 Chrome 标签属于这种），故 ✅ 标签会留存、需手动关。这是浏览器安全策略的硬限制，无法绕过。
- **失败仍返回带错误信息的页面**（不自动关），供排查。

---

## 9. 部署

改了 `quick_entry.py` / `message_sender.py` / 两个 HTML 后（容器代码 COPY 进镜像、非挂载，只 restart 不生效）：

```bash
docker build -t sales-agent:latest .
docker compose --profile taishan-split up -d --force-recreate taishan-api
```

> HTML 与 `/oauth2-callback` 端点都由 `taishan-api` 提供，`taishan-stream` 不涉及。

---

## 10. 排错速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 点了不跳登录、停在 loading | 前端 UA 判定进了 JSAPI 分支（不该） | 确认是在浏览器里测；查 console 是否有 `redirectToOAuth2` |
| 跳到登录页报 `redirect_uri` 不匹配/非法 | 后台注册的回调地址与前端 `redirect_uri` 不一致 | 核对第 4 节回调地址，需完全一致 |
| 回调后显示「身份解析失败」+ `contact/users/me HTTP 403` + `requiredScopes:["Contact.User.Read"]` | 缺 `Contact.User.Read` 权限 | 后台开通该权限点（第 3、4 节） |
| 回调后显示「身份解析失败」+ `getbyunionid` errcode 60011 | 缺 `qyapi_get_member` 权限 | 后台开通该权限点 |
| 消息没发到单聊、回调显示「发送失败」 | userId 拿到了但 batchSend 失败 | 看 `taishan-api` 日志 `OAuth2 fulfill` 那行 |
| 成功页没弹回钉钉 | `dingtalk://` scheme 未生效 | 确认用的是 `dingtalk://dingtalkclient`（不带外链 url） |

查日志：

```bash
docker logs --tail 200 sales-agent-taishan-api 2>&1 | grep -iE "oauth2|users/me|getbyunionid|quick-entry"
```

---

## 11. 关键踩坑（血泪，别重蹈）

1. **`getbyunionid` 端点名容易拼错**。正确是 `POST oapi.dingtalk.com/topapi/user/getbyunionid`（无 `/v2/`）。曾试错的：`topapi/v2/user/getbyunionid`（多 `/v2/`→不合法ApiName）、`user/getbyunionid`（缺 `/topapi/`→404）、`getuseridbyunionid`（旧名→不合法ApiName）。**早期文档（anniu.md 弯路3）因此误判 OAuth2「死路」，其实是端点名错 + 缺权限，已纠正。**
2. **两个权限点是分开的**：用户级 token 的 `Contact.User.Read`、应用级 token 的 `qyapi_get_member`，各开各的。
3. **`contact/users/me` 403 一定要抛响应体**：`requiredScopes` 直接点名缺哪个权限，避免瞎猜。
4. **`dingtalk://` 弹回不能带外链 url**：带了会被钉钉甩到浏览器新开标签。用裸 `dingtalk://dingtalkclient`。
5. **`window.close()` 关不掉钉钉拉起的浏览器标签**：Chrome 安全限制，接受它，✅ 标签需手动关。
