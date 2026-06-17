"""Coach Growth System 数据模型。

11 张表（详见 spec §"Data Model"）。除 coach_milestones（定义表）外，
所有表都带 tenant_id / agent_id / user_id 作用域。

约定与既有模型一致：Text 列、JSON 以 *_json 文本存储、TimestampMixin、
generate_id 主键。create_all 自动建表；既有生产表不受影响。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class CoachUserProfile(TimestampMixin, Base):
    """用户在某 Agent 下的教练成长账户。"""

    __tablename__ = "coach_user_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank: Mapped[str] = mapped_column(Text, nullable=False, default="bronze")
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_evaluated_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_preferences_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_coach_profiles_scope", "tenant_id", "agent_id", "user_id"),
    )


class CoachCompetencyScore(TimestampMixin, Base):
    """某维度的当前分数。每个 (tenant, agent, user, dimension) 唯一。"""

    __tablename__ = "coach_competency_scores"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    milestone_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_evaluation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_evaluated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_coach_scores_unique",
            "tenant_id",
            "agent_id",
            "user_id",
            "dimension",
            unique=True,
        ),
    )


class CoachCompetencyObservation(TimestampMixin, Base):
    """证据化的每日评分变动记录（非零 delta）。"""

    __tablename__ = "coach_competency_observations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    evaluation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    evaluation_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    old_score: Mapped[int] = mapped_column(Integer, nullable=False)
    new_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_quotes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_conversation_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)


class CoachDailyEvaluation(TimestampMixin, Base):
    """某用户某天的评估记录。每个 (tenant, agent, user, date) 最多一条 success。"""

    __tablename__ = "coach_daily_evaluations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    evaluation_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="success")
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    score_deltas_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    iceberg_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    points_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_json: Mapped[str] = mapped_column(Text, nullable=True)
    replaces_evaluation_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_coach_eval_scope_date",
            "tenant_id",
            "agent_id",
            "user_id",
            "evaluation_date",
        ),
    )


class CoachIcebergAnalysis(TimestampMixin, Base):
    """最新与历史冰山诊断。"""

    __tablename__ = "coach_iceberg_analyses"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    evaluation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    analysis_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    surface_blocks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    deep_blocks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    data_sufficiency: Mapped[str] = mapped_column(Text, nullable=False, default="sufficient")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CoachMilestone(TimestampMixin, Base):
    """里程碑定义表（可由代码常量 seed）。"""

    __tablename__ = "coach_milestones"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    scope: Mapped[str] = mapped_column(Text, nullable=False)  # dimension | all_dimensions
    dimension: Mapped[str | None] = mapped_column(Text, nullable=True)  # all-dim 为空
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    level_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    badge_key: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CoachUserMilestone(TimestampMixin, Base):
    """已解锁里程碑。每个 (tenant, agent, user, milestone) 唯一。"""

    __tablename__ = "coach_user_milestones"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    milestone_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    unlocked_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_evaluation_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_coach_user_milestones_unique",
            "tenant_id",
            "agent_id",
            "user_id",
            "milestone_id",
            unique=True,
        ),
    )


class CoachReward(TimestampMixin, Base):
    """奖励记录。"""

    __tablename__ = "coach_rewards"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reward_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_milestone_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_evaluation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class CoachRealtimeObservation(TimestampMixin, Base):
    """实时观察/引导日志（Phase 4）。"""

    __tablename__ = "coach_realtime_observations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scene_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    observed_signals_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dimension_focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    guidance_level: Mapped[str] = mapped_column(Text, nullable=False, default="suppressed")
    guidance_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    applied_to_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suppressed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CoachReportRequest(TimestampMixin, Base):
    """报告请求审计日志。"""

    __tablename__ = "coach_report_requests"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rendered_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CoachSettings(TimestampMixin, Base):
    """Agent 级教练配置。每个 (tenant, agent) 最多一条。"""

    __tablename__ = "coach_settings"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    realtime_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_evaluation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_evaluation_time: Mapped[str] = mapped_column(Text, nullable=False, default="23:00")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Shanghai")
    minimum_user_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_realtime_guidance_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_reward_notification_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    initial_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    allow_negative_delta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    voice_rewards_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    red_packet_reminders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_quote_max_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=160)

    __table_args__ = (
        Index("ix_coach_settings_scope", "tenant_id", "agent_id"),
    )
