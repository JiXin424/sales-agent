"""Sales Stage Taxonomy 单元测试。"""

import pytest

from sales_agent.sales_stages import (
    SALES_STAGES,
    ALL_STAGES,
    get_stage,
    validate_stage,
)


class TestSalesStageTaxonomy:
    """验证销售阶段分类法完整性。"""

    def test_all_stages_have_required_fields(self):
        """每个阶段必须有 display、description、typical_actions。"""
        for stage_id, stage_info in SALES_STAGES.items():
            assert "display" in stage_info, f"Stage {stage_id} missing 'display'"
            assert "description" in stage_info, f"Stage {stage_id} missing 'description'"
            assert "typical_actions" in stage_info, f"Stage {stage_id} missing 'typical_actions'"
            assert isinstance(stage_info["display"], str)
            assert isinstance(stage_info["description"], str)
            assert isinstance(stage_info["typical_actions"], list)
            assert len(stage_info["typical_actions"]) > 0

    def test_required_stages_exist(self):
        """规范要求的 9 个阶段必须存在。"""
        required = [
            "lead_discovery",
            "first_contact",
            "needs_discovery",
            "visit_preparation",
            "proposal",
            "objection",
            "follow_up",
            "deal_closing",
            "post_mortem",
        ]
        for stage_id in required:
            assert stage_id in SALES_STAGES, f"Required stage '{stage_id}' not found"

    def test_all_stages_list_matches_keys(self):
        """ALL_STAGES 列表必须与 SALES_STAGES 键一致。"""
        assert set(ALL_STAGES) == set(SALES_STAGES.keys())

    def test_get_stage_returns_info(self):
        """get_stage 返回正确的阶段信息。"""
        stage = get_stage("visit_preparation")
        assert stage is not None
        assert stage["display"] == "拜访准备"

    def test_get_stage_returns_none_for_unknown(self):
        """get_stage 对未知 ID 返回 None。"""
        assert get_stage("nonexistent_stage") is None

    def test_validate_stage(self):
        """validate_stage 正确识别有效和无效阶段。"""
        assert validate_stage("lead_discovery") is True
        assert validate_stage("nonexistent") is False

    def test_stage_ids_are_stable_identifiers(self):
        """阶段 ID 应该是 snake_case 稳定标识符。"""
        import re
        for stage_id in SALES_STAGES:
            assert re.match(r"^[a-z][a-z0-9_]*$", stage_id), \
                f"Stage ID '{stage_id}' is not valid snake_case"

    def test_display_names_are_chinese(self):
        """阶段显示名应该是中文。"""
        for stage_id, stage_info in SALES_STAGES.items():
            display = stage_info["display"]
            # 至少包含一个中文字符
            assert any("一" <= ch <= "鿿" for ch in display), \
                f"Stage {stage_id} display '{display}' should contain Chinese"
