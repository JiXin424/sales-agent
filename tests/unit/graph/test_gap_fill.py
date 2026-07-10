"""gap_fill：从 search_terms 找出 KB 未命中的实体候选。"""

from sales_agent.graph.retrieval.gap_fill import (
    compute_missing,
    is_covered,
    is_entity_like,
)


def test_is_entity_like_drops_generic_words():
    assert is_entity_like("区别") is False
    assert is_entity_like("对比") is False
    assert is_entity_like("VS") is False  # 大小写不敏感
    assert is_entity_like("怎么样") is False
    assert is_entity_like("的") is False  # 太短 + 停用词
    assert is_entity_like("全品C") is True


def test_is_covered_substring_either_direction():
    assert is_covered("全品C", ["全品C旗舰版"]) is True   # term 是 name 子串
    assert is_covered("全品C旗舰版", ["全品C"]) is True   # name 是 term 子串
    assert is_covered("X品牌", ["全品C"]) is False
    assert is_covered("X品牌", []) is False


def test_compute_missing_basic_split():
    # 全品C 命中、X品牌 缺失
    missing = compute_missing(["全品C", "X品牌"], ["全品C"], max_n=2)
    assert missing == ["X品牌"]


def test_compute_missing_filters_generic_terms():
    # 「区别」被剔除，不进 missing
    missing = compute_missing(["全品C", "区别"], [], max_n=2)
    assert missing == ["全品C"]


def test_compute_missing_caps_at_max_n():
    missing = compute_missing(["产品A", "产品B", "产品C"], [], max_n=2)
    assert missing == ["产品A", "产品B"]


def test_compute_missing_preserves_order_and_dedupes():
    missing = compute_missing(["AA", "AA", "BB"], [], max_n=5)
    assert missing == ["AA", "BB"]


def test_compute_missing_all_covered_returns_empty():
    assert compute_missing(["全品C"], ["全品C"], max_n=2) == []
