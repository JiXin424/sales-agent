"""回归测试：访前/访后卡片模板的双花括号 bug。

历史问题：模板「用户输入」区误用 ``{{message}}`` 双花括号，而
``agent_executor._build_messages`` 用 ``str.format()`` 填充。Python 把
``{{`` 当作转义、输出字面 ``{message}``，导致用户输入、上下文块、检索块
**没有注入 LLM**。JSON 示例区的双花括号是正确的（要输出字面 JSON），保留。

注意：``test_prompt_templates.test_prompt_has_message_placeholder`` 用
``"{message}" in prompt`` 子串匹配，对 ``{{message}}`` 也成立，所以没能
捕获此 bug——这里改为对 ``.format()`` 渲染后的结果做断言。
"""

import pytest

from sales_agent.prompts.visit_preparation import VISIT_PREPARATION_PROMPT
from sales_agent.prompts.post_visit_review import POST_VISIT_REVIEW_PROMPT

_CARD_TEMPLATES = [VISIT_PREPARATION_PROMPT, POST_VISIT_REVIEW_PROMPT]


@pytest.mark.parametrize("template", _CARD_TEMPLATES)
def test_user_input_injected_after_format(template: str):
    """``.format()`` 渲染后，用户输入/上下文/检索块的值必须出现在结果中。"""
    rendered = template.format(
        message="__USER_MSG__",
        context_block="__CTX__",
        retrieval_block="__RAG__",
        retrieval_content="",
    )
    assert "__USER_MSG__" in rendered, "用户输入未注入（疑似双花括号 bug）"
    assert "__CTX__" in rendered, "上下文块未注入"
    assert "__RAG__" in rendered, "检索块未注入"


@pytest.mark.parametrize("template", _CARD_TEMPLATES)
def test_no_double_brace_leftover_in_user_input(template: str):
    """渲染后不应残留字面 ``{{message}}`` 等占位符。"""
    rendered = template.format(
        message="X", context_block="Y", retrieval_block="Z", retrieval_content=""
    )
    assert "{{message}}" not in rendered
    assert "{{context_block}}" not in rendered
    assert "{{retrieval_block}}" not in rendered


@pytest.mark.parametrize("template", _CARD_TEMPLATES)
def test_json_example_block_preserved(template: str):
    """JSON 示例区的双花括号转义必须保留（输出字面 JSON 结构）。"""
    rendered = template.format(
        message="X", context_block="", retrieval_block="", retrieval_content=""
    )
    assert '"summary"' in rendered
    assert '"sections"' in rendered
    assert '"card_type"' in rendered
    # 渲染后 JSON 区应出现字面 ``{``（来自 ``{{`` 转义）
    assert rendered.count("{") >= 2
