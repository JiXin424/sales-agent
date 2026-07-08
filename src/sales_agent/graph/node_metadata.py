"""图节点元数据：集中声明每个节点的类型、是否调 LLM、对应 prompt。

用途：
1. ``graph_debug`` API 据此在 mermaid 图上区分纯函数节点 / LLM 节点 / 子图节点，
   并返回「节点 ↔ prompt」结构化映射供前端展示。
2. 单一事实源——节点增删或 prompt 接入变更时只改这里，前后端同步。

为什么用集中映射表而非 LangGraph ``add_node(tags=...)``：
``compiled.get_graph().nodes`` 不暴露 tags（node.metadata 恒为 None，
RunnableCallable 无 tags 属性），无法在运行时读回。集中表更直接可控，
且顺带提供节点→prompt 对应关系（tags 机制给不了）。

LLM 节点（calls_llm=True）共 5 个：context_resolution / evidence_routing /
retrieve / generate / advance_flow。route_task / check_risk 接入 LLM 后也标 True
（受 feature flag 控制，关闭时退化为纯规则，但节点本身具备 LLM 能力）。
"""

from __future__ import annotations

from typing import Literal


NodeType = Literal["function", "subgraph"]


class NodeMeta:
    """单个图节点的元数据。

    Attributes:
        calls_llm: 节点是否调用 LLM（含受 flag 控制的条件调用）。
        type: "function"（普通函数节点）或 "subgraph"（编译子图节点）。
        desc: 节点职责一句话描述。
        prompts: 该节点对应的 prompt 列表；纯函数节点为空。每项形如
            ``{"name": "TASK_ROUTER_PROMPT", "source": "prompts/task_router_prompt.py",
               "note": "可选说明"}``。
    """

    __slots__ = ("calls_llm", "type", "desc", "prompts")

    def __init__(
        self,
        *,
        calls_llm: bool = False,
        type: NodeType = "function",
        desc: str = "",
        prompts: list[dict] | None = None,
    ) -> None:
        self.calls_llm = calls_llm
        self.type = type
        self.desc = desc
        self.prompts = prompts or []

    def to_dict(self) -> dict:
        return {
            "calls_llm": self.calls_llm,
            "type": self.type,
            "desc": self.desc,
            "prompts": self.prompts,
        }


# ── Online Graph（主图）节点元数据 ────────────────────────────────
ONLINE_NODE_META: dict[str, NodeMeta] = {
    "normalize_turn": NodeMeta(
        desc="解析 requested_flow + 触发器匹配，选 flow_action（路由决策）",
    ),
    "guided_flow": NodeMeta(
        type="subgraph",
        desc="Guided Flow 子图（访前/访后/小赢/卡点流程）",
    ),
    "context_resolution": NodeMeta(
        calls_llm=True,
        desc="话题恢复/过期 + Context Resolver 判定本轮回话关系",
        prompts=[
            {"name": "CONTEXT_RESOLVER_PROMPT",
             "source": "prompts/context_resolver_prompt.py", "note": "上下文消解"},
            {"name": "CLARIFICATION_RESOLVER_PROMPT",
             "source": "prompts/clarification_resolver_prompt.py", "note": "澄清子步"},
        ],
    ),
    "evidence_routing": NodeMeta(
        calls_llm=True,
        desc="Evidence Router 把意图映射成 task_type/knowledge_policy/retrieval_query",
        prompts=[
            {"name": "EVIDENCE_ROUTER_PROMPT",
             "source": "prompts/evidence_router_prompt.py"},
        ],
    ),
    "clarification_response": NodeMeta(
        desc="标记本轮走澄清问答（response_kind=clarify）",
    ),
    "log_control_response": NodeMeta(
        desc="澄清回复落库",
    ),
    "chat": NodeMeta(
        type="subgraph",
        desc="Chat 子图包装节点（函数节点内 ainvoke Chat 子图）",
    ),
    "duplicate": NodeMeta(
        desc="处理重复事件（不更新 last_event_id）",
    ),
    "log_flow_output": NodeMeta(
        desc="guided flow 本轮输出落库",
    ),
    "scenario_coach": NodeMeta(
        desc="场景教练节点：匹配预设问题，命中则返回预设回答",
    ),
    "log_scenario_response": NodeMeta(
        desc="场景教练回答落库",
    ),
}

# ── Chat 子图节点元数据 ───────────────────────────────────────────
CHAT_NODE_META: dict[str, NodeMeta] = {
    "fast_reply": NodeMeta(
        desc="?/help/新话题 等快捷命令的罐头回复",
    ),
    "validate": NodeMeta(
        desc="校验 tenant_id/user_id/message 非空",
    ),
    "resolve_tenant": NodeMeta(
        desc="TenantResolver 填 tenant_info",
    ),
    "load_context": NodeMeta(
        desc="SQL 查 ConversationMessage 取历史消息",
    ),
    "route_task": NodeMeta(
        calls_llm=True,
        desc="规则优先路由；enable_llm_router 打开时 LLM 分类兜底",
        prompts=[
            {"name": "TASK_ROUTER_PROMPT",
             "source": "prompts/task_router_prompt.py",
             "note": "受 enable_llm_router flag 控制，关闭时走纯规则"},
        ],
    ),
    "retrieve": NodeMeta(
        calls_llm=True,
        desc="按 retrieval_path 分支检索：ontology（含实体抽取 LLM）/ rag / skip",
        prompts=[
            {"name": "_ENTITY_EXTRACTION_PROMPT",
             "source": "graph/retrieval/ontology_graph.py",
             "note": "仅 ontology 路径的 extract_terms 调用"},
        ],
    ),
    "evidence_gate": NodeMeta(
        desc="按 knowledge_policy 判定证据是否够，不够则跳过生成",
    ),
    "generate": NodeMeta(
        calls_llm=True,
        desc="主生成节点：PromptRegistry 3-tier 解析 prompt + execute_agent 生成",
        prompts=[
            {"name": "SYSTEM_CONSTRAINT", "source": "prompts/system.py", "note": "全局人设"},
            {"name": "12 个 task prompt", "source": "prompts/<task_type>.py",
             "note": "按 task_type 分派：knowledge_qa/script_generation/objection_handling/"
                     "conversation_review/general_sales_coaching/visit_preparation/"
                     "follow_up_planning/customer_context_summary/deal_advancement/"
                     "conversation_scoring/post_visit_review/emotional_support"},
        ],
    ),
    "check_risk": NodeMeta(
        calls_llm=True,
        desc="规则 full_check 风控；enable_llm_risk_check 打开时叠加 LLM 风控",
        prompts=[
            {"name": "RISK_CHECK_PROMPT",
             "source": "prompts/risk_check_prompt.py",
             "note": "受 enable_llm_risk_check flag 控制，关闭时走纯规则"},
        ],
    ),
    "log": NodeMeta(
        desc="落库 + 更新 ConversationTopic 摘要",
    ),
}

# ── Guided Flow 子图节点元数据 ────────────────────────────────────
GUIDED_FLOW_NODE_META: dict[str, NodeMeta] = {
    "start_flow": NodeMeta(
        desc="查 FlowDefinition + 同步 start()，写 flow 状态 + 首问",
    ),
    "advance_flow": NodeMeta(
        calls_llm=True,
        desc="推进流程 stage；终态 handler 调 LLM 生成卡片/复盘",
        prompts=[
            {"name": "VISIT_PREPARATION_PROMPT",
             "source": "prompts/visit_preparation.py", "note": "访前流程终态"},
            {"name": "POST_VISIT_REVIEW_PROMPT",
             "source": "prompts/post_visit_review.py", "note": "访后流程终态"},
            {"name": "SW_SYSTEM / SW_CARD_TEMPLATE",
             "source": "prompts/coach_quick.py", "note": "小赢欣赏流程"},
            {"name": "SB_SYSTEM / SB_SPLIT_TEMPLATE / SB_CARD_TEMPLATE",
             "source": "prompts/coach_quick.py", "note": "卡点破框流程"},
        ],
    ),
    "cancel_flow": NodeMeta(
        desc="清所有 flow 字段，返回固定再见消息",
    ),
}

# 图 ID → 节点元数据表。graph_id 与 GRAPH_REGISTRY 的 key 一致。
NODE_META_BY_GRAPH: dict[str, dict[str, NodeMeta]] = {
    "online": ONLINE_NODE_META,
    "chat": CHAT_NODE_META,
    "guided-flow": GUIDED_FLOW_NODE_META,
}


def get_node_meta(graph_id: str, node_id: str) -> NodeMeta | None:
    """取某图某节点的元数据；未知节点返回 None（调用方按纯函数兜底）。"""
    return NODE_META_BY_GRAPH.get(graph_id, {}).get(node_id)


def is_llm_node(graph_id: str, node_id: str) -> bool:
    """节点是否调用 LLM。"""
    meta = get_node_meta(graph_id, node_id)
    return bool(meta and meta.calls_llm)
