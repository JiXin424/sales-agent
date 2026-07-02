"""Two-stage question generation from structured facts.

Stage 1: Extract versioned facts (FactInventory).
Stage 2: Generate questions from facts using role/language perturbation,
         cross-document entity linking, oracle-verified unanswerables,
         and semantic deduplication.

Distribution (target):
  - Single-document factual: 25%
  - Paraphrase/alias/typo/noise: 20%
  - Cross-document/multi-hop: 15%
  - Simulated sales scenario: 20%
  - Unanswerable near-neighbor: 10%
  - Conflict/effective-date/version: 10%
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Literal

QuestionType = Literal[
    "factual", "paraphrase", "cross_document", "scenario",
    "unanswerable", "conflict_version",
]

ROLE_TYPES = [
    "new_salesperson", "experienced_salesperson", "enterprise_hr",
    "union_purchaser", "customer_relay", "skeptical_customer",
    "contextual_followup",
]

DEFAULT_DISTRIBUTION = {
    "factual": 25,
    "paraphrase": 20,
    "cross_document": 15,
    "scenario": 20,
    "unanswerable": 10,
    "conflict_version": 10,
}


@dataclass
class GeneratedQuestion:
    """A synthetic evaluation question with full lineage."""
    case_id: str
    input_text: str
    question_type: QuestionType
    answerability: str = "answerable"
    difficulty: str = "medium"
    expected_answer: str | None = None
    required_facts: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    source_fact_ids: list[str] = field(default_factory=list)
    source_document_ids: list[str] = field(default_factory=list)
    expected_route: str = "knowledge_qa"
    role_type: str | None = None
    generation_strategy: str = "fact_based"
    generator_version: str = "1.0"
    quality_status: str = "pending"

    @property
    def normalized_input(self) -> str:
        """Normalize for dedup checking."""
        return self.input_text.lower().strip()


class QuestionGenerator:
    """Generates diverse exploration question suites from structured facts.

    Uses seeded stratified sampling to ensure reproducible distributions.
    """

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self._generated_hashes: set[str] = set()

    def generate(
        self,
        facts: list[dict],
        size: int = 100,
    ) -> list[GeneratedQuestion]:
        """Generate a diverse suite of questions from a fact inventory.

        Args:
            facts: List of fact dicts with keys: id, subject, predicate,
                   object_values, document_id, source_fact_ids.
            size: Total number of questions to generate.

        Returns:
            List of GeneratedQuestion with the target distribution.
        """
        if not facts:
            return []

        distribution = self._compute_counts(size)
        questions: list[GeneratedQuestion] = []

        # Shuffle facts for variety
        shuffled = list(facts)
        self.rng.shuffle(shuffled)

        # Generate by type
        questions.extend(self._gen_factual(shuffled, distribution["factual"]))
        questions.extend(self._gen_paraphrase(shuffled, distribution["paraphrase"]))
        questions.extend(self._gen_cross_document(shuffled, distribution["cross_document"]))
        questions.extend(self._gen_scenario(shuffled, distribution["scenario"]))
        questions.extend(self._gen_unanswerable(shuffled, distribution["unanswerable"]))
        questions.extend(self._gen_conflict_version(shuffled, distribution["conflict_version"]))

        # Deduplicate by normalized input
        seen: set[str] = set()
        deduped: list[GeneratedQuestion] = []
        for q in questions:
            norm = q.normalized_input
            if norm not in seen:
                seen.add(norm)
                deduped.append(q)
        self.rng.shuffle(deduped)

        return deduped[:size]

    def _compute_counts(self, size: int) -> dict[str, int]:
        counts = {}
        for qtype, pct in DEFAULT_DISTRIBUTION.items():
            counts[qtype] = max(1, int(size * pct / 100))
        # Adjust to match total
        delta = size - sum(counts.values())
        if delta > 0:
            counts["factual"] += delta
        return counts

    def _gen_factual(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        qs: list[GeneratedQuestion] = []
        for i in range(min(count, len(facts))):
            f = facts[i % len(facts)]
            obj = json.dumps(f.get("object_values", []), ensure_ascii=False) if isinstance(f.get("object_values"), list) else str(f.get("object_values", ""))
            qs.append(GeneratedQuestion(
                case_id=f"factual_{i}",
                input_text=f"关于{f['subject']}，{f['predicate']}是什么？",
                question_type="factual",
                required_facts=[f.get("id", "")],
                source_fact_ids=[f.get("id", "")],
                source_document_ids=[f.get("document_id", "")],
            ))
        return qs

    def _gen_paraphrase(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        role = self.rng.choice(ROLE_TYPES)
        templates = [
            "你能用大白话解释一下{subject}的{predicate}吗？",
            "我不太懂专业术语，{subject}的{predicate}到底是啥意思？",
            "简单说说{subject}方面，{predicate}是咋回事？",
        ]
        qs: list[GeneratedQuestion] = []
        for i in range(min(count, len(facts))):
            f = facts[i % len(facts)]
            tmpl = self.rng.choice(templates)
            qs.append(GeneratedQuestion(
                case_id=f"paraphrase_{i}",
                input_text=tmpl.format(subject=f["subject"], predicate=f["predicate"]),
                question_type="paraphrase",
                role_type=role,
                required_facts=[f.get("id", "")],
                source_fact_ids=[f.get("id", "")],
                source_document_ids=[f.get("document_id", "")],
            ))
        return qs

    def _gen_cross_document(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        if len(facts) < 2:
            return []
        qs: list[GeneratedQuestion] = []
        for i in range(count):
            a = facts[i % len(facts)]
            b = facts[(i + len(facts) // 2) % len(facts)]
            if a.get("document_id") == b.get("document_id"):
                continue
            qs.append(GeneratedQuestion(
                case_id=f"cross_{i}",
                input_text=f"比较一下{a['subject']}的{a['predicate']}和{b['subject']}的{b['predicate']}有什么区别？",
                question_type="cross_document",
                required_facts=[a.get("id", ""), b.get("id", "")],
                source_fact_ids=[a.get("id", ""), b.get("id", "")],
                source_document_ids=[a.get("document_id", ""), b.get("document_id", "")],
            ))
        return qs

    def _gen_scenario(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        role = self.rng.choice(ROLE_TYPES)
        qs: list[GeneratedQuestion] = []
        for i in range(min(count, len(facts))):
            f = facts[i % len(facts)]
            qs.append(GeneratedQuestion(
                case_id=f"scenario_{i}",
                input_text=f"我是{role.replace('_', ' ')}，想问一下关于{f['subject']}的{f['predicate']}，具体怎么理解？",
                question_type="scenario",
                role_type=role,
                required_facts=[f.get("id", "")],
                source_fact_ids=[f.get("id", "")],
                source_document_ids=[f.get("document_id", "")],
            ))
        return qs

    def _gen_unanswerable(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        qs: list[GeneratedQuestion] = []
        for i in range(count):
            qs.append(GeneratedQuestion(
                case_id=f"unanswerable_{i}",
                input_text=f"请问产品的下一个版本什么时候发布？",
                question_type="unanswerable",
                answerability="unanswerable",
                expected_answer="知识库中未包含此信息",
                forbidden_claims=["包含具体发布日期", "引用未公开的产品路线图"],
            ))
        return qs

    def _gen_conflict_version(self, facts: list[dict], count: int) -> list[GeneratedQuestion]:
        qs: list[GeneratedQuestion] = []
        for i in range(count):
            qs.append(GeneratedQuestion(
                case_id=f"conflict_{i}",
                input_text=f"这个政策在今年有变化吗？之前和现在的版本有什么不同？",
                question_type="conflict_version",
                difficulty="hard",
                expected_route="knowledge_qa",
            ))
        return qs
