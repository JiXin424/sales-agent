"""每日评估 JSON 校验与归一化的单元测试。"""

from __future__ import annotations

from sales_agent.coach import json_validator as V


def _valid_payload():
    return {
        "data_sufficiency": "sufficient",
        "summary": "今日表现稳健",
        "dimensions": {
            "customer_identification": {
                "delta": 1,
                "reason": "补全了决策链",
                "evidence_quotes": ["客户提到老板还要看"],
                "source_conversation_ids": ["c1"],
                "confidence": 0.8,
            },
            "needs_discovery": {
                "delta": 0,
                "reason": "",
                "evidence_quotes": [],
                "confidence": 0.2,
            },
            "value_delivery": {
                "delta": 2,
                "reason": "帮客户算账",
                "evidence_quotes": ["按你的规模一年能省 30 万"],
                "source_conversation_ids": ["c1"],
                "confidence": 0.9,
            },
            "trust_building": {"delta": 0, "reason": "", "evidence_quotes": [], "confidence": 0.1},
            "deal_advancement": {"delta": -1, "reason": "未约定下一步",
                                  "evidence_quotes": ["那再看看吧"], "source_conversation_ids": ["c2"],
                                  "confidence": 0.6},
            "review_reflection": {"delta": 0, "reason": "", "evidence_quotes": [], "confidence": 0.0},
        },
        "iceberg": {
            "surface_blocks": [
                {"type": "value_block", "severity": "high", "description": "价值未量化",
                 "evidence_quotes": ["再看看"], "source_conversation_ids": ["c2"]},
            ],
            "deep_blocks": [],
        },
        "next_growth_suggestion": "下次见客户前先量化价值。",
    }


# ----- clamp / delta -----


def test_clamp_and_delta_helpers():
    assert V.clamp_score(150) == 100
    assert V.clamp_score(-1) == 0
    assert V.is_valid_delta(2) is True
    assert V.is_valid_delta(4) is False


# ----- validate_evaluation_payload: happy path -----


def test_valid_payload_normalizes_to_success():
    res = V.validate_evaluation_payload(_valid_payload())
    assert res.status == "success"
    assert res.payload is not None
    dims = res.payload["dimensions"]
    assert set(dims.keys()) == {
        "customer_identification", "needs_discovery", "value_delivery",
        "trust_building", "deal_advancement", "review_reflection",
    }
    # 归一化后保留 delta 与证据
    assert dims["value_delivery"]["delta"] == 2
    assert dims["value_delivery"]["evidence_quotes"] == ["按你的规模一年能省 30 万"]


# ----- missing dimension -> failed -----


def test_missing_dimension_invalidates():
    payload = _valid_payload()
    del payload["dimensions"]["needs_discovery"]
    res = V.validate_evaluation_payload(payload)
    assert res.status == "failed"
    assert "needs_discovery" in res.reason or "dimension" in res.reason.lower()


# ----- delta out of range -> failed -----


def test_delta_out_of_range_invalidates():
    payload = _valid_payload()
    payload["dimensions"]["customer_identification"]["delta"] = 5
    res = V.validate_evaluation_payload(payload)
    assert res.status == "failed"


def test_delta_non_integer_invalidates():
    payload = _valid_payload()
    payload["dimensions"]["customer_identification"]["delta"] = "1"
    res = V.validate_evaluation_payload(payload)
    assert res.status == "failed"


# ----- non-zero delta missing evidence -> force to 0 -----


def test_nonzero_delta_missing_evidence_forced_to_zero():
    payload = _valid_payload()
    payload["dimensions"]["value_delivery"] = {
        "delta": 2, "reason": "", "evidence_quotes": [], "confidence": 0.0,
    }
    res = V.validate_evaluation_payload(payload)
    assert res.status == "success"
    assert res.payload["dimensions"]["value_delivery"]["delta"] == 0


def test_nonzero_delta_missing_confidence_forced_to_zero():
    payload = _valid_payload()
    payload["dimensions"]["value_delivery"] = {
        "delta": 2, "reason": "ok", "evidence_quotes": ["q"],  # 无 confidence
    }
    res = V.validate_evaluation_payload(payload)
    assert res.status == "success"
    assert res.payload["dimensions"]["value_delivery"]["delta"] == 0


# ----- data_sufficiency insufficient -> skipped -----


def test_insufficient_data_is_skipped():
    payload = _valid_payload()
    payload["data_sufficiency"] = "insufficient"
    res = V.validate_evaluation_payload(payload)
    assert res.status == "skipped"


# ----- malformed payload -----


def test_non_dict_payload_fails():
    res = V.validate_evaluation_payload("not json")
    assert res.status == "failed"


def test_dimensions_not_object_fails():
    payload = _valid_payload()
    payload["dimensions"] = []
    res = V.validate_evaluation_payload(payload)
    assert res.status == "failed"


# ----- iceberg normalization -----


def test_iceberg_caps_and_filters_types():
    payload = _valid_payload()
    payload["iceberg"] = {
        "surface_blocks": [
            {"type": "value_block", "severity": "high", "description": "d1",
             "evidence_quotes": ["q"], "source_conversation_ids": ["c1"]},
            {"type": "unknown_block", "description": "filtered out"},
        ] + [
            {"type": "needs_block", "severity": "low", "description": f"d{i}",
             "evidence_quotes": ["q"], "source_conversation_ids": ["c1"]}
            for i in range(10)
        ],
        "deep_blocks": [
            {"type": "motivation_block", "severity": "medium", "description": "x",
             "evidence_quotes": ["q"], "source_conversation_ids": ["c1"]},
        ] + [
            {"type": "confidence_block", "description": f"y{i}"}
            for i in range(10)
        ],
    }
    res = V.validate_evaluation_payload(payload)
    assert res.status == "success"
    surf = res.payload["iceberg"]["surface_blocks"]
    deep = res.payload["iceberg"]["deep_blocks"]
    # 过滤未知类型 + 封顶
    assert len(surf) <= 5
    assert all(b["type"] != "unknown_block" for b in surf)
    assert len(deep) <= 4
    # severity 默认 medium
    for b in deep:
        assert b["severity"] in {"low", "medium", "high"}


def test_iceberg_missing_handled_gracefully():
    payload = _valid_payload()
    del payload["iceberg"]
    res = V.validate_evaluation_payload(payload)
    assert res.status == "success"
    assert res.payload["iceberg"]["surface_blocks"] == []


# ----- build score deltas from validated payload -----


def test_build_score_deltas_from_payload():
    res = V.validate_evaluation_payload(_valid_payload())
    deltas = V.build_score_deltas(res.payload)
    assert deltas["customer_identification"] == 1
    assert deltas["value_delivery"] == 2
    assert deltas["deal_advancement"] == -1
    assert deltas["needs_discovery"] == 0
