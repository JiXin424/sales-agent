"""教练系统的领域常量。

六维能力模型、每日 delta 评分量规、评分边界、里程碑阈值、
段位/等级表、冰山块类型、实时场景提示等。

这些常量被 json_validator / daily_evaluator / report_service 共用，
后续 Phase 3（里程碑/奖励）也复用。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 六维能力模型
# ---------------------------------------------------------------------------

# (key, 中文标签) —— 顺序固定，报告与雷达图按此顺序展示。
DIMENSIONS: list[tuple[str, str]] = [
    ("customer_identification", "客户识别"),
    ("needs_discovery", "需求挖掘"),
    ("value_delivery", "价值传递"),
    ("trust_building", "信任建立"),
    ("deal_advancement", "交易推进"),
    ("review_reflection", "复盘反思"),
]

DIMENSION_KEYS: list[str] = [k for k, _ in DIMENSIONS]
DIMENSION_LABELS: dict[str, str] = {k: v for k, v in DIMENSIONS}


def dimension_label(key: str) -> str:
    """返回维度中文标签，未知 key 回退 key 本身。"""
    return DIMENSION_LABELS.get(key, key)


# ---------------------------------------------------------------------------
# 评分边界与初始值
# ---------------------------------------------------------------------------

MIN_SCORE = 0
MAX_SCORE = 100
INITIAL_SCORE = 50  # "观察不足，基线水平"

# 每日 delta 必须是 [-3, +3] 内的整数
DELTA_MIN = -3
DELTA_MAX = 3

DELTA_MEANINGS: dict[int, str] = {
    3: "突出表现，例如首次使用 SPIN 追问、帮助客户算账、清晰推动决策。",
    2: "明显进步行为。",
    1: "基础正向行为。",
    0: "信号不明显或证据不足。",
    -1: "轻微退步行为。",
    -2: "明显违反销售方法论。",
    -3: "严重问题，例如高风险承诺、强推、明显误导或严重失控。",
}


def clamp_score(score: int) -> int:
    """把分数钳制到 [MIN_SCORE, MAX_SCORE]。"""
    if score < MIN_SCORE:
        return MIN_SCORE
    if score > MAX_SCORE:
        return MAX_SCORE
    return int(score)


def is_valid_delta(delta) -> bool:
    """delta 必须是 [-3, 3] 内的整数（含布尔排除）。"""
    if isinstance(delta, bool):  # bool 是 int 子类，需排除
        return False
    if not isinstance(delta, int):
        return False
    return DELTA_MIN <= delta <= DELTA_MAX


# ---------------------------------------------------------------------------
# 数据充分性
# ---------------------------------------------------------------------------

DATA_SUFFICIENCY_SUFFICIENT = "sufficient"
DATA_SUFFICIENCY_INSUFFICIENT = "insufficient"
DATA_SUFFICIENCY_VALUES = {DATA_SUFFICIENCY_SUFFICIENT, DATA_SUFFICIENCY_INSUFFICIENT}

# 每日评估状态
EVAL_STATUS_SUCCESS = "success"
EVAL_STATUS_SKIPPED = "skipped"
EVAL_STATUS_FAILED = "failed"
EVAL_STATUS_DRY_RUN = "dry_run"
EVAL_STATUS_VALUES = {EVAL_STATUS_SUCCESS, EVAL_STATUS_SKIPPED, EVAL_STATUS_FAILED, EVAL_STATUS_DRY_RUN}

# 默认最小用户消息数（coach_settings.minimum_user_messages）
DEFAULT_MINIMUM_USER_MESSAGES = 3

# ---------------------------------------------------------------------------
# 冰山模型 —— 块类型
# ---------------------------------------------------------------------------

ICEBERG_SURFACE_TYPES = {
    "customer_block": "客户卡点",
    "needs_block": "需求卡点",
    "value_block": "价值卡点",
    "trust_advancement_block": "信任推进卡点",
    "action_rhythm_block": "行动节奏卡点",
}

ICEBERG_DEEP_TYPES = {
    "motivation_block": "目标动力卡点",
    "confidence_block": "信心卡点",
    "belief_block": "信念卡点",
    "emotional_pressure_block": "情绪压力卡点",
}

ICEBERG_SEVERITY_VALUES = {"low", "medium", "high"}

MAX_SURFACE_BLOCKS = 5
MAX_DEEP_BLOCKS = 4

# ---------------------------------------------------------------------------
# 里程碑（Phase 3 定义，此处先声明阈值与计数）
# ---------------------------------------------------------------------------

# 6 维 × 12 阈值 = 72 维度里程碑；12 全维度里程碑；合计 84。
MILESTONE_THRESHOLDS: list[int] = [5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100]
DIMENSION_MILESTONE_COUNT = len(DIMENSIONS) * len(MILESTONE_THRESHOLDS)  # 72
ALL_DIMENSION_MILESTONE_COUNT = len(MILESTONE_THRESHOLDS)               # 12
TOTAL_MILESTONE_COUNT = DIMENSION_MILESTONE_COUNT + ALL_DIMENSION_MILESTONE_COUNT  # 84

# ---------------------------------------------------------------------------
# 段位（rank）—— 基于总积分（Phase 3 才真正累计积分）
# ---------------------------------------------------------------------------

# (段位 key, 中文, 最低积分)
RANK_TABLE: list[tuple[str, str, int]] = [
    ("bronze", "青铜", 0),
    ("silver", "白银", 100),
    ("samurai", "武士", 300),
    ("master", "大师", 600),
    ("king", "王者", 1000),
]
RANK_KEYS = [k for k, _, _ in RANK_TABLE]


def rank_for_points(points: int) -> tuple[str, str]:
    """根据总积分返回 (rank_key, 中文名)。积分越高段位越高。"""
    result_key, result_name = RANK_TABLE[0][0], RANK_TABLE[0][1]
    for key, name, threshold in RANK_TABLE:
        if points >= threshold:
            result_key, result_name = key, name
    return result_key, result_name


# ---------------------------------------------------------------------------
# 等级（level）—— 基于已解锁里程碑数（Phase 3）
# ---------------------------------------------------------------------------

# (里程碑数下界(含), level)
_LEVEL_TABLE: list[tuple[int, int]] = [
    (0, 0),
    (1, 1),
    (6, 2),
    (13, 3),
    (21, 4),
    (31, 5),
    (41, 6),
    (51, 7),
    (61, 8),
    (71, 9),
    (80, 10),
]


def level_for_milestones(unlocked_count: int) -> int:
    """根据已解锁里程碑数返回 level（0..10）。"""
    level = 0
    for threshold, lv in _LEVEL_TABLE:
        if unlocked_count >= threshold:
            level = lv
    return level


# ---------------------------------------------------------------------------
# 实时教练 —— 场景提示（Phase 4 使用，先声明）
# ---------------------------------------------------------------------------

SCENE_HINTS = {
    "visit_preparation": "访前准备",
    "pain_point_discovery": "痛点挖掘",
    "customer_feedback": "客户反馈",
    "product_demo": "产品演示",
    "post_visit": "访后复盘",
    "next_step": "下一步",
    "closing_or_pricing": "成交/报价",
    "dormant_customer": "沉睡客户",
    "frustration": "情绪受挫",
    "teaching_others": "教他人",
}

# 实时引导强度
GUIDANCE_LEVEL_SPECIFIC = "specific"
GUIDANCE_LEVEL_DIRECTIONAL = "directional"
GUIDANCE_LEVEL_SUPPRESSED = "suppressed"
GUIDANCE_LEVEL_VALUES = {GUIDANCE_LEVEL_SPECIFIC, GUIDANCE_LEVEL_DIRECTIONAL, GUIDANCE_LEVEL_SUPPRESSED}

# 引导强度分数阈值（Phase 4）
GUIDANCE_SPECIFIC_MAX_SCORE = 40      # score < 40 → specific
GUIDANCE_DIRECTIONAL_MAX_SCORE = 70   # 40..70 → directional；> 70 → suppress

# 每日积分上限与默认实时引导/奖励通知上限（Phase 3/4 用）
DAILY_POINTS_CAP = 50
DEFAULT_DAILY_REALTIME_GUIDANCE_LIMIT = 3
DEFAULT_DAILY_REWARD_NOTIFICATION_LIMIT = 3

# 证据片段最大字符数（coach_settings.evidence_quote_max_chars 默认值）
DEFAULT_EVIDENCE_QUOTE_MAX_CHARS = 160

# 报告类型
REPORT_SCORES = "scores"
REPORT_LEVEL = "level"
REPORT_ICEBERG = "iceberg"
REPORT_MILESTONES = "milestones"
REPORT_REWARDS = "rewards"
REPORT_FULL = "full"
REPORT_TYPES = {REPORT_SCORES, REPORT_LEVEL, REPORT_ICEBERG, REPORT_MILESTONES, REPORT_REWARDS, REPORT_FULL}
