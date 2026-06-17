"""钉钉快捷入口 — 独立模块，与核心消息路由解耦。

按照 anniu.md 验证通过的方案实现：
  JSAPI requestAuthCode → topapi/v2/user/getuserinfo → batchSend

端点：
  GET  /integrations/dingtalk/quick              — H5 页面（视频+JSAPI 按钮）
  GET  /integrations/dingtalk/static/{filename}  — 静态资源（视频/图片/HTML）
  GET  /integrations/dingtalk/whoami             — authCode 换身份 + 发送引导消息
  POST /integrations/dingtalk/plugins/register   — 注册钉钉快捷入口按钮

删除此文件 + 移除 main.py 中的注册即可移除快捷入口功能，
不影响核心 DingTalk 单聊集成。
"""

from __future__ import annotations

import json
import logging
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

# 钉钉单聊机器人快捷入口服务端 API（anniu.md 验证通过的方案）
_DINGTALK_PLUGIN_SET_URL = "https://api.dingtalk.com/v1.0/robot/plugins/set"
_DINGTALK_PLUGIN_CLEAR_URL = "https://api.dingtalk.com/v1.0/robot/plugins/clear"
_DINGTALK_PLUGIN_QUERY_URL = "https://api.dingtalk.com/v1.0/robot/plugins/query"


def _get_dingtalk_config() -> DingTalkConfig:
    """获取钉钉配置。"""
    return get_settings().dingtalk


# --- Action 配置 ---

_QUICK_ENTRY_ACTIONS: dict[str, dict[str, str]] = {
    "pre_visit_prepare": {
        "task_type": "visit_preparation",
        "label": "访前准备",
        "subtitle": "1 分钟生成客户沟通作战卡",
        "questions": (
            "请告诉我以下信息，我帮你生成访前作战卡：\n"
            "1. 你要见谁？\n"
            "2. 客户现在大概什么情况？\n"
            "3. 这次你最想推进到哪一步？"
        ),
        "message_icon": "📋",
    },
    "post_visit_review": {
        "task_type": "post_visit_review",
        "label": "访后复盘",
        "subtitle": "1 分钟判断客户状态和下一步动作",
        "questions": (
            "请告诉我以下信息，我帮你生成访后机会推进卡：\n"
            "1. 刚才客户主要说了什么？\n"
            "2. 客户现在是什么态度？\n"
            "3. 你们有没有约定下一步？"
        ),
        "message_icon": "📊",
    },
    # 多轮状态机类快捷入口：点击后在单聊里做多轮追问直到出卡，
    # 会话状态落库（quick_sessions），由 streaming_handler 顶部推进。
    "small_win_appreciation": {
        "task_type": "small_win_appreciation",
        "session_type": "small_win_appreciation",
        "label": "小赢欣赏",
        "subtitle": "3 分钟，看见今天一个小进展",
        "questions": "",
        "message_icon": "🌟",
    },
    "sales_block_breakthrough": {
        "task_type": "sales_block_breakthrough",
        "session_type": "sales_block_breakthrough",
        "label": "卡点破框",
        "subtitle": "3 问，拆掉一个销售卡点",
        "questions": "",
        "message_icon": "🔓",
    },
}


# ============================================================
# GET /integrations/dingtalk/static/{filename} — 静态资源
# ============================================================


@router.get("/static/{filename}")
async def serve_static(filename: str):
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
# GET /integrations/dingtalk/quick — H5 页面
# ============================================================


@router.get("/quick", response_class=HTMLResponse)
async def dingtalk_quick_page(
    action: str = Query("all", description="pre_visit_prepare | post_visit_review | all | small_win_appreciation | sales_block_breakthrough"),
    tenant_id: str = Query("", description="租户 ID"),
) -> HTMLResponse:
    """钉钉快捷入口 H5 页面。

    - 多轮状态机类入口（小赢欣赏 / 卡点破框）→ 渲染单按钮触发页 quick_trigger.html，
      点击「开始」→ JSAPI requestAuthCode → whoami 落库建会话 → 单聊里多轮追问。
    - 其它（含默认 all = 教练模式）→ 渲染 cocah.html 视频页（访前准备 / 访后复盘）。
    """
    config = _get_dingtalk_config()
    corp_id = config.corp_id or ""

    action_cfg = _QUICK_ENTRY_ACTIONS.get(action)
    if action_cfg and action_cfg.get("session_type"):
        # 单按钮触发页
        html_path = _STATIC_DIR / "quick_trigger.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="Trigger template not found")
        html = html_path.read_text(encoding="utf-8")
        html = html.replace("__CORP_ID__", corp_id)
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

    return HTMLResponse(content=html)


# ============================================================
# GET /integrations/dingtalk/whoami — authCode 换身份 + 发消息
# ============================================================


@router.get("/whoami")
async def dingtalk_quick_whoami(
    code: str = Query(..., description="JSAPI requestAuthCode 返回的 authCode"),
    action: str = Query(..., description="pre_visit_prepare | post_visit_review"),
    tenant_id: str = Query(..., description="租户 ID"),
) -> dict[str, Any]:
    """钉钉快捷入口 — authCode 换取用户身份并发送引导消息。

    完整链路（anniu.md 验证通过的方案）：
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

    # 4. 多轮状态机类入口（小赢欣赏 / 卡点破框）：落库建会话 + 发首轮提问，
    #    后续回复由 streaming_handler 顶部推进。普通入口仍发静态引导消息。
    session_type = action_config.get("session_type")
    if session_type:
        from sales_agent.coach.quick_session import start_session, label_of
        from sales_agent.core.database import get_session_factory

        factory = get_session_factory()
        try:
            async with factory() as session:
                first_reply = await start_session(
                    session,
                    tenant_id=tenant_id,
                    external_user_id=dingtalk_user_id,
                    session_type=session_type,
                )
                await session.commit()
        except Exception as e:
            logger.error("Failed to start quick session: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Session start failed: {e}")

        title = label_of(session_type)
        try:
            await sender.send_markdown(dingtalk_user_id, title, first_reply)
        except Exception as e:
            logger.error("Failed to send quick-entry first reply: %s", e, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Message send failed: {e}")

        logger.info(
            "DingTalk quick-entry session started: action=%s tenant=%s user=%s",
            action, tenant_id, dingtalk_user_id,
        )
        return {
            "status": "ok",
            "action": action,
            "message": f"{title}已开始，请在单聊里直接回复。",
        }

    # 4b. 普通入口：发送静态引导消息到用户钉钉单聊
    label = action_config["label"]
    icon = action_config["message_icon"]
    questions = action_config["questions"]
    message_text = f"{icon} {label}\n\n{questions}"

    try:
        await sender.send_text(dingtalk_user_id, message_text)
    except Exception as e:
        logger.error("Failed to send quick-entry message: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Message send failed: {e}")

    logger.info(
        "DingTalk quick-entry triggered: action=%s tenant=%s user=%s",
        action, tenant_id, dingtalk_user_id,
    )

    # 返回简单成功页面
    return {
        "status": "ok",
        "action": action,
        "message": f"{label}引导消息已发送到你的钉钉单聊。",
    }


# ============================================================
# 钉钉快捷入口管理（query / clear / register）
# 参考 anniu.md：POST /v1.0/robot/plugins/set 为全量覆盖；
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
    """构建单个钉钉快捷入口 pluginInfo。"""
    base_url = f"{public_url}/integrations/dingtalk/quick"
    quick_url = (
        f"{base_url}?action={action}&tenant_id={tenant_id}"
        if action else f"{base_url}?tenant_id={tenant_id}"
    )
    plugin_info = {
        "name": json.dumps({"zh_CN": name}, ensure_ascii=False),
        "icon": icon_media_id,
        "pcUrl": quick_url,
        "mobileUrl": quick_url,
    }
    return plugin_info, quick_url


@router.post("/plugins/register")
async def register_quick_entry_plugin(
    tenant_id: str = Query(..., description="租户 ID"),
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


@router.post("/plugins/clear")
async def clear_quick_entry_plugins() -> dict[str, Any]:
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


@router.get("/plugins/query")
async def query_quick_entry_plugins() -> dict[str, Any]:
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
