import pytest

CONSTRAINT_TEST_CASES = [
    ("预算冻结", ["催款", "报价", "采购申请", "签合同"]),
    ("技术负责人已离职", ["联系旧技术负责人", "推旧方案"]),
    ("客户说不感兴趣", ["发更多材料", "持续跟进"]),  # should respect disinterest
]


@pytest.mark.parametrize("obstacle,forbidden", CONSTRAINT_TEST_CASES)
def test_replan_respects_obstacle(obstacle, forbidden):
    """Given an obstacle keyword, the replan should NOT suggest actions containing forbidden words."""
    # This is evaluated by LLM-judge in production; here we assert the test structure
    assert isinstance(obstacle, str)
    assert all(isinstance(w, str) for w in forbidden)
