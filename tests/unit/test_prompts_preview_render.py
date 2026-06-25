"""验证 preview 的 _render_template_for_category 按 category 正确渲染各层 prompt（阶段2）。"""

from sales_agent.api.routes.prompts import _render_template_for_category


def test_render_task_category():
    rendered = _render_template_for_category(
        "task", "knowledge_qa",
        "MSG={message} CTX={context_block} RB={retrieval_block}",
        message="hi", context={"industry": "SaaS"}, sample_variables=None,
    )
    assert "MSG=hi" in rendered
    assert "SaaS" in rendered  # context_block 含 industry
    assert "RB=" in rendered  # retrieval_block 预览空串


def test_render_risk_category():
    rendered = _render_template_for_category(
        "risk", "risk_check", "Q={message} A={answer}",
        message="问", context=None, sample_variables={"answer": "答"},
    )
    assert "Q=问" in rendered
    assert "A=答" in rendered


def test_render_router_category():
    rendered = _render_template_for_category(
        "router", "task_router", "IN={message}",
        message="xxx", context=None, sample_variables=None,
    )
    assert "IN=xxx" in rendered


def test_render_missing_placeholder_is_tolerated():
    """预览渲染对缺失占位符宽容（返回空串，不抛 KeyError）。"""
    rendered = _render_template_for_category(
        "risk", "risk_check", "Q={message} A={answer} EXTRA={missing}",
        message="m", context=None, sample_variables={},
    )
    assert "Q=m" in rendered
    assert "EXTRA=" in rendered  # missing 渲染为空串
