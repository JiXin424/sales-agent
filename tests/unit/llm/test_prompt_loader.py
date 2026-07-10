import pytest, yaml
from sales_agent.llm.prompt_loader import PromptTemplate, load_prompts, get_prompt
from sales_agent.core.config import get_settings

def _write_yaml(tmp_path, data):
    p = tmp_path / "prompts.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True))
    return str(p)

def test_load_valid_and_get(tmp_path):
    data = {"router": {"test_key": {"template": "hello {name}", "placeholders": ["name"], "description": "test"}}}
    load_prompts(_write_yaml(tmp_path, data))
    p = get_prompt("router", "test_key")
    assert p.template == "hello {name}"
    assert p.placeholders == ("name",)
    assert p.description == "test"

def test_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, RuntimeError)):
        load_prompts(str(tmp_path / "nope.yaml"))

def test_empty_template_raises(tmp_path):
    data = {"task": {"x": {"template": "", "placeholders": [], "description": ""}}}
    with pytest.raises(ValueError):
        load_prompts(_write_yaml(tmp_path, data))

def test_missing_template_field_raises(tmp_path):
    data = {"task": {"x": {"placeholders": [], "description": ""}}}
    with pytest.raises(ValueError):
        load_prompts(_write_yaml(tmp_path, data))

def test_get_before_load_raises():
    import sales_agent.llm.prompt_loader as m
    m._PROMPTS = None
    with pytest.raises(RuntimeError):
        get_prompt("router", "x")

def test_unknown_key_raises(tmp_path):
    data = {"router": {"test_key": {"template": "hello", "placeholders": [], "description": ""}}}
    load_prompts(_write_yaml(tmp_path, data))
    with pytest.raises(KeyError):
        get_prompt("router", "nonexistent")

def test_load_real_prompts_all_39_keys(tmp_path):
    """加载真实 config/llm_config.yaml，验证全部 39 个 category+key 可获取且 template 非空。"""
    from pathlib import Path
    real = Path(get_settings().llm_config_path)
    if not real.exists():
        pytest.skip("config/llm_config.yaml not found")
    load_prompts(str(real))
    expected_categories = {
        "task": ["memory_extraction", "emotional_support", "knowledge_qa", "script_generation",
                 "objection_handling", "conversation_review", "general_sales_coaching",
                 "visit_preparation", "follow_up_planning", "customer_context_summary",
                 "deal_advancement", "conversation_scoring", "post_visit_review",
                 "media_vision_user", "media_audio_transcribe"],
        "system": ["system_constraint", "media_vision_system"],
        "router": ["task_router", "context_resolver", "clarification_resolver", "evidence_router",
                   "topic_restore_resolver", "scenario_matcher"],
        "risk": ["risk_check"],
        "coach": ["coach_daily_eval", "coach_daily_eval_system", "coach_sw_system",
                  "coach_sb_system", "coach_sw_card", "coach_sb_split", "coach_sb_card"],
        "web": ["web_analysis"],
        "knowledge": ["entity_extraction", "fact_extraction", "image_interpret",
                      "md_optimize_system", "md_optimize_user", "ontology_term_extractor",
                      "ontology_response"],
    }
    count = 0
    for cat, keys in expected_categories.items():
        for key in keys:
            p = get_prompt(cat, key)
            assert p.template, f"{cat}.{key} template is empty"
            count += 1
    assert count == 39, f"Expected 39 prompts, got {count}"
