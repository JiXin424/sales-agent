"""钉钉消息发送 — 调用钉钉 API 发送单聊消息。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from sales_agent.integrations.dingtalk.config import DingTalkConfig

logger = logging.getLogger(__name__)

# 钉钉 API 端点
DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_SEND_SINGLE_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"

# 重试配置
RETRY_DELAYS = [1.0, 3.0, 10.0]
MAX_RETRIES = 3


class DingTalkAccessTokenManager:
    """钉钉 Access Token 管理器（内存缓存 + 懒刷新）。"""

    def __init__(self, app_key: str, app_secret: str):
        self._app_key = app_key
        self._app_secret = app_secret
        self._token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_access_token(self) -> str:
        """获取 access token，过期时自动刷新。"""
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token

            # 刷新 token
            payload = {
                "appKey": self._app_key,
                "appSecret": self._app_secret,
            }
            try:
                resp = await self._client.post(DINGTALK_TOKEN_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                self._token = data["accessToken"]
                expire_in = data.get("expireIn", 7200)
                self._expires_at = time.time() + expire_in
                logger.debug("DingTalk access token refreshed, expires in %ds", expire_in)
                return self._token
            except Exception as e:
                logger.error("Failed to refresh DingTalk access token: %s", e)
                raise

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()


class DingTalkMessageSender:
    """钉钉消息发送器。"""

    def __init__(self, config: DingTalkConfig):
        self._config = config
        self._token_manager = DingTalkAccessTokenManager(config.app_key, config.app_secret)
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get_token(self) -> str:
        """获取 access token。"""
        return await self._token_manager.get_access_token()

    async def send_text(self, dingtalk_user_id: str, text: str) -> dict[str, Any]:
        """发送文本消息到钉钉用户。

        Args:
            dingtalk_user_id: 钉钉用户 ID
            text: 消息文本

        Returns:
            发送结果 dict
        """
        msg_param = json.dumps({"content": text}, ensure_ascii=False)
        payload = {
            "robotCode": self._config.robot_code,
            "userIds": [dingtalk_user_id],
            "msgKey": "sampleText",
            "msgParam": msg_param,
        }
        return await self._send_with_retry(payload)

    async def send_markdown(
        self, dingtalk_user_id: str, title: str, text: str,
    ) -> dict[str, Any]:
        """发送 Markdown 消息到钉钉用户。

        Args:
            dingtalk_user_id: 钉钉用户 ID
            title: 消息标题
            text: Markdown 内容

        Returns:
            发送结果 dict
        """
        msg_param = json.dumps({"title": title, "text": text}, ensure_ascii=False)
        payload = {
            "robotCode": self._config.robot_code,
            "userIds": [dingtalk_user_id],
            "msgKey": "sampleMarkdown",
            "msgParam": msg_param,
        }
        return await self._send_with_retry(payload)

    async def _send_with_retry(self, payload: dict) -> dict[str, Any]:
        """带重试的消息发送。"""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                token = await self._get_token()
                headers = {"x-acs-dingtalk-access-token": token}
                resp = await self._client.post(
                    DINGTALK_SEND_SINGLE_URL,
                    json=payload,
                    headers=headers,
                )

                if resp.status_code == 401 or resp.status_code == 403:
                    # 认证/权限失败，不重试
                    logger.error(
                        "DingTalk send auth error: status=%d body=%s",
                        resp.status_code, resp.text[:200],
                    )
                    return {"status": "failed", "error": f"auth_error_{resp.status_code}"}

                if resp.status_code >= 500:
                    # 服务器错误，可重试
                    raise httpx.HTTPStatusError(
                        f"Server error: {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                resp.raise_for_status()
                result = resp.json()
                logger.debug("DingTalk message sent successfully")
                return {"status": "sent", **result}

            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "DingTalk send attempt %d failed, retrying in %.1fs: %s",
                        attempt + 1, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "DingTalk send failed after %d attempts: %s",
                        MAX_RETRIES, e,
                    )

        return {"status": "failed", "error": str(last_error)}

    async def close(self) -> None:
        """关闭资源。"""
        await self._client.aclose()
        await self._token_manager.close()

    # --- Quick Entry: JSAPI requestAuthCode 用户身份解析 ---

    async def get_user_info_by_auth_code(self, auth_code: str) -> dict[str, Any]:
        """通过 JSAPI authCode 换取用户 userId（staffId 格式）。

        使用已验证的路径：topapi/v2/user/getuserinfo
        直接返回 staffId 格式的 userId，可用于 batchSend。

        Args:
            auth_code: 前端 dd.requestAuthCode() 返回的 code

        Returns:
            包含 userid (staffId 格式) 的 dict
        """
        # 用 app-level access_token + authCode → topapi/v2/user/getuserinfo
        token = await self._get_token()
        url = f"https://oapi.dingtalk.com/topapi/v2/user/getuserinfo?access_token={token}"
        payload = {"code": auth_code}
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if data.get("errcode") != 0:
            raise ValueError(
                f"DingTalk getuserinfo failed: errcode={data.get('errcode')} "
                f"errmsg={data.get('errmsg')}"
            )

        result = data.get("result", {})
        userid = result.get("userid", "")
        if not userid:
            raise ValueError(f"No userid in getuserinfo response: {data}")

        logger.info("Resolved DingTalk userId from authCode: %s", userid)
        return {"userid": userid, "name": result.get("name", "")}
