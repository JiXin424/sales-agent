"""数据库模型汇总导入。

核心模型在此导入以注册到 Base.metadata。
钉钉集成模型延迟导入，避免循环依赖。
"""

from .base import Base, TimestampMixin, generate_id, utcnow
from .tenant import Tenant
from .agent import Agent
from .agent_prompt_set import AgentPromptSet
from .agent_knowledge_scope import AgentKnowledgeScope
from .agent_risk_policy import AgentRiskPolicy
from .agent_channel_config import AgentChannelConfig
from .agent_clone_manifest import AgentCloneManifest
from .document import Document, DocumentChunk, SourceFile
from .knowledge_version import (
    DocumentRevision,
    KnowledgeVersion,
    KnowledgeVersionDocument,
    RetrievalProfile,
    RouterProfile,
)
from .runtime_release import (
    OptimizationRelease,
    AgentRuntimeBinding,
    ReleaseEvent,
)
from .conversation import (
    Conversation,
    ConversationMessage,
    ConversationSummary,
    RetrievalLog,
)
from .tenant_model_config import TenantModelConfig
from .model_call_log import ModelCallLog
from .prompt import PromptVersion
from .ingestion import IngestionJob
from .agent_run import AgentRun, AgentRunStep
from .feedback import Feedback
from .review_item import ReviewItem
from .knowledge_gap import KnowledgeGap
from .eval import EvalSuite, EvalCase, EvalRun, EvalRunResult
from .eval_trace import EvalMetricResult, RetrievalTrace, RetrievalTraceHit
from .alert import AlertRule, Alert
from .pilot_report import PilotReport
from .coach import (
    CoachUserProfile,
    CoachCompetencyScore,
    CoachCompetencyObservation,
    CoachDailyEvaluation,
    CoachIcebergAnalysis,
    CoachMilestone,
    CoachUserMilestone,
    CoachReward,
    CoachRealtimeObservation,
    CoachReportRequest,
    CoachSettings,
)
from .quick_session import QuickSession


def _import_dingtalk_models():
    """延迟导入钉钉模型，供 init_db 使用。"""
    from sales_agent.integrations.dingtalk.models import (  # noqa: F401
        DingTalkInboundMessage,
        DingTalkOutboundMessage,
        DingTalkUserMapping,
    )


__all__ = [
    "Base",
    "TimestampMixin",
    "generate_id",
    "utcnow",
    "Tenant",
    "Agent",
    "AgentPromptSet",
    "AgentKnowledgeScope",
    "AgentRiskPolicy",
    "AgentChannelConfig",
    "AgentCloneManifest",
    "Document",
    "DocumentChunk",
    "SourceFile",
    "DocumentRevision",
    "KnowledgeVersion",
    "KnowledgeVersionDocument",
    "RetrievalProfile",
    "RouterProfile",
    "OptimizationRelease",
    "AgentRuntimeBinding",
    "ReleaseEvent",
    "Conversation",
    "ConversationMessage",
    "ConversationSummary",
    "RetrievalLog",
    "TenantModelConfig",
    "ModelCallLog",
    "PromptVersion",
    "IngestionJob",
    "AgentRun",
    "AgentRunStep",
    "Feedback",
    "ReviewItem",
    "KnowledgeGap",
    "EvalSuite",
    "EvalCase",
    "EvalRun",
    "EvalRunResult",
    "EvalMetricResult",
    "RetrievalTrace",
    "RetrievalTraceHit",
    "AlertRule",
    "Alert",
    "PilotReport",
    "CoachUserProfile",
    "CoachCompetencyScore",
    "CoachCompetencyObservation",
    "CoachDailyEvaluation",
    "CoachIcebergAnalysis",
    "CoachMilestone",
    "CoachUserMilestone",
    "CoachReward",
    "CoachRealtimeObservation",
    "CoachReportRequest",
    "CoachSettings",
    "QuickSession",
]
