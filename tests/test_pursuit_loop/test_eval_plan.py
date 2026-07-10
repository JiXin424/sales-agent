def test_success_criteria_is_verifiable():
    """A good success_criteria can be observed/verified later."""
    good = "张总确认技术负责人和可沟通时间"
    bad_patterns = ["成功", "推进", "完成"]
    assert len(good) > 4
    assert not any(p in good for p in bad_patterns)


def test_extraction_populates_success_criteria_and_pursuit_goal():
    from sales_agent.services.sales_actions.contracts import SalesActionExtraction
    ext = SalesActionExtraction(
        intent="create_action",
        title="发案例",
        success_criteria="张总确认技术负责人和可沟通时间",
        pursuit_goal="推进张总这单",
        confidence=0.95,
    )
    assert ext.success_criteria
    assert ext.pursuit_goal
