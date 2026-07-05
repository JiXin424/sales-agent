from sales_agent.core.config import Settings


def test_guided_flows_defaults_enabled_in_shanghai():
    settings = Settings()
    assert settings.guided_flows.enabled is True
    assert settings.guided_flows.timezone == "Asia/Shanghai"


def test_guided_flows_env_override(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("guided_flows:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("GUIDED_FLOWS_ENABLED", "false")
    settings = Settings.from_yaml(config_file)
    assert settings.guided_flows.enabled is False
