  一、整体架构

  钉钉客户端 (内置浏览器)
      ↕ HTTPS (aijiaolian.com.cn)
  Traefik 反向代理
      ↕
  FastAPI (api/routers/dingtalk_cool_app.py)
      ↕ Redis Stream
  Worker (worker/main.py)
      ↕
  工作流引擎 (engine.py → 节点执行)
      ↕ DingTalk API
  回复给用户 (streaming card / 单聊文本)

  二、快捷入口是怎么挂上去的

  快捷入口不是酷应用安装后自动出现的，而是通过钉钉 API 单独设置的：

  POST /v1.0/robot/plugins/set
  {
    "robotCode": "dingzdqpllocwfuab27m",
    "pluginInfoList": [{
      "name": "{\"zh_CN\": \"AI助手\"}",
      "icon": "@lALPM3DGuznknlcwMA",
      "pcUrl": "https://aijiaolian.com.cn/api/dingtalk/quick?action=coach_mode&tenant_id=xxx",
      "mobileUrl": "https://aijiaolian.com.cn/api/dingtalk/quick?action=coach_mode&tenant_id=xxx"
    }]
  }

  这会在钉钉聊天输入框上方创建一个按钮。用户点击后，钉钉在内置浏览器中打开那个 URL。

  三、带视频的 H5 页面是怎么挂上去的

  关键函数是 _prepare_cocah_html() (dingtalk_cool_app.py:241-304)：

  1. 读取原始模板 web/app/dingtalk/install/cocah.html — 一个纯静态页面，包含：
    - 一个 <video> 标签播放 cocah1.mp4，自动循环播放
    - 两个透明热区按钮 .slice-1（视频 1050-1450px 位置）和 .slice-2（1450-1800px 位置）
  2. 后端动态注入 JSAPI 和交互逻辑：
    - 注入 <script src="dingtalk.open.js"> 加载钉钉 JSAPI
    - 将视频路径 cocah1.mp4 改为 /api/dingtalk/static/cocah1.mp4（走后端静态文件端点）
    - 注入 overlay 样式（加载中/成功/失败的遮罩层）
    - 移除原始 <script> 块（正则替换掉）
    - 注入新的 <script> 包含完整认证+推送逻辑
  3. 视频文件通过 /api/dingtalk/static/cocah1.mp4 (dingtalk_cool_app.py:417-437) 由 FastAPI FileResponse 提供服务

  四、用户点击后事件流（完整时间线）

  T0: 用户在钉钉聊天中点击"AI助手"快捷入口按钮
       ↓ 钉钉客户端在内置浏览器中打开
       ↓ https://aijiaolian.com.cn/api/dingtalk/quick?action=coach_mode&tenant_id=xxx

  T1: GET /api/dingtalk/quick  → quick_entry() [line 507]
       ↓ 从数据库查租户配置 (corp_id, client_id)
       ↓ action == "coach_mode" → 调用 _prepare_cocah_html()
       ↓ 注入 JSAPI + 认证逻辑 + overlay
       ↓ 返回完整 HTML 页面

  T2: 浏览器渲染页面
       ↓ 加载 dingtalk-jsapi CDN
       ↓ 播放 cocah1.mp4 视频
       ↓ 显示两个热区按钮（叠在视频上）

  T3: 用户点击"访前准备"(slice-1) 或 "访后复盘"(slice-2)
       ↓ JS 调用 doAct(action)
       ↓ action: slice-1 → "coach_pre_visit", slice-2 → "coach_post_review"

  T4: JSAPI 免登
       ↓ dd.ready() → dd.runtime.permission.requestAuthCode({corpId: CORP_ID})
       ↓ 钉钉客户端返回 authCode（注意：这里用的是 JSAPI requestAuthCode，不是 OAuth2）
       ↓ 前提：钉钉开放平台配置了"端内免登地址"为 https://aijiaolian.com.cn

  T5: 前端调用后端
       ↓ fetch("/api/dingtalk/whoami?code=AUTH_CODE&action=coach_pre_visit&tenant_id=xxx")
       ↓ 显示 loading overlay

  T6: GET /api/dingtalk/whoami → whoami() [line 593]
       ↓ DingTalkClient.exchange_auth_code(code)
       ↓   → POST topapi/v2/user/getuserinfo (用 AppKey/AppSecret 换 access_token 再换 userid)
       ↓   → 返回 {userid: "022045516146-1529960953", name: "张三"}

  T7: 推送消息到 Redis Stream
       ↓ _push_quick_entry_to_redis(tenant_id, user_id, user_name, action) [line 185]
       ↓ 构造 InternalMessage 格式的 dict:
       ↓   {
       ↓     "channel": "dingtalk",
       ↓     "msg_type": "card_callback",
       ↓     "content": {"action": "coach_pre_visit"},
       ↓     "sender_id": "022045516146-1529960953",
       ↓     "agent_id": "pre-visit-prep",  (仅 pre_visit 动作设置)
       ↓     "chat_type": "single"
       ↓   }
       ↓ XADD 到 Redis Stream: tenant:{tenant_id}:msg:incoming

  T8: 返回前端 {ok: true}
       ↓ 前端显示 ✅ 成功
       ↓ 调用 dd.biz.navigation.close() 关闭 H5 页面

  T9: Worker 消费 Redis 消息
       ↓ Worker 主循环 (worker/main.py) 用 XREADGROUP 消费 stream
       ↓ 解析为 InternalMessage
       ↓ 进入 _execute_workflow_and_stream()

  T10: 路由决策 (worker/main.py:295-344)
       ↓ msg_type == "card_callback" → 解析 content.action
       ↓ action in {"pre_visit_prep", "coach_pre_visit"} → 设置 msg.agent_id = _PRIMARY_AGENT_ID
       ↓ action == "coach_post_review" → 同样路由到 primary workflow
       ↓ 这两个 action 都走到 **主工作流**（unified graph 含 pre_visit + post_visit 分支）

  T11: 工作流执行
       ↓ 创建 streaming card (DingTalk 互动卡片)
       ↓ 启动 thinking animation (心跳动画)
       ↓ WorkflowEngine 执行节点图
       ↓ on_token 回调流式更新卡片内容
       ↓ 节点执行完成后更新 WorkflowRun 记录

  T12: 回复推送到钉钉
       ↓ DingTalkEgress 通过钉钉 API 更新 streaming card
       ↓ 用户在钉钉聊天中看到 AI 教练的回复（作战卡/复盘建议等）

  五、关键文件索引

  ┌──────────────────────────────────────────┬────────────────────────────────────────────────┐
  │                   文件                   │                      职责                      │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/routers/dingtalk_cool_app.py:507     │ /quick 端点，渲染 H5 页面                      │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/routers/dingtalk_cool_app.py:241     │ _prepare_cocah_html() 注入视频+JSAPI           │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/routers/dingtalk_cool_app.py:417     │ /static/{filename} 提供视频文件                │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/routers/dingtalk_cool_app.py:593     │ /whoami 端点，authCode→userId→Redis            │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/routers/dingtalk_cool_app.py:185     │ _push_quick_entry_to_redis() 推入 Redis        │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ web/app/dingtalk/install/cocah.html      │ 视频热区 H5 模板                               │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ api/core/channels/dingtalk/sdk/client.py │ exchange_auth_code() / send_single_chat_text() │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ worker/main.py:250                       │ _route_message() agent_id 路由                 │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ worker/main.py:309                       │ coach_pre_visit/card_callback 路由拦截         │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ worker/main.py:327                       │ coach_post_review 路由拦截                     │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ worker/processor.py                      │ process_message() 工作流执行                   │
  ├──────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ worker/queue.py                          │ RedisStreamQueue Redis Stream 封装             │
  └──────────────────────────────────────────┴────────────────────────────────────────────────┘

  六、为什么用 JSAPI requestAuthCode 而不是 OAuth2

  文档里记录了踩过的坑（docs/dingtalk-cool-app-install.md:73-89）：OAuth2 换回来的是 openId/unionId，而 v1.0/robot/oToMessages/batchSend 只接受旧版 senderStaffId 格式的 userId。钉钉已经废弃了 unionId→userId
  的转换接口，所以 OAuth2 这条路彻底不通。

  JSAPI requestAuthCode 直接返回的就是旧版 userid，一步到位。
