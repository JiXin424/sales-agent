"""Optimization LangGraph node implementations.

Each node is an async function that reads/writes OptimizationState.
Nodes are idempotent: they check for existing stage output before executing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Node implementations ──────────────────────────────────────────────────

async def baseline_node(state: dict) -> dict:
    """Baseline: record the starting release/eval state.

    In production this would run the baseline eval. For now, it records
    the references passed in state.
    """
    stage = state.get("stage", "")
    if stage == "baseline":
        logger.info("Baseline stage already completed, skipping")
        return {}

    return {
        "stage": "baseline",
        "log_messages": ["Baseline recorded"],
    }


async def diagnose_node(state: dict) -> dict:
    """Run deterministic failure diagnoser on baseline eval results."""
    stage = state.get("stage", "")
    if stage == "diagnose":
        return {}

    diagnoses_raw = state.get("diagnoses_json", "[]")
    diagnoses = json.loads(diagnoses_raw) if isinstance(diagnoses_raw, str) else diagnoses_raw
    has_human_only = any(
        d.get("recommended_action") == "human_review" for d in diagnoses
    )

    return {
        "stage": "diagnose",
        "diagnosis_status": "needs_human" if has_human_only else "completed",
        "log_messages": [f"Diagnosis complete: {len(diagnoses)} clusters"],
    }


async def propose_node(state: dict) -> dict:
    """Propose optimization candidates (delegates to constrained tools)."""
    return {
        "stage": "propose",
        "log_messages": ["Proposal stage placeholder"],
    }


async def build_node(state: dict) -> dict:
    """Build sandbox versions for each candidate."""
    return {
        "stage": "build",
        "log_messages": ["Sandbox build placeholder"],
    }


async def targeted_eval_node(state: dict) -> dict:
    """Run targeted evaluation against each candidate's target cases."""
    return {
        "stage": "targeted_eval",
        "log_messages": ["Targeted eval placeholder"],
    }


async def regression_eval_node(state: dict) -> dict:
    """Run fixed-suite, sibling, safety, and cross-tenant regression evals."""
    return {
        "stage": "regression_eval",
        "log_messages": ["Regression eval placeholder"],
    }


async def awaiting_approval_node(state: dict) -> dict:
    """Mark iteration as awaiting human approval."""
    return {
        "stage": "awaiting_approval",
        "approval_status": "pending",
        "log_messages": ["Awaiting human approval"],
    }


async def publish_node(state: dict) -> dict:
    """Publish the approved release manifest."""
    return {
        "stage": "publish",
        "status": "completed",
        "log_messages": ["Published"],
    }


async def question_evolution_node(state: dict) -> dict:
    """Trigger next-round exploration question generation."""
    return {
        "stage": "question_evolution",
        "log_messages": ["Question evolution triggered"],
    }


# ── Conditional routing ───────────────────────────────────────────────────

def route_after_diagnose(state: dict) -> str:
    """Route after diagnosis: propose if automatable, else human approval."""
    if state.get("diagnosis_status") == "needs_human":
        return "awaiting_approval"
    diagnoses = json.loads(state.get("diagnoses_json", "[]")) if isinstance(state.get("diagnoses_json"), str) else state.get("diagnoses_json", [])
    if not diagnoses:
        return "end"
    return "propose"


def route_after_regression(state: dict) -> str:
    """Route after regression: approval if gates pass, else retry or end."""
    if state.get("gate_passed"):
        return "awaiting_approval"
    candidate_count = state.get("candidate_count", 0)
    max_candidates = 3
    if candidate_count < max_candidates:
        return "propose"
    return "end"


def route_after_approval(state: dict) -> str:
    """Route after approval: publish if approved, else end."""
    if state.get("approval_status") == "approved":
        return "publish"
    return "end"
