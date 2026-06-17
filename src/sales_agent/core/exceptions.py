"""自定义异常类和错误码。"""

from enum import Enum


class ErrorCode(str, Enum):
    """错误码，对应 spec 9.2 节。"""

    INVALID_REQUEST = "INVALID_REQUEST"
    MESSAGE_EMPTY = "MESSAGE_EMPTY"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
    TENANT_DISABLED = "TENANT_DISABLED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    MODEL_FAILED = "MODEL_FAILED"
    RISK_BLOCKED = "RISK_BLOCKED"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    STARTUP_VALIDATION_FAILED = "STARTUP_VALIDATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # --- 钉钉集成错误码 ---
    DINGTALK_TENANT_MISMATCH = "DINGTALK_TENANT_MISMATCH"
    DINGTALK_SIGNATURE_FAILED = "DINGTALK_SIGNATURE_FAILED"
    DINGTALK_DEDUP = "DINGTALK_DEDUP"
    DINGTALK_RATE_LIMITED = "DINGTALK_RATE_LIMITED"
    DINGTALK_SEND_FAILED = "DINGTALK_SEND_FAILED"
    DINGTALK_UNSUPPORTED_MESSAGE = "DINGTALK_UNSUPPORTED_MESSAGE"
    DINGTALK_NOT_CONFIGURED = "DINGTALK_NOT_CONFIGURED"


# 错误码到 HTTP 状态码的映射
ERROR_HTTP_STATUS = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.MESSAGE_EMPTY: 400,
    ErrorCode.MESSAGE_TOO_LONG: 400,
    ErrorCode.TENANT_NOT_FOUND: 404,
    ErrorCode.TENANT_DISABLED: 403,
    ErrorCode.RETRIEVAL_FAILED: 500,
    ErrorCode.MODEL_FAILED: 502,
    ErrorCode.RISK_BLOCKED: 200,
    ErrorCode.TENANT_MISMATCH: 403,
    ErrorCode.MODEL_AUTH_FAILED: 502,
    ErrorCode.STARTUP_VALIDATION_FAILED: 503,
    ErrorCode.INTERNAL_ERROR: 500,
    # --- 钉钉集成 ---
    ErrorCode.DINGTALK_TENANT_MISMATCH: 403,
    ErrorCode.DINGTALK_SIGNATURE_FAILED: 401,
    ErrorCode.DINGTALK_DEDUP: 200,
    ErrorCode.DINGTALK_RATE_LIMITED: 429,
    ErrorCode.DINGTALK_SEND_FAILED: 502,
    ErrorCode.DINGTALK_UNSUPPORTED_MESSAGE: 200,
    ErrorCode.DINGTALK_NOT_CONFIGURED: 503,
}

# 错误码到用户可见提示的映射
ERROR_USER_MESSAGE = {
    ErrorCode.INVALID_REQUEST: "请求内容不完整，请检查后重试",
    ErrorCode.MESSAGE_EMPTY: "请输入要咨询的问题",
    ErrorCode.MESSAGE_TOO_LONG: "内容过长，请分段发送",
    ErrorCode.TENANT_NOT_FOUND: "当前企业未开通或配置不存在",
    ErrorCode.TENANT_DISABLED: "当前企业服务暂不可用",
    ErrorCode.RETRIEVAL_FAILED: "暂时无法查询知识库，请稍后重试",
    ErrorCode.MODEL_FAILED: "模型服务暂时不可用，请稍后重试",
    ErrorCode.RISK_BLOCKED: "该请求涉及高风险承诺，已改为安全建议",
    ErrorCode.TENANT_MISMATCH: "请求租户与当前 Agent 实例不匹配",
    ErrorCode.MODEL_AUTH_FAILED: "模型认证失败，请检查当前租户模型配置",
    ErrorCode.STARTUP_VALIDATION_FAILED: "Agent 启动配置校验失败",
    ErrorCode.INTERNAL_ERROR: "系统异常，请稍后重试",
    # --- 钉钉集成 ---
    ErrorCode.DINGTALK_TENANT_MISMATCH: "钉钉企业与当前 Agent 实例不匹配",
    ErrorCode.DINGTALK_SIGNATURE_FAILED: "钉钉回调签名校验失败",
    ErrorCode.DINGTALK_DEDUP: "重复事件已忽略",
    ErrorCode.DINGTALK_RATE_LIMITED: "你发送得有点快，我先暂停处理一下。请稍后再试。",
    ErrorCode.DINGTALK_SEND_FAILED: "钉钉消息发送失败",
    ErrorCode.DINGTALK_UNSUPPORTED_MESSAGE: "暂时只支持文字消息。图片、文件、语音功能后续开放。",
    ErrorCode.DINGTALK_NOT_CONFIGURED: "钉钉集成未配置",
}


class SalesAgentError(Exception):
    """销售 Agent 基础异常。"""

    def __init__(
        self,
        code: ErrorCode,
        detail: str = "",
        user_message: str | None = None,
    ):
        self.code = code
        self.detail = detail
        self.user_message = user_message or ERROR_USER_MESSAGE.get(code, "未知错误")
        super().__init__(self.user_message)


class TenantNotFoundError(SalesAgentError):
    """租户不存在。"""

    def __init__(self, tenant_id: str):
        super().__init__(
            code=ErrorCode.TENANT_NOT_FOUND,
            detail=f"tenant_id={tenant_id} was not found",
        )


class TenantDisabledError(SalesAgentError):
    """租户被禁用。"""

    def __init__(self, tenant_id: str):
        super().__init__(
            code=ErrorCode.TENANT_DISABLED,
            detail=f"tenant_id={tenant_id} is disabled",
        )


class ValidationError(SalesAgentError):
    """请求校验失败。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.INVALID_REQUEST,
            detail=detail,
        )


class MessageEmptyError(SalesAgentError):
    """消息为空。"""

    def __init__(self):
        super().__init__(code=ErrorCode.MESSAGE_EMPTY)


class MessageTooLongError(SalesAgentError):
    """消息过长。"""

    def __init__(self, max_chars: int):
        super().__init__(
            code=ErrorCode.MESSAGE_TOO_LONG,
            detail=f"Message exceeds {max_chars} characters",
        )


class RetrievalFailedError(SalesAgentError):
    """检索失败。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.RETRIEVAL_FAILED,
            detail=detail,
        )


class ModelFailedError(SalesAgentError):
    """模型调用失败。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.MODEL_FAILED,
            detail=detail,
        )


class RiskBlockedError(SalesAgentError):
    """输出被风险策略拦截。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.RISK_BLOCKED,
            detail=detail,
        )


class TenantMismatchError(SalesAgentError):
    """请求租户与当前 Agent 实例不匹配。"""

    def __init__(self, request_tenant: str, instance_tenant: str):
        super().__init__(
            code=ErrorCode.TENANT_MISMATCH,
            detail=f"request tenant={request_tenant}, instance tenant={instance_tenant}",
        )


class ModelAuthFailedError(SalesAgentError):
    """模型认证失败。"""

    def __init__(self, provider: str = "", api_key_ref: str = ""):
        # detail 中只包含脱敏信息，不包含明文 key
        super().__init__(
            code=ErrorCode.MODEL_AUTH_FAILED,
            detail=f"provider={provider}, key_ref={api_key_ref}",
        )


# --- 钉钉集成异常 ---


class DingTalkTenantMismatchError(SalesAgentError):
    """钉钉企业与当前 Agent 实例不匹配。"""

    def __init__(self, corp_id: str):
        super().__init__(
            code=ErrorCode.DINGTALK_TENANT_MISMATCH,
            detail=f"corp_id={corp_id} does not match instance DINGTALK_CORP_ID",
        )


class DingTalkSignatureError(SalesAgentError):
    """钉钉回调签名校验失败。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.DINGTALK_SIGNATURE_FAILED,
            detail=detail,
        )


class DingTalkRateLimitedError(SalesAgentError):
    """钉钉消息速率超限。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            code=ErrorCode.DINGTALK_RATE_LIMITED,
            detail=detail,
        )
