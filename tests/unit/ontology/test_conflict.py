from sales_agent.ontology.conflict import classify_fact_risk, merge_status_for_risk
from sales_agent.ontology.schemas import FactCandidate


def test_high_risk_for_certification_and_price_commitment():
    cert = FactCandidate(subject_name="产品A", predicate="certified_for", value="三类证")
    price = FactCandidate(subject_name="产品A", predicate="price_commitment", value="保证最低价")
    assert classify_fact_risk(cert) == "high"
    assert classify_fact_risk(price) == "high"


def test_medium_risk_for_price_range():
    fact = FactCandidate(subject_name="产品A", predicate="price", value="10-20万")
    assert classify_fact_risk(fact) == "medium"


def test_low_risk_for_alias_or_description():
    fact = FactCandidate(subject_name="产品A", predicate="alias", value="A产品")
    assert classify_fact_risk(fact) == "low"


def test_merge_status_for_risk():
    assert merge_status_for_risk("low") == "active"
    assert merge_status_for_risk("medium") == "pending_review"
    assert merge_status_for_risk("high") == "pending_review"
