"""钉钉回复渲染 — 将 Agent 回复渲染为钉钉友好格式。"""

from __future__ import annotations

import logging

from sales_agent.integrations.dingtalk.config import DingTalkConfig
from sales_agent.services.response_formatter import format_text_output, format_sales_visible_sources

logger = logging.getLogger(__name__)

# 错误回复模板
ERROR_TEMPLATES = {
    "model_failed": (
        "我这边暂时无法调用模型服务。你可以稍后再试，"
        "或先把客户问题发给主管确认。"
    ),
    "no_knowledge": (
        "当前知识库没有找到可靠依据。我可以先给通用销售建议，"
        "但涉及产品政策、价格或交付，请以企业内部确认为准。"
    ),
    "risk_blocked": (
        "这个表达涉及未经确认的承诺，我不能直接生成保证类话术。"
        "可以改成更稳妥的表达：\n\n"
        "「建议使用合规的销售表达，不要对外做出未确认的承诺。」"
    ),
}


class DingTalkMessageRenderer:
    """钉钉消息渲染器。"""

    def __init__(self, config: DingTalkConfig):
        self._max_chars = config.max_reply_chars

    def render(self, answer: dict, sources: list[dict], risk_result=None) -> str:
        """渲染 Agent 回复为钉钉友好文本。

        Args:
            answer: Agent 回复 dict（含 summary + sections）
            sources: 检索来源列表
            risk_result: 风险检查结果

        Returns:
            渲染后的文本
        """
        # 复用现有格式化函数
        text = format_text_output(answer)

        # 添加来源标题
        if sources:
            source_text = format_sales_visible_sources(sources)
            if source_text:
                text = f"{text}\n\n{source_text}"

        # 截断
        if len(text) > self._max_chars:
            text = self._truncate(text)

        return text

    def render_error(self, error_type: str) -> str:
        """渲染错误回复。

        Args:
            error_type: 错误类型（model_failed / no_knowledge / risk_blocked）

        Returns:
            用户友好的错误提示
        """
        return ERROR_TEMPLATES.get(error_type, "暂时无法处理，请稍后再试。")

    def _truncate(self, text: str) -> str:
        """智能截断长消息。

        保留优先级：判断 > 推荐话术 > 注意事项 > 来源 > 冗余解释
        """
        if len(text) <= self._max_chars:
            return text

        # 简单截断 + 省略提示
        truncated = text[: self._max_chars - 20]
        # 尝试在最后一个换行处截断
        last_newline = truncated.rfind("\n")
        if last_newline > self._max_chars * 0.6:
            truncated = truncated[:last_newline]

        return f"{truncated}\n\n...(内容过长已截断)"
