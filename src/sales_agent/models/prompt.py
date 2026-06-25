"""Prompt 版本管理模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class PromptVersion(TimestampMixin, Base):
    """Prompt 版本记录。

    统一承载所有层 prompt（task / system / router / risk / coach）。

    - ``prompt_category``: task | system | router | risk | coach（默认 task，向后兼容）。
    - ``prompt_key``: 该类别下的具体标识。task 类即 task_type 值（如 knowledge_qa）；
      非 task 类如 system_constraint / task_router / risk_check / coach_daily_eval 等。
    - ``task_type``: 仅 task 类使用；非 task 类为 NULL。保留该列是为了让旧查询/索引继续工作。
    - ``required_placeholders_json``: 该模板运行时 ``.format()`` 必须注入的占位符列表（JSON 数组），
      供 registry 校验与前端提示。例如 risk 类要求 ``["message", "answer"]``。

    每个 tenant + (category, key) 最多一个 active 版本。
    状态流转：draft → active → archived。
    """

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    prompt_category: Mapped[str] = mapped_column(
        Text, nullable=False, default="task", server_default="task"
    )  # task / system / router / risk / coach
    prompt_key: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    task_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="draft"
    )  # draft / active / archived
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    required_placeholders_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        # 旧索引：保留，task 类查询仍走它
        Index(
            "ix_prompt_versions_tenant_task_status",
            "tenant_id",
            "task_type",
            "status",
        ),
        # 新索引：通用 (category, key) 解析路径
        Index(
            "ix_prompt_versions_tenant_cat_key_status",
            "tenant_id",
            "prompt_category",
            "prompt_key",
            "status",
        ),
    )
