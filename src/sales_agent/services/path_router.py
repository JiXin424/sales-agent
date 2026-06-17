"""路径路由服务：根据请求特征选择 fast / standard / slow 执行路径。

对应 spec §4。
"""

from __future__ import annotations

from dataclasses import dataclass

from sales_agent.core.config import get_settings, PathRouterConfig, LatencyConfig


@dataclass
class PathResult:
    """路径选择结果。"""

    path: str  # "fast" | "standard" | "slow"
    reason: str  # 机器可读原因
    needs_llm_router: bool
    needs_retrieval: bool
    needs_llm_risk_check: bool
    allow_processing_notice: bool


# --- 快速命令集（与 DingTalkCommandParser / config 同步） ---
_FAST_COMMANDS = {"帮助", "help", "？", "?"}
_RESET_COMMANDS = {"新话题", "清空上下文", "重新开始", "忘掉前面", "/reset", "/new"}

# --- LLM 风险关键词（用于判断是否需要 LLM 风险检查） ---
_LLM_RISK_KEYWORDS = [
    "价格", "折扣", "合同", "交付", "保证效果",
    "最低价", "优惠", "竞品对比", "保证",
]


class PathRouter:
    """路径路由器。

    纯规则判断，不调用 LLM 或 DB，<1ms 延迟。
    """

    def __init__(
        self,
        latency_config: LatencyConfig | None = None,
        path_router_config: PathRouterConfig | None = None,
    ):
        settings = get_settings()
        self._latency = latency_config or settings.latency
        self._path_router = path_router_config or settings.path_router

    def select_path(
        self,
        *,
        message: str,
        task_type: str | None = None,
        route_confidence: float = 0.5,
        needs_retrieval: bool = False,
        history_length: int = 0,
        message_length: int = 0,
        input_risk_level: str = "none",
    ) -> PathResult:
        """根据输入特征选择执行路径。

        Args:
            message: 用户原始消息
            task_type: 已识别的任务类型（快速命令时为 None）
            route_confidence: 任务路由置信度
            needs_retrieval: 是否需要 RAG
            history_length: 历史消息字符数
            message_length: 当前消息字符数
            input_risk_level: 规则风险预检等级

        Returns:
            PathResult 包含路径选择和各步骤开关
        """
        stripped = message.strip()

        # --- 1. 快速命令 → fast path ---
        if self._path_router.enable_fast_path:
            if stripped.lower() in _FAST_COMMANDS or stripped in _FAST_COMMANDS:
                return PathResult(
                    path="fast",
                    reason="help_command",
                    needs_llm_router=False,
                    needs_retrieval=False,
                    needs_llm_risk_check=False,
                    allow_processing_notice=False,
                )

            if stripped in _RESET_COMMANDS:
                return PathResult(
                    path="fast",
                    reason="reset_command",
                    needs_llm_router=False,
                    needs_retrieval=False,
                    needs_llm_risk_check=False,
                    allow_processing_notice=False,
                )

        # --- 2. 风险等级驱动（优先于 fast path） ---
        if input_risk_level in ("medium", "high"):
            return PathResult(
                path="slow",
                reason=f"input_risk_{input_risk_level}",
                needs_llm_router=route_confidence < self._path_router.llm_router_confidence_threshold,
                needs_retrieval=needs_retrieval,
                needs_llm_risk_check=True,
                allow_processing_notice=self._path_router.enable_slow_path_notice,
            )

        # --- 3. 根据 task_type 判断路径 ---
        if task_type is not None:
            # Slow path 场景
            if task_type == "knowledge_qa":
                return PathResult(
                    path="slow",
                    reason="knowledge_qa_requires_rag",
                    needs_llm_router=False,
                    needs_retrieval=True,
                    needs_llm_risk_check=False,
                    allow_processing_notice=self._path_router.enable_slow_path_notice,
                )

            if task_type == "conversation_review" and (
                message_length >= self._latency.long_message_chars
                or history_length >= self._latency.long_history_chars
            ):
                return PathResult(
                    path="slow",
                    reason="long_conversation_review",
                    needs_llm_router=False,
                    needs_retrieval=False,
                    needs_llm_risk_check=False,
                    allow_processing_notice=self._path_router.enable_slow_path_notice,
                )

            # Fast path 场景（仅在无风险时）
            if self._path_router.enable_fast_path:
                if task_type == "emotional_support" and not needs_retrieval and message_length < self._latency.long_message_chars:
                    return PathResult(
                        path="fast",
                        reason="simple_emotional_support",
                        needs_llm_router=False,
                        needs_retrieval=False,
                        needs_llm_risk_check=False,
                        allow_processing_notice=False,
                    )

                if task_type == "script_generation" and not needs_retrieval and message_length < self._latency.long_message_chars:
                    return PathResult(
                        path="fast",
                        reason="simple_script_generation",
                        needs_llm_router=False,
                        needs_retrieval=False,
                        needs_llm_risk_check=False,
                        allow_processing_notice=False,
                    )

        # --- 4. 置信度过低 → slow path ---
        if route_confidence < self._path_router.clarify_confidence_threshold:
            return PathResult(
                path="slow",
                reason="low_confidence_may_need_clarification",
                needs_llm_router=True,
                needs_retrieval=False,
                needs_llm_risk_check=False,
                allow_processing_notice=self._path_router.enable_slow_path_notice,
            )

        # --- 5. 检查是否需要 LLM 风险检查 ---
        needs_llm_risk = self._should_llm_risk_check(message, input_risk_level)

        # --- 6. 默认 standard path ---
        return PathResult(
            path=self._latency.default_path,
            reason="default_standard_path",
            needs_llm_router=route_confidence < self._path_router.llm_router_confidence_threshold,
            needs_retrieval=needs_retrieval,
            needs_llm_risk_check=needs_llm_risk,
            allow_processing_notice=False,
        )

    def _should_llm_risk_check(self, message: str, input_risk_level: str) -> bool:
        """判断是否需要 LLM 风险检查。"""
        if input_risk_level in ("medium", "high"):
            return True
        for keyword in _LLM_RISK_KEYWORDS:
            if keyword in message:
                return True
        return False
