"""Scenario Coach: preset sales-scenario Q&A interception.

Self-contained package. Depends only on: settings (ScenarioCoachConfig),
a chat_model passed by the graph node, and the answer_dict contract.
"""

from sales_agent.scenarios.loader import (
    ScenarioRegistry,
    get_scenario_registry,
    parse_scenario_md,
)
from sales_agent.scenarios.models import (
    AnswerSection,
    Scenario,
    ScenarioMatchDecision,
    ScenarioQuestion,
)

__all__ = [
    "AnswerSection",
    "Scenario",
    "ScenarioMatchDecision",
    "ScenarioQuestion",
    "ScenarioRegistry",
    "get_scenario_registry",
    "parse_scenario_md",
]
