"""钉钉集成配置模型。"""

from pydantic import BaseModel, Field


class DingTalkRateLimitConfig(BaseModel):
    """钉钉速率限制配置。"""

    per_user_per_minute: int = 20
    per_user_per_day: int = 500
    per_tenant_per_minute: int = 200


class DingTalkConfig(BaseModel):
    """钉钉单聊集成配置。

    凭证通过环境变量注入，不在 YAML 中存储：
    DINGTALK_MESSAGE_MODE, DINGTALK_CORP_ID, DINGTALK_APP_KEY, DINGTALK_APP_SECRET,
    DINGTALK_ROBOT_CODE, DINGTALK_ENCRYPT_TOKEN, DINGTALK_AES_KEY

    Stream 模式（默认）：常驻 WebSocket 连接，不需要公网回调 URL。
    HTTP 模式：钉钉推送事件到 callback_path。
    """

    enabled: bool = False
    message_mode: str = "stream"  # "stream" | "http"
    mode: str = "single_chat"
    corp_id: str = ""
    app_key: str = ""
    app_secret: str = ""
    robot_code: str = ""
    encrypt_token: str = ""
    aes_key: str = ""
    callback_path: str = "/integrations/dingtalk/events"
    public_url: str = ""  # 服务公网地址，如 https://aijiaolian.com.cn
    async_processing: bool = True
    reply_format: str = "markdown"
    max_reply_chars: int = 3000
    rate_limit: DingTalkRateLimitConfig = Field(default_factory=DingTalkRateLimitConfig)
    reset_commands: list[str] = Field(
        default_factory=lambda: [
            "新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new",
        ]
    )

    # --- 流式卡片配置 ---
    streaming_enabled: bool = False
    card_template_id: str = ""  # 钉钉互动卡片模板 ID，用户后续填入
    stream_update_interval_ms: int = 300  # 卡片更新最小间隔（毫秒）
    stream_min_chunk_chars: int = 30  # 最少积累字符数才触发更新

    # --- 媒体消息适配：图片理解 + 语音转写 ---
    media_enabled: bool = True
    media_base_url: str = ""
    media_api_key_env: str = ""
    vision_model: str = "qwen-vl-plus"
    audio_model: str = "whisper-1"
    media_download_timeout_seconds: int = 20
