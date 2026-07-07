"""钉钉互动卡片发送与流式更新。

使用钉钉卡片 API 实现 AI 流式打字机效果。
参考实现：Yanshi_Omni_Agent 项目。

API 端点：
- 创建并投放卡片：POST https://api.dingtalk.com/v1.0/card/instances/createAndDeliver
- 流式更新（打字机）：PUT  https://api.dingtalk.com/v1.0/card/streaming
- 普通更新卡片数据：PUT  https://api.dingtalk.com/v1.0/card/instances
- 场域：IM_ROBOT（机器人单聊），openSpaceId 格式：dtv1.card//IM_ROBOT.{userId}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.integrations.dingtalk.message_sender import DingTalkAccessTokenManager

logger = logging.getLogger(__name__)

# 钉钉卡片 API 端点
_CREATE_AND_DELIVER_URL = "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver"
_STREAMING_UPDATE_URL = "https://api.dingtalk.com/v1.0/card/streaming"
_UPDATE_CARD_URL = "https://api.dingtalk.com/v1.0/card/instances"

# 重试配置
_MAX_RETRIES = 2
_RETRY_DELAYS = [0.5, 1.5]


class DingTalkCardSender:
    """钉钉互动卡片发送器，支持 AI 流式打字机效果。

    用法::

        sender = DingTalkCardSender(config)
        out_track_id = await sender.send_markdown_card(user_id, "标题", "内容")
        await sender.streaming_update(out_track_id, "content", "更新的内容")
        await sender.streaming_finalize(out_track_id, "content", "最终内容")
    """

    def __init__(self, config: DingTalkConfig) -> None:
        self._config = config
        self._token_manager = DingTalkAccessTokenManager(config.app_key, config.app_secret)
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _get_token(self) -> str:
        return await self._token_manager.get_access_token()

    # ------------------------------------------------------------------
    # 创建并投放卡片（POST /v1.0/card/instances/createAndDeliver）
    # ------------------------------------------------------------------

    async def send_markdown_card(
        self,
        dingtalk_user_id: str,
        title: str,
        markdown_text: str,
    ) -> str:
        """通过 createAndDeliver 接口发送互动卡片到机器人单聊。

        Args:
            dingtalk_user_id: 钉钉用户 staff_id
            title: 卡片标题（用于消息列表展示）
            markdown_text: Markdown 正文内容

        Returns:
            outTrackId，用于后续 streaming_update / update_card
        """
        out_track_id = f"stream_{uuid.uuid4().hex[:12]}"

        payload = {
            "cardTemplateId": self._config.card_template_id,
            "outTrackId": out_track_id,
            "cardData": {
                "cardParamMap": {
                    "content": markdown_text,
                },
            },
            "openSpaceId": f"dtv1.card//IM_ROBOT.{dingtalk_user_id}",
            "callbackType": "STREAM",
            "userIdType": 1,
            "imRobotOpenSpaceModel": {"supportForward": True},
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        }

        result = await self._request_with_retry("POST", _CREATE_AND_DELIVER_URL, payload)
        logger.info(
            "Card sent: outTrackId=%s userId=%s result=%s",
            out_track_id, dingtalk_user_id,
            json.dumps(result, ensure_ascii=False)[:200],
        )
        return out_track_id

    # ------------------------------------------------------------------
    # 流式更新（PUT /v1.0/card/streaming）— 打字机效果
    # ------------------------------------------------------------------

    async def streaming_update(
        self,
        out_track_id: str,
        content: str,
        *,
        key: str = "content",
        guid: str | None = None,
    ) -> None:
        """流式更新卡片内容（打字机效果）。

        Args:
            out_track_id: send_markdown_card 返回的 outTrackId
            content: 当前的完整流式内容
            key: 需要流式更新的变量名（默认 content）
            guid: 请求唯一标志（幂等），不传则自动生成
        """
        if not out_track_id:
            return

        if guid is None:
            guid = uuid.uuid4().hex.upper()

        payload = {
            "outTrackId": out_track_id,
            "guid": guid,
            "key": key,
            "content": content,
            "isFull": True,
            "isFinalize": False,
            "isError": False,
        }

        await self._request_with_retry("PUT", _STREAMING_UPDATE_URL, payload)

    async def streaming_finalize(
        self,
        out_track_id: str,
        content: str,
        *,
        key: str = "content",
    ) -> None:
        """流式更新结束帧（关闭"生成中"指示器）。

        Args:
            out_track_id: send_markdown_card 返回的 outTrackId
            content: 最终完整内容
            key: 需要流式更新的变量名
        """
        if not out_track_id:
            return

        guid = uuid.uuid4().hex.upper()
        payload = {
            "outTrackId": out_track_id,
            "guid": guid,
            "key": key,
            "content": content,
            "isFull": True,
            "isFinalize": True,
            "isError": False,
        }

        await self._request_with_retry("PUT", _STREAMING_UPDATE_URL, payload)

    # ------------------------------------------------------------------
    # 普通更新卡片（PUT /v1.0/card/instances）— fallback
    # ------------------------------------------------------------------

    async def update_card(
        self,
        out_track_id: str,
        markdown_text: str,
    ) -> None:
        """更新已有卡片的公共数据（fallback 方式）。

        Args:
            out_track_id: send_markdown_card 返回的 outTrackId
            markdown_text: 新的 Markdown 内容
        """
        if not out_track_id:
            logger.warning("update_card called with empty out_track_id, skipping")
            return

        payload = {
            "outTrackId": out_track_id,
            "userIdType": 1,
            "cardUpdateOptions": {
                "updateCardDataByKey": True,
                "updatePrivateDataByKey": False,
            },
            "cardData": {
                "cardParamMap": {
                    "content": markdown_text,
                },
            },
        }

        await self._request_with_retry("PUT", _UPDATE_CARD_URL, payload)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self, method: str, url: str, payload: dict,
    ) -> dict[str, Any]:
        """带重试的 HTTP 请求。"""
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                token = await self._get_token()
                headers = {
                    "x-acs-dingtalk-access-token": token,
                    "Content-Type": "application/json",
                }

                if method == "POST":
                    resp = await self._client.post(url, json=payload, headers=headers)
                else:
                    resp = await self._client.put(url, json=payload, headers=headers)

                if resp.status_code in (401, 403):
                    raise httpx.HTTPStatusError(
                        f"Card API auth error: status={resp.status_code}",
                        request=resp.request, response=resp,
                    )

                if resp.status_code == 400:
                    raise httpx.HTTPStatusError(
                        f"Card API bad request: method={method} url={url} body={resp.text[:200]}",
                        request=resp.request, response=resp,
                    )

                if resp.status_code == 404:
                    raise httpx.HTTPStatusError(
                        f"Card API not found: method={method} url={url} body={resp.text[:200]}",
                        request=resp.request, response=resp,
                    )

                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Server error: {resp.status_code}",
                        request=resp.request, response=resp,
                    )

                resp.raise_for_status()
                return resp.json()

            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "Card API %s %s attempt %d failed, retrying in %.1fs: %s",
                        method, url, attempt + 1, delay, e,
                    )
                    await asyncio.sleep(delay)

        logger.error("Card API %s %s failed after %d attempts: %s", method, url, _MAX_RETRIES + 1, last_error)
        raise last_error  # type: ignore[misc]

    async def close(self) -> None:
        """关闭资源。"""
        await self._client.aclose()
        await self._token_manager.close()
