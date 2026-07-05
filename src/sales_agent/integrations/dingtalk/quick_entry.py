"""钉钉快捷入口 — 独立模块，与核心消息路由解耦。

按照 docs/dingtalk/anniu.md 验证通过的方案实现：
  JSAPI requestAuthCode → topapi/v2/user/getuserinfo → batchSend

端点（tenant_id 进 path 段，供共享域名下 Traefik 按 /t/{tenant_id}/ 分流到各租户实例；
query 参数无法被 Traefik 路由，多租户必须靠 path 段区分）：
  GET  /integrations/dingtalk/t/{tenant_id}/quick              — H5 页面（视频+JSAPI 按钮）
  GET  /integrations/dingtalk/t/{tenant_id}/static/{filename}  — 静态资源（视频/图片/HTML）
  GET  /integrations/dingtalk/t/{tenant_id}/whoami             — authCode 换身份 + 发送引导消息
  POST /integrations/dingtalk/t/{tenant_id}/plugins/register   — 注册钉钉快捷入口按钮

删除此文件 + 移除 main.py 中的注册即可移除快捷入口功能，
不影响核心 DingTalk 单聊集成。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from html import escape as _html_escape
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from sales_agent.core.config import get_settings
from sales_agent.integrations.dingtalk.config import DingTalkConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/dingtalk", tags=["dingtalk-quick-entry"])

# 静态资源目录
_STATIC_DIR = Path(__file__).parent / "static"

# 钉钉单聊机器人快捷入口服务端 API（docs/dingtalk/anniu.md 验证通过的方案）
_DINGTALK_PLUGIN_SET_URL = "https://api.dingtalk.com/v1.0/robot/plugins/set"
_DINGTALK_PLUGIN_CLEAR_URL = "https://api.dingtalk.com/v1.0/robot/plugins/clear"
_DINGTALK_PLUGIN_QUERY_URL = "https://api.dingtalk.com/v1.0/robot/plugins/query"


def _get_dingtalk_config() -> DingTalkConfig:
    """获取钉钉配置。"""
    return get_settings().dingtalk


# ============================================================
# JSAPI 鉴权（dd.config）—— PC 端必需
# 背景：PC 钉钉调用任何 dd JSAPI（含 requestAuthCode）前必须先 dd.config 鉴权，
# 否则回调不触发、页面卡在 loading（本仓库的历史 bug）。移动端无需 dd.config，
# 但带上也不影响（官方双端通用写法）。jsapi_ticket 来自 oapi/get_jsapi_ticket。
# ============================================================

# jsapi_ticket 进程级缓存（taishan 单租户单应用，一个进程对应一份 ticket）
_jsapi_ticket_cache: dict[str, Any] = {"ticket": "", "expires_at": 0.0}
_jsapi_ticket_lock = asyncio.Lock()


def sign_jsapi(jsapi_ticket: str, noncestr: str, timestamp: str, url: str) -> str:
    """钉钉 JSAPI 签名（dd.config 用）。

    签名串固定为（键按字典序、``noncestr``/``timestamp`` 全小写）::

        jsapi_ticket={jsapi_ticket}&noncestr={noncestr}&timestamp={timestamp}&url={url}

    取 SHA1 的小写十六进制。

    Note:
        ``url`` 必须是当前网页完整 URL 且不含 ``#`` 及之后部分，需与前端
        ``dd.config`` 时所在页面 URL 严格一致（含 query string）。
    """
    # 用 "&".join 拼接，避免源码里出现 "&timestamp" 字面量被编辑器/实体解码
    # 误转成乘号 ×（历史坑：&times -> ×），导致签名串字节错误、dd.config 静默失败。
    raw = "&".join([
        f"jsapi_ticket={jsapi_ticket}",
        f"noncestr={noncestr}",
        f"timestamp={timestamp}",
        f"url={url}",
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def _get_jsapi_ticket(access_token: str) -> str:
    """获取（并内存缓存）钉钉 jsapi_ticket。

    jsapi_ticket 有效期 7200s，提前 5 分钟刷新；并发刷新用锁串行化。
    """
    async with _jsapi_ticket_lock:
        cached = _jsapi_ticket_cache
        if cached["ticket"] and time.time() < cached["expires_at"] - 300:
            return cached["ticket"]

        url = f"https://oapi.dingtalk.com/get_jsapi_ticket?access_token={access_token}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(f"get_jsapi_ticket HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("errcode") not in (0, None):
            raise RuntimeError(f"get_jsapi_ticket error: {data}")
        ticket = data.get("ticket", "")
        if not ticket:
            raise RuntimeError(f"no ticket in get_jsapi_ticket response: {data}")
        cached["ticket"] = ticket
        cached["expires_at"] = time.time() + int(data.get("expires_in", 7200))
        logger.info("DingTalk jsapi_ticket refreshed, expires_in=%ss", data.get("expires_in"))
        return ticket


# --- Action 配置 ---

_QUICK_ENTRY_ACTIONS: dict[str, dict[str, str]] = {
    "pre_visit_prepare": {
        "flow_id": "pre_visit_prepare",
        "task_type": "visit_preparation",
        "label": "访前准备",
        "subtitle": "1 分钟生成客户沟通作战卡",
        "message_icon": "📋",
    },
    "post_visit_review": {
        "flow_id": "post_visit_review",
        "task_type": "post_visit_review",
        "label": "访后复盘",
        "subtitle": "1 分钟判断客户状态和下一步动作",
        "message_icon": "📊",
    },
    "small_win_appreciation": {
        "flow_id": "small_win_appreciation",
        "task_type": "small_win_appreciation",
        "label": "小赢欣赏",
        "subtitle": "3 分钟，看见今天一个小进展",
        "message_icon": "🌟",
    },
    "sales_block_breakthrough": {
        "flow_id": "sales_block_breakthrough",
        "task_type": "sales_block_breakthrough",
        "label": "卡点破框",
        "subtitle": "3 问，拆掉一个销售卡点",
        "message_icon": "🔓",
    },
}


# ============================================================
# GET /integrations/dingtalk/t/{tenant_id}/static/{filename} — 静态资源
# ============================================================


@router.get("/t/{tenant_id}/static/{filename}")
async def serve_static(tenant_id: str, filename: str):
    """提供静态资源（视频、图片等）。"""
    # 安全检查：防止路径遍历
    safe_name = Path(filename).name
    file_path = _STATIC_DIR / safe_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

    content_type = _guess_content_type(safe_name)
    return FileResponse(file_path, media_type=content_type)


def _guess_content_type(filename: str) -> str:
    """根据扩展名猜测 MIME 类型。"""
    ext = Path(filename).suffix.lower()
    types = {
        ".html": "text/html; charset=utf-8",
        ".mp4": "video/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".css": "text/css",
        ".js": "application/javascript",
    }
    return types.get(ext, "application/octet-stream")


# ============================================================
# GET /integrations/dingtalk/t/{tenant_id}/quick — H5 页面
# ============================================================


@router.get("/t/{tenant_id}/quick", response_class=HTMLResponse)
async def dingtalk_quick_page(
    tenant_id: str,
    action: str = Query("all", description="pre_visit_prepare | post_visit_review | all | small_win_appreciation | sales_block_breakthrough"),
) -> HTMLResponse:
    """钉钉快捷入口 H5 页面。

    - 多轮状态机类入口（小赢欣赏 / 卡点破框）→ 渲染单按钮触发页 quick_trigger.html，
      点击「开始」→ JSAPI requestAuthCode → whoami 落库建会话 → 单聊里多轮追问。
    - 其它（含默认 all = 教练模式）→ 渲染 cocah.html 视频页（访前准备 / 访后复盘）。
    """
    config = _get_dingtalk_config()
    corp_id = config.corp_id or ""
    app_key = config.app_key or ""  # OAuth2 client_id（AppKey 非机密，可下发前端）

    action_cfg = _QUICK_ENTRY_ACTIONS.get(action)
    if action_cfg and action_cfg.get("session_type"):
        # 单按钮触发页
        html_path = _STATIC_DIR / "quick_trigger.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="Trigger template not found")
        html = html_path.read_text(encoding="utf-8")
        html = html.replace("__CORP_ID__", corp_id)
        html = html.replace("__APP_KEY__", app_key)
        html = html.replace("__ACTION__", action)
        html = html.replace("__LABEL__", action_cfg.get("label", action))
        html = html.replace("__SUBTITLE__", action_cfg.get("subtitle", ""))
        html = html.replace("__ICON__", action_cfg.get("message_icon", ""))
        return HTMLResponse(content=html)

    # 默认：教练模式视频页（访前准备 / 访后复盘）
    html_path = _STATIC_DIR / "cocah.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="H5 template not found")

    html = html_path.read_text(encoding="utf-8")
    html = html.replace("__CORP_ID__", corp_id)
    html = html.replace("__APP_KEY__", app_key)

    return HTMLResponse(content=html)


# ============================================================
# 快捷入口收尾逻辑：识别到 staffId userId 后，建会话(多轮类) 或 发引导消息(普通类)
# whoami (JSAPI 路径) 与 oauth2-callback (浏览器路径) 共用
# ============================================================


async def _fulfill_quick_action(
    sender: Any,
    dingtalk_user_id: str,
    action: str,
    action_config: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """识别到 staffId userId 后的统一收尾。

    通过 Online Graph 处理引导动作，不再使用 legacy ``quick_sessions``。
    抛出异常由各端点转成 HTTP/HTML 响应。
    """
    settings = get_settings()
    if not settings.guided_flows.enabled:
        await sender.send_text(dingtalk_user_id, "该引导功能暂时停用")
        return {"status": "ok", "action": action, "message": "该引导功能暂时停用"}

    import time as _time

    from sales_agent.core.database import get_session_factory
    from sales_agent.integrations.dingtalk.agent_resolver import resolve_dingtalk_agent_id
    from sales_agent.integrations.dingtalk.user_mapper import DingTalkUserMapper
    from sales_agent.services.online_conversation import invoke_online_turn

    config = _get_dingtalk_config()
    corp_id = config.corp_id or ""
    label = action_config.get("label", action)
    entry_action = action_config.get("task_type", action)

    factory = get_session_factory()
    async with factory() as session:
        # 1. Map DingTalk user to internal user ID
        user_mapper = DingTalkUserMapper(session, tenant_id)
        internal_user_id = await user_mapper.get_or_create_user(
            corp_id=corp_id,
            dingtalk_user_id=dingtalk_user_id,
            display_name="",
        )

        # 2. Resolve concrete Agent ID
        agent_id = await resolve_dingtalk_agent_id(session, tenant_id)

        # 3. Call invoke_online_turn with entry_action
        result = await invoke_online_turn(
            db=session,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=internal_user_id,
            session_user_id=dingtalk_user_id,
            channel="dingtalk",
            conversation_id=f"quick_{action}_{dingtalk_user_id}",
            message="",
            entry_action=entry_action,
            event_id=f"quick_{action}_{dingtalk_user_id}_{int(_time.time())}",
        )

        # 4. Send result["answer_dict"]["summary"]
        summary = result.get("answer_dict", {}).get("summary", "")
        await sender.send_text(dingtalk_user_id, f"{label}\n\n{summary}")

        # 5. Commit only message/log writes (no quick_sessions access)
        await session.commit()

    return {"status": "ok", "action": action, "message": f"{label}已发送到你的钉钉单聊。"}


# ============================================================
# GET /integrations/dingtalk/t/{tenant_id}/whoami — authCode 换身份 + 发消息
# ============================================================


@router.get("/t/{tenant_id}/whoami")
async def dingtalk_quick_whoami(
    tenant_id: str,
    code: str = Query(..., description="JSAPI requestAuthCode 返回的 authCode"),
    action: str = Query(..., description="pre_visit_prepare | post_visit_review"),
) -> dict[str, Any]:
    """钉钉快捷入口 — authCode 换取用户身份并发送引导消息。

    完整链路（docs/dingtalk/anniu.md 验证通过的方案）：
    1. authCode → topapi/v2/user/getuserinfo → userId (staffId 格式)
    2. userId → v1.0/robot/oToMessages/batchSend → 发送引导消息
    """
    config = _get_dingtalk_config()

    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    if not config.app_key or not config.app_secret or not config.robot_code:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")

    # 1. 校验 action
    action_config = _QUICK_ENTRY_ACTIONS.get(action)
    if action_config is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {action}. "
                   f"Must be one of: {', '.join(_QUICK_ENTRY_ACTIONS.keys())}",
        )

    # 2. 校验 tenant_id
    try:
        from sales_agent.core.tenant_runtime import get_tenant_runtime
        runtime = get_tenant_runtime()
        if tenant_id != runtime.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant mismatch")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")

    # 3. authCode → userId（topapi/v2/user/getuserinfo，返回 staffId 格式）
    from sales_agent.integrations.dingtalk.message_sender import DingTalkMessageSender

    sender = DingTalkMessageSender(config)
    try:
        user_info = await sender.get_user_info_by_auth_code(code)
    except Exception as e:
        logger.error("Failed to resolve DingTalk user from authCode: %s", e, exc_info=True)
        raise HTTPException(status_code=401, detail=f"User resolution failed: {e}")

    dingtalk_user_id = user_info.get("userid", "")
    if not dingtalk_user_id:
        raise HTTPException(status_code=401, detail="Could not resolve userId from authCode")

    # 4. 收尾：建会话(多轮类) 或 发引导消息(普通类)。JSAPI 与 OAuth2 路径共用。
    try:
        result = await _fulfill_quick_action(sender, dingtalk_user_id, action, action_config, tenant_id)
    except Exception as e:
        logger.error("Failed to fulfill quick-entry action=%s: %s", action, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Quick-entry failed: {e}")

    logger.info(
        "DingTalk quick-entry triggered: action=%s tenant=%s user=%s",
        action, tenant_id, dingtalk_user_id,
    )
    return result


# ============================================================
# GET /integrations/dingtalk/t/{tenant_id}/jsapi-config — 返回 dd.config 鉴权参数
# ============================================================


@router.get("/t/{tenant_id}/jsapi-config")
async def dingtalk_jsapi_config(
    tenant_id: str,
    url: str = Query(..., description="当前网页完整 URL（不含 # 及之后部分），前端用 encodeURIComponent 编码"),
) -> dict[str, Any]:
    """返回 PC 端 ``dd.config`` 所需的鉴权参数。

    PC 钉钉调用任何 dd JSAPI（含 ``requestAuthCode``）前必须先 ``dd.config`` 鉴权，
    否则回调不触发、H5 卡死在 loading。移动端 ``requestAuthCode`` 无需 ``dd.config``，
    但带上 dd.config 也不影响（官方双端通用写法），故两个页面统一先取本端点再 dd.config。

    返回 ``{agentId, corpId, timeStamp, nonceStr, signature}``，前端直接喂给 ``dd.config``。
    需要先配置 ``DINGTALK_AGENT_ID``，并在钉钉开发者后台把 H5 域名加入应用服务器域名。
    """
    config = _get_dingtalk_config()
    if not config.app_key or not config.app_secret:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")
    if not config.agent_id:
        raise HTTPException(
            status_code=503,
            detail="DINGTALK_AGENT_ID not configured (required for PC dd.config auth)",
        )

    access_token = await _get_access_token(config)
    ticket = await _get_jsapi_ticket(access_token)

    noncestr = secrets.token_hex(8)
    timestamp = str(int(time.time()))
    signature = sign_jsapi(ticket, noncestr, timestamp, url)

    logger.info("DingTalk jsapi-config issued (agent=%s url=%s)", config.agent_id, url)
    return {
        "agentId": config.agent_id,
        "corpId": config.corp_id,
        "timeStamp": timestamp,
        "nonceStr": noncestr,
        "signature": signature,
    }


# ============================================================
# GET /integrations/dingtalk/t/{tenant_id}/oauth2-callback — OAuth2 网页登录回调（PC 浏览器场景）
# PC 钉钉点机器人快捷入口默认跳系统浏览器，那里没有 JSAPI 桥（notInDingTalk），
# 只能走 OAuth2 扫码免登识别用户。前端 UA 检测到非钉钉容器时跳 login.dingtalk.com，
# 用户授权后回跳到本端点，完成 authCode→userId→发消息。
# ============================================================


def _parse_oauth_action(state: str) -> str:
    """解析 OAuth2 state，返回 action。

    tenant_id 已在 URL path 段（``/t/{tenant_id}/oauth2-callback``）携带，state 只需传 action。
    兼容旧的 ``action:tenant_id`` 格式（取冒号前）。
    """
    action, _sep, _tenant_id = state.partition(":")
    return action


def _oauth_result_page(ok: bool, message: str) -> HTMLResponse:
    """OAuth2 回调结果页。

    成功：不停留——极简 ✅ 后自动 ``window.close()`` + 跳 ``dingtalk://`` 弹回钉钉。
    失败：显示错误信息（供排查，不自动关闭）。
    """
    if ok:
        return _oauth_success_page(message)

    color = "#ff4d4f"
    template = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        "<title>快捷入口</title><style>"
        "*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}"
        "html,body{height:100%;background:linear-gradient(160deg,#1a1a2e 0%,#16213e 100%);"
        'color:#fff;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}'
        ".wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;text-align:center;padding:28px}"
        ".box{font-size:64px;margin-bottom:18px}"
        ".msg{font-size:17px;line-height:1.7;color:__COLOR__;max-width:420px;word-break:break-word}"
        ".hint{margin-top:22px;font-size:13px;color:#6b7898}"
        '</style></head><body><div class="wrap">'
        '<div class="box">❌</div><div class="msg">__MESSAGE__</div>'
        '<div class="hint">此页面可以关闭</div>'
        "</div></body></html>"
    )
    html = template.replace("__COLOR__", color).replace("__MESSAGE__", _html_escape(message))
    return HTMLResponse(content=html)


def _oauth_success_page(message: str) -> HTMLResponse:
    """登录+发送成功页：极简 ✅，加载即尝试关闭浏览器页并弹回钉钉客户端。

    - ``window.close()``：浏览器只允许关闭"脚本打开"的标签页，钉钉拉起的 Chrome
      标签通常关不掉，所以只作兜底（部分环境/历史栈为空时可关）。
    - ``dingtalk://`` AppLink：可靠地唤起/置前钉钉客户端（Chrome 会先弹"打开钉钉?"确认）。
    - 手动链接兜底：自动跳转被拦截时用户可点。
    """
    template = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        "<title>已发送</title><style>"
        "*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}"
        "html,body{height:100%;background:linear-gradient(160deg,#1a1a2e 0%,#16213e 100%);"
        'color:#fff;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}'
        ".wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;text-align:center;padding:28px}"
        ".box{font-size:60px;margin-bottom:14px}"
        ".msg{font-size:17px;color:#52c41a;max-width:420px}"
        ".hint{margin-top:12px;font-size:13px;color:#6b7898}"
        "a.link{margin-top:18px;font-size:14px;color:#5b8cff}"
        '</style></head><body onload="goback()"><div class="wrap">'
        '<div class="box">✅</div><div class="msg">__MESSAGE__</div>'
        '<div class="hint">正在返回钉钉…</div>'
        '<a class="link" id="backlink" href="#">若未自动返回，点此打开钉钉</a>'
        "</div><script>"
        "function goback(){"
        "var dl='dingtalk://dingtalkclient';"
        "var a=document.getElementById('backlink');if(a)a.href=dl;"
        "try{window.opener=null;window.open('','_self');window.close();}catch(e){}"
        "setTimeout(function(){try{window.location.href=dl;}catch(e){}},300);"
        "}"
        "</script></body></html>"
    )
    return HTMLResponse(content=template.replace("__MESSAGE__", _html_escape(message)))


@router.get("/t/{tenant_id}/oauth2-callback", response_class=HTMLResponse)
async def dingtalk_oauth2_callback(
    tenant_id: str,
    authCode: str = Query("", description="OAuth2 回调授权码（login.dingtalk.com 回传）"),
    code: str = Query("", description="个别版本回调用 code"),
    state: str = Query("", description="action（tenant_id 在 path 段）"),
) -> HTMLResponse:
    """OAuth2 网页登录回调（PC 浏览器场景）。

    链路：authCode → userAccessToken → unionId → staffId userId（getbyunionid，需 qyapi_get_member）
         → 复用 _fulfill_quick_action 发消息/建会话。

    前置：钉钉后台「登录配置」已把本回调地址注册为重定向域名，且应用已开通 qyapi_get_member。
    """
    config = _get_dingtalk_config()
    auth_code = authCode or code
    if not auth_code:
        return _oauth_result_page(False, "缺少授权码（authCode），请重新从钉钉入口进入。")

    action = _parse_oauth_action(state)
    if not action:
        return _oauth_result_page(False, f"参数解析失败（state={state or '空'}）。")

    action_config = _QUICK_ENTRY_ACTIONS.get(action)
    if action_config is None:
        return _oauth_result_page(False, f"未知的入口类型：{action}")

    # 租户校验
    try:
        from sales_agent.core.tenant_runtime import get_tenant_runtime
        runtime = get_tenant_runtime()
        if tenant_id != runtime.tenant_id:
            return _oauth_result_page(False, "租户不匹配。")
    except Exception:
        return _oauth_result_page(False, f"租户不存在：{tenant_id}")

    from sales_agent.integrations.dingtalk.message_sender import DingTalkMessageSender
    sender = DingTalkMessageSender(config)

    # authCode → staffId userId（OAuth2 三步）
    try:
        user_id = await sender.get_userid_by_oauth2_code(auth_code)
    except Exception as e:
        logger.error("OAuth2 resolve userId failed: %s", e, exc_info=True)
        return _oauth_result_page(False, f"身份解析失败：{e}")

    # 收尾：建会话(多轮类) 或 发引导消息(普通类)
    try:
        await _fulfill_quick_action(sender, user_id, action, action_config, tenant_id)
    except Exception as e:
        logger.error("OAuth2 fulfill failed action=%s: %s", action, e, exc_info=True)
        return _oauth_result_page(False, f"发送失败：{e}")

    logger.info(
        "DingTalk quick-entry (OAuth2) triggered: action=%s tenant=%s user=%s",
        action, tenant_id, user_id,
    )
    return _oauth_result_page(True, f"{action_config.get('label', action)}已发送到你的钉钉单聊，请查收。")


# ============================================================
# 钉钉快捷入口管理（query / clear / register）
# 参考 docs/dingtalk/anniu.md：POST /v1.0/robot/plugins/set 为全量覆盖；
# /v1.0/robot/plugins/clear 清空全部；/v1.0/robot/plugins/query 查询。
# ============================================================


async def _get_access_token(config: DingTalkConfig) -> str:
    """用 AppKey/AppSecret 换取 access_token。"""
    from sales_agent.integrations.dingtalk.message_sender import DingTalkAccessTokenManager

    token_manager = DingTalkAccessTokenManager(config.app_key, config.app_secret)
    try:
        return await token_manager.get_access_token()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get access token: {e}")


async def _query_plugins(access_token: str, robot_code: str) -> Any:
    """查询当前机器人的全部快捷入口（POST /v1.0/robot/plugins/query）。"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _DINGTALK_PLUGIN_QUERY_URL,
            json={"robotCode": robot_code},
            headers={"x-acs-dingtalk-access-token": access_token},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"DingTalk plugins/query failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _clear_plugins(access_token: str, robot_code: str) -> Any:
    """清空机器人全部快捷入口（POST /v1.0/robot/plugins/clear）。"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _DINGTALK_PLUGIN_CLEAR_URL,
            json={"robotCode": robot_code},
            headers={"x-acs-dingtalk-access-token": access_token},
        )
    if resp.status_code >= 400:
        logger.error("DingTalk plugins/clear failed: status=%d body=%s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"DingTalk plugins/clear failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _set_plugins(access_token: str, robot_code: str, plugin_info_list: list[dict]) -> Any:
    """全量设置机器人快捷入口（POST /v1.0/robot/plugins/set）。

    pluginInfoList 为全量覆盖：传入什么，最终就是什么。
    """
    payload = {"robotCode": robot_code, "pluginInfoList": plugin_info_list}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _DINGTALK_PLUGIN_SET_URL,
            json=payload,
            headers={"x-acs-dingtalk-access-token": access_token},
        )
    if resp.status_code >= 400:
        logger.error("DingTalk plugins/set failed: status=%d body=%s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"DingTalk plugins/set failed: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.text else {}


async def _upload_quick_entry_icon(access_token: str) -> str:
    """上传快捷入口图标并返回 media_id。"""
    icon_path = _STATIC_DIR / "coach_mode.png"
    if not icon_path.exists():
        raise HTTPException(status_code=500, detail="coach_mode.png not found in static/")

    icon_media_id = await _upload_media(access_token, icon_path)
    logger.info("Uploaded coach_mode.png to DingTalk, media_id: %s", icon_media_id)
    return icon_media_id


def _build_quick_entry_plugin_info(
    public_url: str,
    tenant_id: str,
    icon_media_id: str,
    action: str | None,
    name: str,
) -> tuple[dict[str, str], str]:
    """构建单个钉钉快捷入口 pluginInfo。

    URL 形如 ``{public_url}/integrations/dingtalk/t/{tenant_id}/quick[?action=...]``：
    tenant_id 进 path 段，供共享域名下 Traefik 按 ``PathPrefix(`/t/{tenant_id}/`)``
    分流到各租户实例（query 参数无法被 Traefik 路由，多租户必须靠 path 段区分）；
    action（教练模式为空）走 query。
    """
    base_url = f"{public_url}/integrations/dingtalk/t/{tenant_id}/quick"
    quick_url = f"{base_url}?action={action}" if action else base_url
    plugin_info = {
        "name": json.dumps({"zh_CN": name}, ensure_ascii=False),
        "icon": icon_media_id,
        "pcUrl": quick_url,
        "mobileUrl": quick_url,
    }
    return plugin_info, quick_url


@router.post("/t/{tenant_id}/plugins/register")
async def register_quick_entry_plugin(
    tenant_id: str,
    clear_first: bool = Query(
        True, description="True=先清空全部已有快捷入口再注册（默认）；False=直接全量覆盖",
    ),
    name: str = Query("教练模式", description="默认教练入口名称"),
    entries: str = Query(
        "coach,small_win_appreciation,sales_block_breakthrough",
        description="逗号分隔：coach,small_win_appreciation,sales_block_breakthrough",
    ),
) -> dict[str, Any]:
    """注册钉钉快捷入口按钮。

    默认注册完整集合：
    - 教练模式：打开 cocah.html 页面，含访前准备 / 访后复盘。
    - 小赢欣赏：打开 quick_trigger.html，触发多轮状态机。
    - 卡点破框：打开 quick_trigger.html，触发多轮状态机。

    最终通过钉钉官方 POST /v1.0/robot/plugins/set 全量设置。
    """
    config = _get_dingtalk_config()

    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    if not config.robot_code or not config.app_key or not config.app_secret:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")

    if not config.public_url:
        raise HTTPException(
            status_code=500,
            detail="public_url not configured. Set DINGTALK_PUBLIC_URL env var.",
        )

    try:
        from sales_agent.core.tenant_runtime import get_tenant_runtime
        runtime = get_tenant_runtime()
        if tenant_id != runtime.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant mismatch")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")

    requested = [item.strip() for item in entries.split(",") if item.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="entries must not be empty")

    entry_specs: list[tuple[str | None, str]] = []
    for item in requested:
        if item == "coach":
            entry_specs.append((None, name))
            continue
        action_cfg = _QUICK_ENTRY_ACTIONS.get(item)
        if action_cfg is None:
            raise HTTPException(status_code=400, detail=f"Invalid quick entry: {item}")
        entry_specs.append((item, action_cfg.get("label", item)))

    public_url = config.public_url.rstrip("/")
    access_token = await _get_access_token(config)

    cleared: Any = None
    if clear_first:
        cleared = await _clear_plugins(access_token, config.robot_code)
        logger.info("DingTalk plugins cleared before register: %s", cleared)

    icon_media_id = await _upload_quick_entry_icon(access_token)
    plugin_info_list: list[dict[str, str]] = []
    plugin_urls: list[dict[str, str]] = []
    for action, plugin_name in entry_specs:
        plugin_info, quick_url = _build_quick_entry_plugin_info(
            public_url=public_url,
            tenant_id=tenant_id,
            icon_media_id=icon_media_id,
            action=action,
            name=plugin_name,
        )
        plugin_info_list.append(plugin_info)
        plugin_urls.append({"name": plugin_name, "action": action or "coach", "url": quick_url})

    result = await _set_plugins(access_token, config.robot_code, plugin_info_list)
    logger.info("DingTalk plugins registered successfully: %s", result)

    return {
        "status": "ok",
        "cleared": cleared,
        "plugins": plugin_urls,
        "dingtalk_response": result,
    }


@router.post("/t/{tenant_id}/plugins/clear")
async def clear_quick_entry_plugins(tenant_id: str) -> dict[str, Any]:
    """清空机器人全部快捷入口（POST /v1.0/robot/plugins/clear）。"""
    config = _get_dingtalk_config()

    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    if not config.robot_code or not config.app_key or not config.app_secret:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")

    access_token = await _get_access_token(config)
    result = await _clear_plugins(access_token, config.robot_code)
    logger.info("DingTalk plugins cleared: %s", result)

    return {"status": "ok", "dingtalk_response": result}


@router.get("/t/{tenant_id}/plugins/query")
async def query_quick_entry_plugins(tenant_id: str) -> dict[str, Any]:
    """查询机器人当前全部快捷入口。"""
    config = _get_dingtalk_config()

    if not config.enabled:
        raise HTTPException(status_code=503, detail="DingTalk integration not enabled")

    if not config.robot_code or not config.app_key or not config.app_secret:
        raise HTTPException(status_code=503, detail="DingTalk credentials not configured")

    access_token = await _get_access_token(config)
    result = await _query_plugins(access_token, config.robot_code)

    return {"status": "ok", "robot_code": config.robot_code, "dingtalk_response": result}


async def _upload_media(access_token: str, file_path: Path) -> str:
    """上传图片到钉钉媒体服务器，返回 media_id。

    使用 oapi/media/upload 接口。
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": access_token, "type": "image"},
                files={"media": (file_path.name, f, "image/png")},
            )

    if resp.status_code >= 400:
        raise RuntimeError(f"DingTalk media upload failed: {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"DingTalk media upload error: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")

    media_id = data.get("media_id", "")
    if not media_id:
        raise RuntimeError(f"No media_id in upload response: {data}")

    return media_id
