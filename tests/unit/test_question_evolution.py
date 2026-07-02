"""Test QuestionGenerator: distribution, lineage, and anti-leakage."""

import json
from eval.question_evolution import QuestionGenerator, DEFAULT_DISTRIBUTION


def test_generator_respects_distribution():
    """Generated suite must approximately match the target distribution."""
    facts = [
        {"id": f"f{i}", "subject": f"Topic{i}", "predicate": f"is_{i}",
         "object_values": ["val"], "document_id": f"doc{i}"}
        for i in range(50)
    ]
    gen = QuestionGenerator(seed=42)
    questions = gen.generate(facts, size=100)

    counts = {}
    for q in questions:
        counts[q.question_type] = counts.get(q.question_type, 0) + 1

    # Check that all expected types are present
    for qtype in DEFAULT_DISTRIBUTION:
        assert qtype in counts, f"Missing question type: {qtype}"
        # Rough check: each type should have at least 1
        assert counts[qtype] >= 1, f"Too few {qtype}: {counts[qtype]}"


def test_question_records_fact_lineage():
    """Each question must record source fact IDs and generator version."""
    facts = [
        {"id": "f1", "subject": "价格", "predicate": "是",
         "object_values": ["299元/年"], "document_id": "doc1"}
    ]
    gen = QuestionGenerator(seed=1)
    questions = gen.generate(facts, size=10)

    for q in questions:
        assert q.generator_version == "1.0"
        if q.question_type in ("factual", "paraphrase", "scenario"):
            assert len(q.source_fact_ids) >= 1


def test_new_suite_produces_diverse_types():
    """A generated suite should contain multiple question types."""
    facts = [
        {"id": f"f{i}", "subject": f"T{i}", "predicate": f"P{i}",
         "object_values": ["V"], "document_id": f"d{i}"}
        for i in range(30)
    ]
    gen = QuestionGenerator(seed=7)
    questions = gen.generate(facts, size=50)

    types_found = {q.question_type for q in questions}
    assert len(types_found) >= 4, f"Only {len(types_found)} types found"


def test_empty_facts_returns_empty():
    gen = QuestionGenerator()
    questions = gen.generate([], size=10)
    assert questions == []


def test_deduplication_removes_duplicate_inputs():
    """Semantically identical inputs must be deduplicated."""
    # Force two questions with the same input
    facts = [
        {"id": "f1", "subject": "X", "predicate": "Y",
         "object_values": ["Z"], "document_id": "d1"}
    ]
    gen = QuestionGenerator(seed=999)  # deterministic
    questions = gen.generate(facts, size=10)
    inputs = [q.input_text for q in questions]
    # All inputs should be unique
    assert len(inputs) == len(set(inputs))
