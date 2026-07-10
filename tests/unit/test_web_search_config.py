"""WebSearchConfig.max_gap_entities 默认值与 env 覆盖。"""

from sales_agent.core.config import WebSearchConfig


def test_max_gap_entities_default():
    assert WebSearchConfig().max_gap_entities == 2


def test_max_gap_entities_override():
    cfg = WebSearchConfig(max_gap_entities=3)
    assert cfg.max_gap_entities == 3
