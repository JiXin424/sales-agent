"""Quick Session state machine as a LangGraph StateGraph.

Replaces the manual stage-field state machine in ``coach/quick_session.py``.
"""

from __future__ import annotations

from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END


class QuickSessionState(TypedDict, total=False):
    """State for the Quick Session state machine."""
    session_type: str           # "small_win" | "sales_block"
    stage: str                  # Current stage name
    payload: dict               # Accumulated user responses
    reply_text: str             # Response to show the user
    card_data: dict | None      # Final generated card


# Small Win Appreciation stages
def node_small_win(state: QuickSessionState) -> dict:
    """Ask: 今天有什么小成交/小进展想分享？"""
    return {
        "stage": "small_win",
        "reply_text": "太棒了！说说看，今天有什么让你觉得有成就感的进展？哪怕是一个小细节也没关系 😊",
    }


def node_strength(state: QuickSessionState) -> dict:
    """Ask: 在这个过程中，你觉得自己的优势是什么？"""
    payload = state.get("payload", {})
    payload["small_win"] = state.get("reply_text", "")
    return {
        "stage": "strength",
        "payload": payload,
        "reply_text": "真好！在这个过程中，你觉得是你身上的什么能力或特质帮到了你？",
    }


def node_gratitude(state: QuickSessionState) -> dict:
    """Ask: 客户给了什么积极反馈？"""
    payload = state.get("payload", {})
    payload["strength"] = state.get("reply_text", "")
    return {
        "stage": "gratitude",
        "payload": payload,
        "reply_text": "客户当时有什么反应？有没有让你觉得被认可的瞬间？",
    }


def node_energy(state: QuickSessionState) -> dict:
    """Ask: 这个经验接下来怎么用？"""
    payload = state.get("payload", {})
    payload["gratitude"] = state.get("reply_text", "")
    return {
        "stage": "energy",
        "payload": payload,
        "reply_text": "最后一个问题～这次成功的经验，你觉得可以在接下来的哪些客户身上复用？",
    }


def node_generate_card(state: QuickSessionState) -> dict:
    """Generate the LLM appreciation card."""
    payload = state.get("payload", {})
    payload["energy"] = state.get("reply_text", "")
    # In real implementation, calls LLM to generate a structured card
    card = {
        "title": "🎉 小胜利记录",
        "small_win": payload.get("small_win", ""),
        "strength": payload.get("strength", ""),
        "gratitude": payload.get("gratitude", ""),
        "energy": payload.get("energy", ""),
    }
    return {
        "stage": "completed",
        "payload": payload,
        "card_data": card,
        "reply_text": f"帮你记录下来了！\n\n🎉 **小胜利**：{card['small_win']}\n💪 **你的优势**：{card['strength']}\n\n继续保持这个状态！",
    }


def route_session_type(state: QuickSessionState) -> str:
    """Route to the correct session type's first node."""
    if state.get("session_type") == "small_win":
        return "small_win"
    return "awaiting_blocker"


def advance_from_small_win(state: QuickSessionState) -> str:
    """Determine next stage in small_win flow."""
    stage = state.get("stage", "small_win")
    transitions = {
        "small_win": "strength",
        "strength": "gratitude",
        "gratitude": "energy",
        "energy": "generate_card",
        "completed": "generate_card",
    }
    return transitions.get(stage, "generate_card")


def build_quick_session_graph() -> StateGraph:
    """Build the Quick Session state machine as a StateGraph.

    Graph structure::

        START --(session_type?)--> small_win --> strength --> gratitude
                                                                    |
                                                                    v
                                                                  energy
                                                                    |
                                                                    v
                                                              generate_card
                                                                    |
                                                                    v
                                                                   END
    """
    builder = StateGraph(QuickSessionState)

    # Nodes
    builder.add_node("small_win", node_small_win)
    builder.add_node("strength", node_strength)
    builder.add_node("gratitude", node_gratitude)
    builder.add_node("energy", node_energy)
    builder.add_node("generate_card", node_generate_card)

    # Entry routing
    builder.add_conditional_edges(
        START, route_session_type,
        {"small_win": "small_win", "awaiting_blocker": "generate_card"},
    )

    # Stage transitions
    builder.add_conditional_edges(
        "small_win", advance_from_small_win,
        {"strength": "strength", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "strength", advance_from_small_win,
        {"gratitude": "gratitude", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "gratitude", advance_from_small_win,
        {"energy": "energy", "generate_card": "generate_card"},
    )
    builder.add_conditional_edges(
        "energy", advance_from_small_win,
        {"generate_card": "generate_card"},
    )
    builder.add_edge("generate_card", END)

    return builder
