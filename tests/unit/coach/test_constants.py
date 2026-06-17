"""教练常量单元测试。"""

from __future__ import annotations

from sales_agent.coach import constants as C


def test_six_dimensions_with_stable_keys_and_labels():
    assert len(C.DIMENSIONS) == 6
    keys = [k for k, _ in C.DIMENSIONS]
    assert keys == [
        "customer_identification",
        "needs_discovery",
        "value_delivery",
        "trust_building",
        "deal_advancement",
        "review_reflection",
    ]
    # 每个 key 都有中文标签
    for k in keys:
        assert C.dimension_label(k)
    assert C.dimension_label("unknown_key") == "unknown_key"


def test_score_clamp_boundaries():
    assert C.clamp_score(-5) == C.MIN_SCORE == 0
    assert C.clamp_score(0) == 0
    assert C.clamp_score(50) == 50
    assert C.clamp_score(100) == 100
    assert C.clamp_score(150) == C.MAX_SCORE == 100
    assert C.INITIAL_SCORE == 50


def test_delta_validation_integers_only_in_range():
    for d in (-3, -2, -1, 0, 1, 2, 3):
        assert C.is_valid_delta(d) is True
    assert C.is_valid_delta(4) is False
    assert C.is_valid_delta(-4) is False
    # 非整数
    assert C.is_valid_delta(1.5) is False
    assert C.is_valid_delta("1") is False
    assert C.is_valid_delta(None) is False
    # 布尔不是合法 delta
    assert C.is_valid_delta(True) is False
    assert C.is_valid_delta(False) is False


def test_milestone_counts_72_plus_12_equals_84():
    assert C.MILESTONE_THRESHOLDS == [5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert C.DIMENSION_MILESTONE_COUNT == 72
    assert C.ALL_DIMENSION_MILESTONE_COUNT == 12
    assert C.TOTAL_MILESTONE_COUNT == 84


def test_rank_for_points_thresholds():
    assert C.rank_for_points(0)[0] == "bronze"
    assert C.rank_for_points(99)[0] == "bronze"
    assert C.rank_for_points(100)[0] == "silver"
    assert C.rank_for_points(300)[0] == "samurai"
    assert C.rank_for_points(599)[0] == "samurai"
    assert C.rank_for_points(600)[0] == "master"
    assert C.rank_for_points(1000)[0] == "king"
    assert C.rank_for_points(5000)[0] == "king"
    key, name = C.rank_for_points(100)
    assert name == "白银"


def test_level_for_milestones_table():
    assert C.level_for_milestones(0) == 0
    assert C.level_for_milestones(1) == 1
    assert C.level_for_milestones(5) == 1
    assert C.level_for_milestones(6) == 2
    assert C.level_for_milestones(12) == 2
    assert C.level_for_milestones(13) == 3
    assert C.level_for_milestones(79) == 9
    assert C.level_for_milestones(80) == 10
    assert C.level_for_milestones(200) == 10


def test_iceberg_type_and_report_constants():
    assert len(C.ICEBERG_SURFACE_TYPES) == 5
    assert len(C.ICEBERG_DEEP_TYPES) == 4
    assert C.MAX_SURFACE_BLOCKS == 5
    assert C.MAX_DEEP_BLOCKS == 4
    assert C.REPORT_TYPES == {"scores", "level", "iceberg", "milestones", "rewards", "full"}
    assert C.EVAL_STATUS_VALUES == {"success", "skipped", "failed", "dry_run"}
