from __future__ import annotations

from eval.memory_eval.versions import VersionBundle, collect_version_bundle


def test_version_bundle_has_all_required_fields():
    vb = collect_version_bundle(dataset_version="multiturn_v1", memory_schema_version="0013")
    assert isinstance(vb, VersionBundle)
    for field in (
        "model_version",
        "prompt_version",
        "code_version",
        "dataset_version",
        "knowledge_version",
        "memory_schema_version",
        "generator_version",
    ):
        assert hasattr(vb, field), f"missing {field}"
    assert vb.dataset_version == "multiturn_v1"
    assert vb.memory_schema_version == "0013"


def test_version_bundle_never_returns_none_for_required():
    vb = collect_version_bundle()
    # Required fields must be strings (empty string allowed, None is not).
    assert vb.model_version is not None
    assert vb.code_version is not None
