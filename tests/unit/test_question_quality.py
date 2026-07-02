"""Test QualityReport: dedup, per-document caps, quarantine."""

from eval.question_quality import assess_quality, QualityReport


def test_no_issues_for_clean_suite():
    questions = [
        {"input_text": "What is A?", "source_document_ids": ["d1"], "source_fact_ids": ["f1"]},
        {"input_text": "What is B?", "source_document_ids": ["d2"], "source_fact_ids": ["f2"]},
    ]
    report = assess_quality(questions)
    assert report.quarantined == 0
    assert report.accepted == 2


def test_duplicate_inputs_are_quarantined():
    questions = [
        {"input_text": "What is A?", "source_document_ids": ["d1"], "source_fact_ids": ["f1"]},
        {"input_text": "What is A?", "source_document_ids": ["d1"], "source_fact_ids": ["f1"]},
    ]
    report = assess_quality(questions)
    assert report.quarantined >= 1
    assert report.duplicate_count >= 1


def test_over_document_cap_is_quarantined():
    questions = [
        {"input_text": f"Q{i}", "source_document_ids": ["d1"], "source_fact_ids": [f"f{i}"]}
        for i in range(10)  # 10 questions from same document (>5 cap)
    ]
    report = assess_quality(questions)
    assert report.quarantined > 0
    assert "over_document_cap" in report.issues[0]
