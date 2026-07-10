"""验证 BUILTIN_PROMPTS 注册表的完整性。

确保随代码发布的默认 prompt 全部登记，使 PromptRegistry 三级回退
（Agent 绑定 → tenant active 版本 → 内置常量）的兜底层可用。
"""

from __future__ import annotations

from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS, get_builtin


def test_builtin_prompts_contains_new_six():
    """Task 10 补进来的 6 个漏网 prompt 全部注册到 BUILTIN_PROMPTS。"""
    keys = [
        ("task", "memory_extraction"),
        ("router", "topic_restore_resolver"),
        ("router", "scenario_matcher"),
        ("system", "media_vision_system"),
        ("task", "media_vision_user"),
        ("task", "media_audio_transcribe"),
    ]
    for category, key in keys:
        p = get_builtin(category, key)
        assert p is not None, f"BUILTIN_PROMPTS 缺 {category}/{key}"
        assert p.template and p.template.strip(), f"{category}/{key} template 为空"


def test_required_placeholders_match_prompt_content():
    """注册时填的 required_placeholders 与模板里真实 {placeholder} 一致。"""
    import re

    cases = [
        ("task", "memory_extraction", ()),
        ("router", "topic_restore_resolver", ()),
        ("router", "scenario_matcher", ("questions_json",)),
        ("system", "media_vision_system", ()),
        ("task", "media_vision_user", ()),
        ("task", "media_audio_transcribe", ()),
    ]
    # 单花括号占位符：{name}（排除 {{ }} 这种转义的成对花括号）
    placeholder_re = re.compile(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})")

    for category, key, expected in cases:
        p = get_builtin(category, key)
        assert p is not None, f"BUILTIN_PROMPTS 缺 {category}/{key}"
        found = set(placeholder_re.findall(p.template))
        assert set(expected) == found, (
            f"{category}/{key} 占位符不一致：注册={expected} 模板实际={found}"
        )


def test_builtin_prompts_unique_keys():
    """同一 (category, key) 不应重复登记。"""
    seen = set()
    for b in BUILTIN_PROMPTS:
        pair = (b.category, b.key)
        assert pair not in seen, f"重复登记 {pair}"
        seen.add(pair)
