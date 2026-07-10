"""风险检查服务：三次检查（输入预检、来源检查、输出后检）。

对应 spec 第 13 节。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sales_agent.llm.call_params import get_call_params
# 复用 task_router 的平衡括号 JSON 提取器：``re.search(r"\{[^}]+\}")`` 在嵌套
# JSON 上会截断（risk prompt 同样可能返回嵌套结构），此处与 task_router 保持一致。
from sales_agent.services.task_router import _extract_first_json

# risk prompt 已迁移至 config/prompts.yaml（get_prompt("risk", "risk_check")）

logger = logging.getLogger(__name__)


# --- 风险类型常量 ---
RISK_PRICE_COMMITMENT = "price_commitment"
RISK_DISCOUNT_COMMITMENT = "discount_commitment"
RISK_DELIVERY_COMMITMENT = "delivery_commitment"
RISK_CONTRACT_COMMITMENT = "contract_commitment"
RISK_UNSUPPORTED_CLAIM = "unsupported_claim"
RISK_COMPETITOR_ATTACK = "competitor_attack"
RISK_SENSITIVE_EXTERNAL = "sensitive_external_message"
RISK_CROSS_TENANT = "cross_tenant_leakage"
RISK_MANIPULATIVE = "manipulative_sales"


# --- 风险动作 ---
ACTION_ALLOW = "allow"
ACTION_WARN = "warn"
ACTION_REWRITE = "rewrite"
ACTION_BLOCK = "block"


# --- 风险等级 ---
LEVEL_NONE = "none"
LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"


@dataclass
class RiskCheckResult:
    """风险检查结果。"""

    level: str = LEVEL_NONE
    flags: list[str] = field(default_factory=list)
    action: str = ACTION_ALLOW
    notice: str = ""
    rewrite_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "flags": self.flags,
            "action": self.action,
            "notice": self.notice,
            "rewrite_summary": self.rewrite_summary,
        }


# --- 输入预检规则 ---
# (pattern, risk_type, level, action)
_INPUT_RULES: list[tuple[str, str, str, str]] = [
    # 价格承诺
    (r"保证?\s*最低价", RISK_PRICE_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定可以?\s*(申请到|给)\s*折扣", RISK_DISCOUNT_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"今天定就给你?\s*(降|优惠|打折)", RISK_PRICE_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"价格绝对比竞品低", RISK_PRICE_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    # 夸大承诺
    (r"保证?\s*(业绩|效果|结果)\s*(翻倍|提升|翻番)", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"100%\s*(解决|成功|有效)", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定成交", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"行业第一|绝对领先", RISK_UNSUPPORTED_CLAIM, LEVEL_MEDIUM, ACTION_REWRITE),
    # 交付承诺
    (r"保证?\s*一周?上线", RISK_DELIVERY_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定能?\s*按时交付", RISK_DELIVERY_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"所有定制都能做", RISK_DELIVERY_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    # 操纵性销售
    (r"欺骗客户|忽悠客户", RISK_MANIPULATIVE, LEVEL_HIGH, ACTION_BLOCK),
]


# --- 输出后检规则 ---
_OUTPUT_RULES: list[tuple[str, str, str, str]] = [
    # 价格承诺
    (r"保证?\s*最低价", RISK_PRICE_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定可以?\s*(给|申请|提供)\s*折扣", RISK_DISCOUNT_COMMITMENT, LEVEL_HIGH, ACTION_WARN),
    (r"保证?\s*(业绩|效果|结果)\s*(翻倍|提升)", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"100%\s*(解决|成功)", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定成交", RISK_UNSUPPORTED_CLAIM, LEVEL_HIGH, ACTION_BLOCK),
    (r"他们(肯定|一定)?不靠谱", RISK_COMPETITOR_ATTACK, LEVEL_MEDIUM, ACTION_REWRITE),
    (r"竞品(都是)?(骗人|垃圾|很差)", RISK_COMPETITOR_ATTACK, LEVEL_HIGH, ACTION_BLOCK),
    (r"保证?\s*一周?上线", RISK_DELIVERY_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
    (r"一定能?\s*按时交付", RISK_DELIVERY_COMMITMENT, LEVEL_HIGH, ACTION_BLOCK),
]


def _check_rules(text: str, rules: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str]]:
    """检查文本是否匹配风险规则，返回 [(risk_type, level, action)]。"""
    hits = []
    for pattern, risk_type, level, action in rules:
        if re.search(pattern, text):
            hits.append((risk_type, level, action))
    return hits


def merge_risk_results(rule: RiskCheckResult, llm: RiskCheckResult) -> RiskCheckResult:
    """合并规则和 LLM 风险结果，取更高的风险等级。

    规则检查（``full_check``）与 LLM 检查（``check_llm_risk``）结果同构，
    合并策略：取更高 level 的 action/notice/rewrite_summary，flags 取并集。
    LLM 风控失败时应由调用方 try/except 回退到规则结果（避免静默放行）。
    """
    level_priority = {LEVEL_NONE: 0, LEVEL_LOW: 1, LEVEL_MEDIUM: 2, LEVEL_HIGH: 3}

    rule_level = level_priority.get(rule.level, 0)
    llm_level = level_priority.get(llm.level, 0)

    # 取更高的
    if llm_level > rule_level:
        merged = RiskCheckResult(
            level=llm.level,
            flags=list(set(rule.flags + llm.flags)),
            action=llm.action,
            notice=llm.notice or rule.notice,
            rewrite_summary=llm.rewrite_summary or rule.rewrite_summary,
        )
    else:
        merged = RiskCheckResult(
            level=rule.level,
            flags=list(set(rule.flags + llm.flags)),
            action=rule.action,
            notice=rule.notice,
            rewrite_summary=rule.rewrite_summary,
        )

    return merged


class RiskChecker:
    """风险检查器。"""

    def __init__(self, risk_config: dict[str, str] | None = None):
        """
        Args:
            risk_config: 租户风险策略，key 为风险类型，value 为默认动作。
        """
        self.risk_config = risk_config or {}

    def _get_action(self, risk_type: str, default_action: str) -> str:
        """获取风险动作，租户配置可覆盖默认动作（但不能降低平台底线）。"""
        configured = self.risk_config.get(f"default_{risk_type}_action")
        if configured:
            # 平台底线：block 级别的不能降级
            if default_action == ACTION_BLOCK:
                return ACTION_BLOCK
            return configured
        return default_action

    def check_input(self, message: str) -> RiskCheckResult:
        """输入预检：检查用户输入是否包含高风险请求。

        Returns:
            RiskCheckResult
        """
        hits = _check_rules(message, _INPUT_RULES)
        if not hits:
            return RiskCheckResult()

        # 取最高风险等级
        worst = max(hits, key=lambda h: (h[1] == LEVEL_HIGH, h[1] == LEVEL_MEDIUM))
        risk_type, level, default_action = worst
        action = self._get_action(risk_type, default_action)

        result = RiskCheckResult(
            level=level,
            flags=[h[0] for h in hits],
            action=action,
        )

        if action == ACTION_BLOCK:
            result.notice = "该请求涉及高风险承诺，已改为安全建议"
        elif action == ACTION_WARN:
            result.notice = "涉及价格或折扣，请按企业内部流程确认后再对客户表达"

        return result

    def check_sources(
        self,
        sources: list[dict],
        tenant_id: str,
    ) -> RiskCheckResult:
        """检索来源检查：确认来源属于当前租户。

        Args:
            sources: 检索结果列表
            tenant_id: 当前请求的 tenant_id

        Returns:
            RiskCheckResult
        """
        flags = []
        for source in sources:
            source_tid = source.get("tenant_id", "")
            if source_tid and source_tid != tenant_id:
                flags.append(RISK_CROSS_TENANT)
                logger.critical(
                    "CROSS-TENANT LEAKAGE! tenant_id=%s, source tenant=%s, chunk=%s",
                    tenant_id,
                    source_tid,
                    source.get("chunk_id", ""),
                )

        if flags:
            return RiskCheckResult(
                level=LEVEL_HIGH,
                flags=flags,
                action=ACTION_BLOCK,
                notice="检测到跨租户数据泄漏风险，已拦截",
            )

        return RiskCheckResult()

    def check_output(self, answer_text: str) -> RiskCheckResult:
        """输出后检：检查模型输出是否包含风险内容。

        Args:
            answer_text: 模型输出的文本（序列化后的）

        Returns:
            RiskCheckResult
        """
        hits = _check_rules(answer_text, _OUTPUT_RULES)
        if not hits:
            return RiskCheckResult()

        worst = max(hits, key=lambda h: (h[1] == LEVEL_HIGH, h[1] == LEVEL_MEDIUM))
        risk_type, level, default_action = worst
        action = self._get_action(risk_type, default_action)

        result = RiskCheckResult(
            level=level,
            flags=[h[0] for h in hits],
            action=action,
        )

        if action == ACTION_BLOCK:
            result.notice = "输出包含高风险承诺，已改为安全建议"
        elif action == ACTION_WARN:
            result.notice = "涉及价格或折扣，请按企业内部流程确认后再对客户表达"
        elif action == ACTION_REWRITE:
            result.rewrite_summary = "已将绝对化表达改为基于价值的表达"

        return result

    async def check_llm_risk(
        self,
        message: str,
        answer_text: str,
        chat_model: Any,
        risk_prompt: str | None = None,
    ) -> RiskCheckResult:
        """LLM 风险检查：用 LLM 判断输出是否包含风险内容。

        仅在规则风险检查为 medium/high 或命中风险关键词时调用。
        对应 spec §7.2。
        """
        try:
            prompt = (risk_prompt or get_prompt("risk", "risk_check").template).format(
                message=message, answer=answer_text[:1000]
            )
            p = get_call_params("risk_checker")
            response = await chat_model.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=p.temperature,
                max_tokens=p.max_tokens,
            )
            # 用平衡花括号提取完整 JSON（支持嵌套），与 task_router 一致；
            # 旧 ``re.search(r"\{[^}]+\}")`` 在嵌套 JSON 上会截断出非法 JSON。
            data = _extract_first_json(response)
            if data:
                level = data.get("level", LEVEL_NONE)
                action = data.get("action", ACTION_ALLOW)
                # 安全校验：确保值合法
                if level not in (LEVEL_NONE, LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH):
                    level = LEVEL_NONE
                if action not in (ACTION_ALLOW, ACTION_WARN, ACTION_REWRITE, ACTION_BLOCK):
                    action = ACTION_ALLOW
                return RiskCheckResult(
                    level=level,
                    flags=data.get("flags", []),
                    action=action,
                    notice=data.get("notice", ""),
                )
        except Exception as e:
            logger.warning("LLM risk check failed: %s", e)

        return RiskCheckResult()

    def full_check(
        self,
        message: str,
        sources: list[dict],
        tenant_id: str,
        answer_text: str,
    ) -> RiskCheckResult:
        """完整的三阶段风险检查。

        Returns:
            合并后的 RiskCheckResult（取最高风险等级）
        """
        results = [
            self.check_input(message),
            self.check_sources(sources, tenant_id),
            self.check_output(answer_text),
        ]

        # 合并：取最高风险等级
        level_priority = {LEVEL_NONE: 0, LEVEL_LOW: 1, LEVEL_MEDIUM: 2, LEVEL_HIGH: 3}
        best = max(results, key=lambda r: level_priority.get(r.level, 0))

        # 合并所有 flags
        all_flags = []
        for r in results:
            all_flags.extend(r.flags)

        # 如果任何一个是 block，最终就是 block
        if any(r.action == ACTION_BLOCK for r in results):
            best.action = ACTION_BLOCK
        elif any(r.action == ACTION_WARN for r in results):
            best.action = ACTION_WARN
        elif any(r.action == ACTION_REWRITE for r in results):
            best.action = ACTION_REWRITE

        best.flags = list(set(all_flags))  # 去重
        return best
