"""销售阶段分类法（Sales Stage Taxonomy）。

定义系统范围内一致使用的销售阶段模型。
路由、prompt、输出 schema 和评估都引用这里的规范阶段。
"""

from __future__ import annotations

from typing import Any


# 每个阶段包含：
#   id (key):       稳定标识符，代码中引用
#   display:        中文显示名
#   description:    阶段描述，用于 prompt 和文档
#   typical_actions: 该阶段常见的销售动作（供 prompt 参考）

SALES_STAGES: dict[str, dict[str, Any]] = {
    "lead_discovery": {
        "display": "线索发现",
        "description": "识别和筛选潜在客户，判断是否值得进一步投入资源。",
        "typical_actions": [
            "搜集潜在客户信息",
            "判断客户画像匹配度",
            "初步确认需求和预算可能性",
        ],
    },
    "first_contact": {
        "display": "首次触达",
        "description": "第一次主动联系客户，建立初步认知和信任。",
        "typical_actions": [
            "发送触达消息或邮件",
            "电话/微信首次沟通",
            "了解对方基本情况和角色",
        ],
    },
    "needs_discovery": {
        "display": "需求挖掘",
        "description": "深入了解客户业务痛点、需求和决策流程。",
        "typical_actions": [
            "提问了解业务场景",
            "确认痛点和优先级",
            "了解决策人和流程",
        ],
    },
    "visit_preparation": {
        "display": "拜访准备",
        "description": "为即将到来的客户拜访做系统化准备。",
        "typical_actions": [
            "制定拜访目标和议程",
            "准备探询问题和产品角度",
            "预判异议和应对策略",
        ],
    },
    "proposal": {
        "display": "方案报价",
        "description": "向客户呈现解决方案和报价，进入商务谈判阶段。",
        "typical_actions": [
            "输出定制化方案",
            "报价和条款沟通",
            "处理方案层面的反馈",
        ],
    },
    "objection": {
        "display": "异议处理",
        "description": "客户提出价格、功能、竞品等方面的异议，需要有效回应。",
        "typical_actions": [
            "澄清异议本质",
            "提供价值对比和案例",
            "寻找折中或替代方案",
        ],
    },
    "follow_up": {
        "display": "跟进维护",
        "description": "方案报价后的持续跟进，保持客户参与度。",
        "typical_actions": [
            "定期跟进进展",
            "提供补充材料或案例",
            "安排下次沟通",
        ],
    },
    "deal_closing": {
        "display": "成交推进",
        "description": "推动客户做出最终采购决策，完成签约。",
        "typical_actions": [
            "确认剩余障碍",
            "促成决策",
            "推进签约流程",
        ],
    },
    "post_mortem": {
        "display": "复盘总结",
        "description": "对已完成的销售过程进行复盘，总结经验教训。",
        "typical_actions": [
            "分析成功/失败原因",
            "记录关键教训",
            "优化销售流程",
        ],
    },
}

# 所有规范阶段 ID 列表（有序）
ALL_STAGES = list(SALES_STAGES.keys())


def get_stage(stage_id: str) -> dict[str, Any] | None:
    """获取指定阶段的信息。"""
    return SALES_STAGES.get(stage_id)


def validate_stage(stage_id: str) -> bool:
    """校验 stage_id 是否为规范阶段。"""
    return stage_id in SALES_STAGES
