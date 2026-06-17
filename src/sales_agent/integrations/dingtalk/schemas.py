"""钉钉集成 API 请求/响应模型。"""

from pydantic import BaseModel, Field


class DingTalkEventAccepted(BaseModel):
    """钉钉事件接收成功响应。"""

    status: str = "accepted"
    event_id: str


class DingTalkHealthResponse(BaseModel):
    """钉钉健康检查响应。"""

    status: str
    tenant_id: str
    message_mode: str  # "stream" | "http"
    corp_id_bound: bool
    sender_ready: bool


class DingTalkSendTestRequest(BaseModel):
    """钉钉测试消息发送请求。"""

    dingtalk_user_id: str
    message: str = "这是一条测试消息"


class DingTalkSendTestResponse(BaseModel):
    """钉钉测试消息发送响应。"""

    status: str
    message_id: str | None = None
    error: str | None = None


# --- 快捷入口（Quick Entry）---


class DingTalkQuickEntryRequest(BaseModel):
    """钉钉快捷入口请求 — JSAPI requestAuthCode 流程。"""

    auth_code: str = Field(..., description="JSAPI requestAuthCode 返回的 authCode")
    action: str = Field(..., description="pre_visit_prepare | post_visit_review")
    tenant_id: str = Field(..., description="租户 ID")


class DingTalkQuickEntryResponse(BaseModel):
    """钉钉快捷入口响应。"""

    status: str
    message: str | None = None
    error: str | None = None
