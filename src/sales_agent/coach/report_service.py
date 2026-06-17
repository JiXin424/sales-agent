"""教练报告服务。

render_report 只读数据库，返回 ``{summary, sections}``（与 Agent answer_dict
同形），可被 HTTP / 钉钉渲染器直接复用。不写数据、不调 LLM。

支持的报告类型：scores / level / iceberg / milestones / rewards / full。
Phase 1+2：scores / iceberg / full 有真实数据；level/milestones/rewards
返回当前默认状态（Phase 3 才真正累计）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach.constants import (
    DIMENSIONS,
    INITIAL_SCORE,
    REPORT_FULL,
    REPORT_ICEBERG,
    REPORT_LEVEL,
    REPORT_MILESTONES,
    REPORT_REWARDS,
    REPORT_SCORES,
    dimension_label,
    rank_for_points,
)
from sales_agent.models.coach import (
    CoachCompetencyObservation,
    CoachCompetencyScore,
    CoachDailyEvaluation,
    CoachIcebergAnalysis,
    CoachReward,
    CoachUserMilestone,
    CoachUserProfile,
)

logger = logging.getLogger(__name__)

INSUFFICIENT_MSG = "目前还没有足够的销售对话数据来做这份报告。多和助手聊聊真实的销售场景，下次就能看到结果了。"


class CoachReportService:
    """教练报告渲染。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def render_report(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        report_type: str,
        viewer_user_id: str | None = None,
        query_text: str = "",
    ) -> dict[str, Any]:
        """渲染指定类型的报告。"""
        scores = await self._load_scores(tenant_id, agent_id, user_id)
        profile = await self._load_profile(tenant_id, agent_id, user_id)

        has_data = bool(scores) or profile is not None

        if report_type == REPORT_SCORES:
            return self._render_scores(scores)
        if report_type == REPORT_ICEBERG:
            return await self._render_iceberg(tenant_id, agent_id, user_id, has_data)
        if report_type == REPORT_LEVEL:
            return self._render_level(profile)
        if report_type == REPORT_MILESTONES:
            return await self._render_milestones(tenant_id, agent_id, user_id, has_data)
        if report_type == REPORT_REWARDS:
            return await self._render_rewards(tenant_id, agent_id, user_id, has_data)
        if report_type == REPORT_FULL:
            return await self._render_full(
                tenant_id, agent_id, user_id, scores, profile, has_data
            )
        # 兜底
        return self._render_scores(scores)

    # ------------------------------------------------------------------
    # 各类型
    # ------------------------------------------------------------------

    def _render_scores(self, scores: list[CoachCompetencyScore]) -> dict[str, Any]:
        if not scores:
            return {
                "summary": INSUFFICIENT_MSG,
                "sections": [],
            }
        score_map = {s.dimension: s for s in scores}
        lines: list[str] = []
        sections: list[dict[str, str]] = []
        for key, label in DIMENSIONS:
            s = score_map.get(key)
            if s is None:
                score_val = INITIAL_SCORE
                delta = 0
            else:
                score_val = int(s.score)
                delta = int(s.last_delta)
            delta_str = self._fmt_delta(delta)
            lines.append(f"- {label}：{score_val} 分{delta_str}")
            sections.append({
                "title": label,
                "content": f"当前 {score_val} 分{delta_str}",
            })

        # 最近一条非零证据高亮
        latest_obs = "（暂无新的能力变动记录）"
        summary = "你的六维能力当前评分：\n" + "\n".join(lines)
        return {"summary": summary, "sections": sections}

    async def _render_iceberg(
        self, tenant_id: str, agent_id: str, user_id: str, has_data: bool
    ) -> dict[str, Any]:
        iceberg = await self._load_latest_iceberg(tenant_id, agent_id, user_id)
        if iceberg is None or not has_data:
            return {"summary": INSUFFICIENT_MSG, "sections": []}

        surface = self._loads(iceberg.surface_blocks_json, [])
        deep = self._loads(iceberg.deep_blocks_json, [])
        if not surface and not deep:
            summary = "目前没有明显卡点，继续保持。"
            if iceberg.data_sufficiency == "insufficient":
                summary = INSUFFICIENT_MSG
            return {"summary": summary, "sections": []}

        sections: list[dict[str, str]] = []
        if surface:
            sections.append({
                "title": "表层卡点（行为层）",
                "content": self._fmt_blocks(surface),
            })
        if deep:
            sections.append({
                "title": "深层卡点（心态层）",
                "content": self._fmt_blocks(deep),
            })
        summary = (iceberg.summary or "").strip() or "以下是当前最值得关注的卡点。"
        return {"summary": summary, "sections": sections}

    def _render_level(self, profile: CoachUserProfile | None) -> dict[str, Any]:
        points = int(profile.total_points) if profile else 0
        rank_key, rank_name = rank_for_points(points)
        level = int(profile.level) if profile else 0
        summary = f"段位：{rank_name}　等级：Lv.{level}　成长积分：{points}"
        return {
            "summary": summary,
            "sections": [
                {"title": "段位与等级", "content": summary},
            ],
        }

    async def _render_milestones(
        self, tenant_id: str, agent_id: str, user_id: str, has_data: bool
    ) -> dict[str, Any]:
        unlocked = await self._count_unlocked(tenant_id, agent_id, user_id)
        summary = f"已解锁里程碑：{unlocked} 个。"
        if not has_data:
            summary = INSUFFICIENT_MSG
        return {"summary": summary, "sections": []}

    async def _render_rewards(
        self, tenant_id: str, agent_id: str, user_id: str, has_data: bool
    ) -> dict[str, Any]:
        rows = await self._load_rewards(tenant_id, agent_id, user_id, limit=5)
        if not rows:
            summary = "暂无奖励记录。" if has_data else INSUFFICIENT_MSG
            return {"summary": summary, "sections": []}
        sections = [
            {
                "title": r.reward_type,
                "content": f"状态：{r.status}　{r.message or ''}".rstrip(),
            }
            for r in rows
        ]
        return {"summary": "最近的奖励记录：", "sections": sections}

    async def _render_full(
        self,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        scores: list[CoachCompetencyScore],
        profile: CoachUserProfile | None,
        has_data: bool,
    ) -> dict[str, Any]:
        if not has_data:
            return {"summary": INSUFFICIENT_MSG, "sections": []}

        sections: list[dict[str, str]] = []

        # 六维概览
        score_map = {s.dimension: s for s in scores}
        score_lines = []
        for key, label in DIMENSIONS:
            s = score_map.get(key)
            val = int(s.score) if s else INITIAL_SCORE
            score_lines.append(f"- {label}：{val}")
        sections.append({
            "title": "六维能力",
            "content": "\n".join(score_lines),
        })

        # 段位/等级
        points = int(profile.total_points) if profile else 0
        _, rank_name = rank_for_points(points)
        level = int(profile.level) if profile else 0
        sections.append({
            "title": "段位与等级",
            "content": f"段位：{rank_name}　等级：Lv.{level}　成长积分：{points}",
        })

        # 冰山
        iceberg = await self._load_latest_iceberg(tenant_id, agent_id, user_id)
        if iceberg is not None:
            surface = self._loads(iceberg.surface_blocks_json, [])
            deep = self._loads(iceberg.deep_blocks_json, [])
            if surface:
                sections.append({
                    "title": "表层卡点",
                    "content": self._fmt_blocks(surface),
                })
            if deep:
                sections.append({
                    "title": "深层卡点",
                    "content": self._fmt_blocks(deep),
                })

        # 下一步建议（取最近一次评估的 next_growth_suggestion）
        suggestion = await self._load_latest_suggestion(tenant_id, agent_id, user_id)
        if suggestion:
            sections.append({"title": "下一步建议", "content": suggestion})

        summary = "教练报告：六维能力 + 段位等级 + 卡点诊断。"
        return {"summary": summary, "sections": sections}

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------

    async def _load_scores(
        self, tenant_id: str, agent_id: str, user_id: str
    ) -> list[CoachCompetencyScore]:
        rows = (
            await self.db.execute(
                select(CoachCompetencyScore).where(
                    CoachCompetencyScore.tenant_id == tenant_id,
                    CoachCompetencyScore.agent_id == agent_id,
                    CoachCompetencyScore.user_id == user_id,
                )
            )
        ).scalars().all()
        return list(rows)

    async def _load_profile(
        self, tenant_id: str, agent_id: str, user_id: str
    ) -> CoachUserProfile | None:
        return (
            await self.db.execute(
                select(CoachUserProfile).where(
                    CoachUserProfile.tenant_id == tenant_id,
                    CoachUserProfile.agent_id == agent_id,
                    CoachUserProfile.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def _load_latest_iceberg(
        self, tenant_id: str, agent_id: str, user_id: str
    ) -> CoachIcebergAnalysis | None:
        return (
            await self.db.execute(
                select(CoachIcebergAnalysis)
                .where(
                    CoachIcebergAnalysis.tenant_id == tenant_id,
                    CoachIcebergAnalysis.agent_id == agent_id,
                    CoachIcebergAnalysis.user_id == user_id,
                )
                .order_by(CoachIcebergAnalysis.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _load_latest_suggestion(
        self, tenant_id: str, agent_id: str, user_id: str
    ) -> str:
        row = (
            await self.db.execute(
                select(CoachDailyEvaluation)
                .where(
                    CoachDailyEvaluation.tenant_id == tenant_id,
                    CoachDailyEvaluation.agent_id == agent_id,
                    CoachDailyEvaluation.user_id == user_id,
                    CoachDailyEvaluation.status == "success",
                )
                .order_by(CoachDailyEvaluation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or not row.result_json:
            return ""
        try:
            data = json.loads(row.result_json)
        except json.JSONDecodeError:
            return ""
        return str(data.get("next_growth_suggestion", "")).strip()

    async def _count_unlocked(
        self, tenant_id: str, agent_id: str, user_id: str
    ) -> int:
        from sqlalchemy import func
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(CoachUserMilestone)
                .where(
                    CoachUserMilestone.tenant_id == tenant_id,
                    CoachUserMilestone.agent_id == agent_id,
                    CoachUserMilestone.user_id == user_id,
                )
            )
        ).scalar()
        return int(total or 0)

    async def _load_rewards(
        self, tenant_id: str, agent_id: str, user_id: str, *, limit: int = 5
    ) -> list[CoachReward]:
        rows = (
            await self.db.execute(
                select(CoachReward)
                .where(
                    CoachReward.tenant_id == tenant_id,
                    CoachReward.agent_id == agent_id,
                    CoachReward.user_id == user_id,
                )
                .order_by(CoachReward.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)

    # ------------------------------------------------------------------
    # 渲染小工具
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_delta(delta: int) -> str:
        if delta == 0:
            return ""
        sign = "+" if delta > 0 else ""
        return f"（{sign}{delta}）"

    @staticmethod
    def _loads(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    @classmethod
    def _fmt_blocks(cls, blocks: list[dict]) -> str:
        type_zh = {
            "customer_block": "客户卡点", "needs_block": "需求卡点",
            "value_block": "价值卡点", "trust_advancement_block": "信任推进卡点",
            "action_rhythm_block": "行动节奏卡点",
            "motivation_block": "目标动力卡点", "confidence_block": "信心卡点",
            "belief_block": "信念卡点", "emotional_pressure_block": "情绪压力卡点",
        }
        sev_zh = {"low": "轻", "medium": "中", "high": "重"}
        lines: list[str] = []
        for b in blocks:
            t = type_zh.get(b.get("type", ""), b.get("type", ""))
            sev = sev_zh.get(b.get("severity", "medium"), "中")
            desc = b.get("description", "")
            quotes = b.get("evidence_quotes", [])
            q = f"｜证据：{quotes[0]}" if quotes else ""
            lines.append(f"- [{sev}] {t}：{desc}{q}")
        return "\n".join(lines)
