"""验证 WEB_ANALYSIS_PROMPT 模板与 BUILTIN_PROMPTS 注册。"""

from sales_agent.prompts.web_analysis_prompt import WEB_ANALYSIS_PROMPT
from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS, get_builtin


def test_web_analysis_has_placeholder():
    assert "{search_results}" in WEB_ANALYSIS_PROMPT


def test_web_analysis_has_confidence_rules():
    """旧 web_llm 的置信度判定规则。"""
    assert "high" in WEB_ANALYSIS_PROMPT
    assert "medium" in WEB_ANALYSIS_PROMPT
    assert "low" in WEB_ANALYSIS_PROMPT


def test_web_analysis_has_json_schema():
    assert '"analysis"' in WEB_ANALYSIS_PROMPT
    assert '"confidence"' in WEB_ANALYSIS_PROMPT
    assert '"constraints"' in WEB_ANALYSIS_PROMPT


def test_web_analysis_registered_in_builtin():
    b = get_builtin("web", "web_analysis")
    assert b is not None
    assert "search_results" in b.required_placeholders


def test_web_analysis_formats():
    rendered = WEB_ANALYSIS_PROMPT.format(search_results="某网页内容")
    assert "某网页内容" in rendered
