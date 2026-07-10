"""钉钉媒体消息适配。

把钉钉图片/语音媒体转换为现有 Agent 可消费的文本：
- 图片：下载媒体后调用视觉模型生成业务上下文描述。
- 语音：下载媒体后调用 ASR 转写。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx
from openai import AsyncOpenAI

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.llm.call_params import get_call_params
from sales_agent.integrations.dingtalk.message_sender import DingTalkAccessTokenManager

logger = logging.getLogger(__name__)

DINGTALK_MEDIA_DOWNLOAD_URL = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
_IMAGE_TYPES = {"picture", "image", "richText"}
_AUDIO_TYPES = {"voice", "audio"}
_SUPPORTED_MEDIA_TYPES = _IMAGE_TYPES | _AUDIO_TYPES
_RECOGNITION_TEXT_KEYS = {
    "recognition",
    "recognizedText",
    "recognized_text",
    "speechRecognition",
    "speech_recognition",
    "voiceRecognition",
    "voice_recognition",
    "audioText",
    "audio_text",
    "asrText",
    "asr_text",
    "transcript",
    "transcription",
}

# --- 模块级 prompt 常量（注册进 BUILTIN_PROMPTS，获得 DB 覆盖路径） ---
MEDIA_VISION_SYSTEM_PROMPT = (
    "你是销售陪跑助手的图片理解模块。请用中文简洁描述图片中的文字、场景、"
    "客户意图和对销售回复有用的信息，不要编造看不到的内容。"
)
MEDIA_VISION_USER_PROMPT = "请理解这张钉钉用户发来的图片，输出可供销售 Agent 回答的上下文。"
MEDIA_AUDIO_TRANSCRIBE_PROMPT = "请只转写这段语音的中文内容，不要添加解释。"


class DingTalkMediaAdapter:
    """Convert DingTalk media messages into text for the Online Graph."""

    def __init__(
        self,
        config: DingTalkConfig,
        settings: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        self._config = config
        self._settings = settings
        self._http_client = http_client or httpx.AsyncClient(
            timeout=float(config.media_download_timeout_seconds)
        )
        self._owns_http_client = http_client is None
        self._token_manager = DingTalkAccessTokenManager(config.app_key, config.app_secret)
        self._openai_client = openai_client

    async def to_agent_text(
        self,
        *,
        message_type: str,
        text: str | None,
        download_codes: list[str] | None,
        raw_event: dict[str, Any] | None,
    ) -> str:
        """Return the text that should be sent into the existing Agent pipeline."""
        base_text = (text or "").strip()
        codes = [c for c in (download_codes or []) if c]

        if message_type == "text":
            return base_text

        if message_type not in _SUPPORTED_MEDIA_TYPES:
            raise ValueError(f"Unsupported DingTalk message type: {message_type}")

        if not self._config.media_enabled:
            raise ValueError("Media message support is disabled")

        if not codes:
            codes = self._extract_download_codes(raw_event or {})

        if message_type in _AUDIO_TYPES:
            platform_text = self._extract_platform_recognition_text(raw_event or {})
            if platform_text:
                return "\n".join([base_text, f"[用户发送了一段语音，钉钉识别文本如下：{platform_text}]"]).strip()

            if not codes:
                raise ValueError("No downloadCode found for DingTalk voice message")
            parts = []
            for code in codes:
                media_bytes, mime_type = await self._download_media_bytes(code)
                transcript = (await self._transcribe_audio(media_bytes, mime_type)).strip()
                if transcript:
                    parts.append(transcript)
            if not parts:
                raise ValueError("Voice transcription returned empty text")
            return "\n".join([base_text, f"[用户发送了一段语音，转写如下：{' '.join(parts)}]"]).strip()

        if not codes and base_text:
            return base_text
        if not codes:
            raise ValueError("No downloadCode found for DingTalk image message")

        descriptions = []
        for code in codes:
            media_bytes, mime_type = await self._download_media_bytes(code)
            description = (await self._describe_image(media_bytes, mime_type)).strip()
            if description:
                descriptions.append(description)
        if not descriptions:
            raise ValueError("Image understanding returned empty text")

        visual_text = "\n".join(
            f"[用户发送了图片{i + 1}，识别和理解如下：{desc}]"
            for i, desc in enumerate(descriptions)
        )
        return "\n".join([base_text, visual_text]).strip()

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()
        await self._token_manager.close()

    async def _download_media_bytes(self, download_code: str) -> tuple[bytes, str]:
        token = await self._token_manager.get_access_token()
        headers = {"x-acs-dingtalk-access-token": token}
        payload = {
            "robotCode": self._config.robot_code or self._config.app_key,
            "downloadCode": download_code,
        }
        resp = await self._http_client.post(
            DINGTALK_MEDIA_DOWNLOAD_URL,
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        download_url = resp.json().get("downloadUrl")
        if not download_url:
            raise ValueError("DingTalk media download API did not return downloadUrl")

        media_resp = await self._http_client.get(download_url)
        media_resp.raise_for_status()
        mime_type = media_resp.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        return media_resp.content, mime_type

    async def _describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        client = self._get_openai_client()
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        p = get_call_params("media_vision")
        response = await client.chat.completions.create(
            model=self._config.vision_model,
            messages=[
                {
                    "role": "system",
                    "content": MEDIA_VISION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": MEDIA_VISION_USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=p.temperature,
            max_tokens=p.max_tokens,
        )
        content = response.choices[0].message.content
        if isinstance(content, list):
            return "".join(str(item) for item in content)
        return content or ""

    async def _transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        if self._config.audio_model.startswith("qwen-audio"):
            return await self._transcribe_audio_with_chat(audio_bytes, mime_type)

        client = self._get_openai_client()
        filename = _filename_for_mime(mime_type)
        response = await client.audio.transcriptions.create(
            model=self._config.audio_model,
            file=(filename, audio_bytes, mime_type),
        )
        text = getattr(response, "text", None)
        if text is not None:
            return text
        if isinstance(response, dict):
            return str(response.get("text", ""))
        return str(response)

    async def _transcribe_audio_with_chat(self, audio_bytes: bytes, mime_type: str) -> str:
        client = self._get_openai_client()
        audio_format = _audio_format_for_mime(mime_type)
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        p = get_call_params("media_audio")
        response = await client.chat.completions.create(
            model=self._config.audio_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": MEDIA_AUDIO_TRANSCRIBE_PROMPT},
                        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": audio_format}},
                    ],
                }
            ],
            temperature=p.temperature,
            max_tokens=p.max_tokens,
        )
        content = response.choices[0].message.content
        if isinstance(content, list):
            return "".join(str(item) for item in content)
        return content or ""

    def _get_openai_client(self) -> AsyncOpenAI:
        if self._openai_client is not None:
            return self._openai_client
        model_config = getattr(self._settings, "model", None)
        base_url = (
            self._config.media_base_url
            or os.getenv("DINGTALK_MEDIA_BASE_URL")
            or getattr(model_config, "base_url", None)
            or os.getenv("MODEL_BASE_URL")
        )
        api_key_env = (
            self._config.media_api_key_env
            or ("DINGTALK_MEDIA_API_KEY" if os.getenv("DINGTALK_MEDIA_API_KEY") else "")
            or getattr(model_config, "api_key_env", None)
            or "MODEL_API_KEY"
        )
        api_key = os.getenv(api_key_env) or os.getenv("DINGTALK_MEDIA_API_KEY") or os.getenv("MODEL_API_KEY")
        if not api_key or not base_url:
            raise ValueError("DINGTALK_MEDIA_API_KEY/DINGTALK_MEDIA_BASE_URL or MODEL_API_KEY/MODEL_BASE_URL are required for DingTalk media understanding")
        timeout_seconds = getattr(model_config, "timeout_seconds", 30)
        max_retries = getattr(model_config, "max_retries", 2)
        self._openai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout_seconds),
            max_retries=max_retries,
        )
        return self._openai_client

    @staticmethod
    def _extract_platform_recognition_text(raw_event: dict[str, Any]) -> str:
        values: list[str] = []

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    walk(child, child_key)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)
            elif isinstance(value, str) and key in _RECOGNITION_TEXT_KEYS:
                cleaned = value.strip()
                if cleaned:
                    values.append(cleaned)

        walk(raw_event)
        return " ".join(dict.fromkeys(values))

    @staticmethod
    def _extract_download_codes(raw_event: dict[str, Any]) -> list[str]:
        codes: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"downloadCode", "download_code"} and isinstance(child, str) and child:
                        codes.append(child)
                    else:
                        walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(raw_event)
        return list(dict.fromkeys(codes))


def extract_download_codes(raw_event: dict[str, Any]) -> list[str]:
    """Public helper used by event normalization and Stream SDK events."""
    return DingTalkMediaAdapter._extract_download_codes(raw_event)


def supported_media_type(message_type: str) -> bool:
    return message_type in _SUPPORTED_MEDIA_TYPES


def _filename_for_mime(mime_type: str) -> str:
    ext = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "audio/amr": "amr",
        "audio/aac": "aac",
        "audio/mp4": "m4a",
    }.get(mime_type, "bin")
    return f"dingtalk-audio.{ext}"


def _audio_format_for_mime(mime_type: str) -> str:
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "audio/amr": "amr",
        "audio/aac": "aac",
        "audio/mp4": "m4a",
    }.get(mime_type, "wav")
