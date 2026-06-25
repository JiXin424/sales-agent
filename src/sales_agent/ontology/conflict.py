from __future__ import annotations

from sales_agent.ontology.schemas import FactCandidate


HIGH_RISK_PREDICATES = {
    "certified_for",
    "certification",
    "policy_clause",
    "delivery_commitment",
    "price_commitment",
    "compliance_boundary",
    "technical_metric",
}

MEDIUM_RISK_PREDICATES = {
    "price",
    "pricing",
    "case_metric",
    "competitor_claim",
    "performance_metric",
}


def classify_fact_risk(fact: FactCandidate) -> str:
    predicate = fact.predicate.lower()
    text = f"{fact.value or ''} {fact.object_name or ''}".lower()
    if predicate in HIGH_RISK_PREDICATES:
        return "high"
    if any(word in text for word in ("保证", "最低价", "一周上线", "三类证", "政策规定")):
        return "high"
    if predicate in MEDIUM_RISK_PREDICATES:
        return "medium"
    return "low"


def merge_status_for_risk(risk_level: str) -> str:
    return "active" if risk_level == "low" else "pending_review"
