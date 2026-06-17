"""Agent 风险策略 — 独立于 tenant config 的风险规则副本。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class AgentRiskPolicy(TimestampMixin, Base):
    """Agent 级风险规则。

    rules_json 形如:
      {"price_commitment":"warn","delivery_commitment":"block", ...}
    与 schemas.TenantRiskPolicy 字段一致，便于复用 RiskChecker。
    """

    __tablename__ = "agent_risk_policies"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return f"<AgentRiskPolicy(id={self.id}, agent={self.agent_id})>"
