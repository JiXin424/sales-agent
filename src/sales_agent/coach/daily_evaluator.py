"""每日能力评估服务。

把某用户一天的会话聚合 → 调一次 LLM（严格 JSON）→ 校验 →
幂等地更新分数 / 观察记录 / 冰山分析 / 评估记录。

设计要点：
- 幂等：(tenant, agent, user, date) 已有 success 时直接返回，绝不重复加 delta。
- Phase 1：force_recompute 非 dry_run 时被显式拒绝（修订支持留待后续）。
- 失败不抛异常给上层业务：每用户独立结果，失败记 failed，不影响他人。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.coach import json_validator as V
from sales_agent.coach.constants import (
    DEFAULT_EVIDENCE_QUOTE_MAX_CHARS,
    DEFAULT_MINIMUM_USER_MESSAGES,
    DIMENSION_KEYS,
    INITIAL_SCORE,
    clamp_score,
)
from sales_agent.llm.call_params import get_call_params
from sales_agent.models.coach import (
    CoachCompetencyObservation,
    CoachCompetencyScore,
    CoachDailyEvaluation,
    CoachIcebergAnalysis,
    CoachUserProfile,
    CoachSettings,
)
from sales_agent.models.conversation import Conversation, ConversationMessage
from sales_agent.prompts.coach_daily_evaluation import build_evaluation_prompt

logger = logging.getLogger(__name__)

# 单次评估输入的近似最大字符数（超出则截断）
_MAX_INPUT_CHARS = 12000


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


class DailyEvaluationService:
    """每日评估服务。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Any = None,
        chat_model: Any = None,
        reward_sender: Any = None,
        reward_rng: Any = None,
    ) -> None:
        self.db = db
        self._chat_model = chat_model
        self._reward_sender = reward_sender
        self._reward_rng = reward_rng
        self._last_progression: dict[str, Any] | None = None

    def _resolve_chat_model(self) -> Any:
        if self._chat_model is not None:
            return self._chat_model
        from sales_agent.core.tenant_runtime import get_tenant_runtime
        runtime = get_tenant_runtime()
        if runtime.model_provider is not None:
            return runtime.model_provider.chat
        raise RuntimeError("无可用的 chat model（TenantRuntime 未配置 model provider）")

    async def run_for_agent(
        self,
        agent_id: str,
        *,
        tenant_id: str,
        user_id: str | None = None,
        date: str | None = None,
        dry_run: bool = False,
        force_recompute: bool = False,
    ) -> dict[str, Any]:
        """对某 Agent 运行每日评估（可选指定单个 user）。

        返回 ``{"results": [...], "summary": {...}}``。
        """
        evaluation_date = self._resolve_date(date)
        settings = await self._load_settings(tenant_id, agent_id)

        users = await self._find_candidate_users(
            tenant_id, agent_id, evaluation_date, target_user_id=user_id
        )

        results: list[dict[str, Any]] = []
        for uid in users:
            try:
                res = await self._evaluate_one(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    user_id=uid,
                    evaluation_date=evaluation_date,
                    dry_run=dry_run,
                    force_recompute=force_recompute,
                    settings=settings,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("Coach daily evaluation crashed for user=%s: %s", uid, e)
                res = {
                    "status": "failed",
                    "user_id": uid,
                    "evaluation_date": evaluation_date,
                    "error": str(e),
                }
            results.append(res)

        return {
            "results": results,
            "summary": {
                "agent_id": agent_id,
                "evaluation_date": evaluation_date,
                "dry_run": dry_run,
                "force_recompute": force_recompute,
                "total": len(results),
                "success": sum(1 for r in results if r.get("status") == "success"),
                "skipped": sum(1 for r in results if r.get("status") == "skipped"),
                "failed": sum(1 for r in results if r.get("status") == "failed"),
                "dry_run": sum(1 for r in results if r.get("status") == "dry_run"),
            },
        }

    # ------------------------------------------------------------------
    # 单用户评估
    # ------------------------------------------------------------------

    async def _evaluate_one(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_date: str,
        dry_run: bool,
        force_recompute: bool,
        settings: CoachSettings | None,
    ) -> dict[str, Any]:
        min_messages = self._get_int(settings, "minimum_user_messages", DEFAULT_MINIMUM_USER_MESSAGES)
        evidence_max = self._get_int(settings, "evidence_quote_max_chars", DEFAULT_EVIDENCE_QUOTE_MAX_CHARS)
        initial_score = self._get_int(settings, "initial_score", INITIAL_SCORE)

        # 1. 幂等检查
        existing = await self._find_success_evaluation(
            tenant_id, agent_id, user_id, evaluation_date
        )
        if existing is not None and not (force_recompute or dry_run):
            return {
                "status": "success",
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "evaluation_id": existing.id,
                "idempotent": True,
                "message": "当日已有成功评估，未重复计分",
            }
        if force_recompute and not dry_run:
            return {
                "status": "failed",
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "error": "Phase 1 不支持非 dry_run 的 force_recompute（修订/反算支持留待后续）；请用 dry_run=True 预览。",
            }

        # 2. 聚合输入
        agg = await self._aggregate_input(tenant_id, agent_id, user_id, evaluation_date)
        user_msg_count = agg["user_message_count"]
        input_summary = agg["summary"]
        conversation_block = agg["block"]

        # 3. 数据不足 → skipped
        if user_msg_count < min_messages:
            await self._write_evaluation(
                tenant_id, agent_id, user_id, evaluation_date,
                status="skipped",
                conversation_count=agg["conversation_count"],
                user_message_count=user_msg_count,
                input_summary=input_summary,
                result={},
                score_deltas={},
                iceberg={},
                error={"reason": f"用户消息数 {user_msg_count} < 阈值 {min_messages}"},
            )
            return {
                "status": "skipped",
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "user_message_count": user_msg_count,
                "message": "当日有效销售消息不足，已跳过",
            }

        # 4. LLM 调用 + 解析
        started = time.time()
        parsed, error = await self._call_llm(conversation_block, tenant_id, agent_id)
        latency_ms = int((time.time() - started) * 1000)

        # 5. 校验
        if parsed is None:
            await self._write_evaluation(
                tenant_id, agent_id, user_id, evaluation_date,
                status="failed",
                conversation_count=agg["conversation_count"],
                user_message_count=user_msg_count,
                input_summary=input_summary,
                result={},
                score_deltas={},
                iceberg={},
                latency_ms=latency_ms,
                error={"reason": f"LLM 返回无法解析为 JSON: {error}"},
            )
            return {
                "status": "failed",
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "error": f"LLM JSON 解析失败: {error}",
            }

        vr = V.validate_evaluation_payload(parsed, evidence_max_chars=evidence_max)
        if vr.status != "success":
            status = "skipped" if vr.status == "skipped" else "failed"
            await self._write_evaluation(
                tenant_id, agent_id, user_id, evaluation_date,
                status=status,
                conversation_count=agg["conversation_count"],
                user_message_count=user_msg_count,
                input_summary=input_summary,
                result=parsed,
                score_deltas={},
                iceberg={},
                latency_ms=latency_ms,
                error={"reason": vr.reason},
            )
            return {
                "status": status,
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "error": vr.reason,
            }

        payload = vr.payload or {}
        score_deltas = V.build_score_deltas(payload)
        iceberg = payload.get("iceberg", {})

        # 6. dry_run → 只写 dry_run 记录，不动分数
        if dry_run:
            eval_row = await self._write_evaluation(
                tenant_id, agent_id, user_id, evaluation_date,
                status="dry_run",
                conversation_count=agg["conversation_count"],
                user_message_count=user_msg_count,
                input_summary=input_summary,
                result=payload,
                score_deltas=score_deltas,
                iceberg=iceberg,
                latency_ms=latency_ms,
            )
            return {
                "status": "dry_run",
                "user_id": user_id,
                "evaluation_date": evaluation_date,
                "evaluation_id": eval_row.id,
                "score_deltas": score_deltas,
                "message": "dry_run：未修改任何分数",
            }

        # 7. 成功：幂等地更新分数 / 观察 / 冰山
        eval_row = await self._write_evaluation(
            tenant_id, agent_id, user_id, evaluation_date,
            status="success",
            conversation_count=agg["conversation_count"],
            user_message_count=user_msg_count,
            input_summary=input_summary,
            result=payload,
            score_deltas=score_deltas,
            iceberg=iceberg,
            latency_ms=latency_ms,
        )

        await self._apply_success(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            evaluation_date=evaluation_date,
            evaluation_id=eval_row.id,
            payload=payload,
            score_deltas=score_deltas,
            iceberg=iceberg,
            initial_score=initial_score,
            conversation_count=agg["conversation_count"],
            evaluation_row=eval_row,
        )

        result = {
            "status": "success",
            "user_id": user_id,
            "evaluation_date": evaluation_date,
            "evaluation_id": eval_row.id,
            "score_deltas": score_deltas,
        }
        if self._last_progression:
            result["progression"] = self._last_progression.get("progression")
            result["rewards"] = self._last_progression.get("rewards")
        return result

    # ------------------------------------------------------------------
    # 成功路径：幂等写入分数/观察/冰山/档案
    # ------------------------------------------------------------------

    async def _apply_success(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_date: str,
        evaluation_id: str,
        payload: dict[str, Any],
        score_deltas: dict[str, int],
        iceberg: dict[str, Any],
        initial_score: int,
        conversation_count: int = 0,
        evaluation_row: CoachDailyEvaluation | None = None,
    ) -> None:
        # 档案
        profile = await self._ensure_profile(tenant_id, agent_id, user_id, initial_score)
        profile.last_evaluated_date = evaluation_date

        dims = payload.get("dimensions", {})
        now_iso = datetime.now(timezone.utc).isoformat()

        score_moves: dict[str, tuple[int, int]] = {}
        for key in DIMENSION_KEYS:
            delta = int(score_deltas.get(key, 0))
            entry = dims.get(key, {})
            score_row = await self._ensure_score_row(
                tenant_id, agent_id, user_id, key, initial_score
            )
            old_score = int(score_row.score)
            new_score = clamp_score(old_score + delta)
            score_moves[key] = (old_score, new_score)

            score_row.score = new_score
            score_row.last_delta = delta
            score_row.last_evaluation_id = evaluation_id
            score_row.last_evaluated_at = now_iso

            if delta != 0:
                obs = CoachCompetencyObservation(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    evaluation_id=evaluation_id,
                    evaluation_date=evaluation_date,
                    dimension=key,
                    delta=delta,
                    old_score=old_score,
                    new_score=new_score,
                    reason=str(entry.get("reason", "")),
                    evidence_quotes_json=json.dumps(
                        entry.get("evidence_quotes", []), ensure_ascii=False
                    ),
                    source_conversation_ids_json=json.dumps(
                        entry.get("source_conversation_ids", []), ensure_ascii=False
                    ),
                    confidence=float(entry.get("confidence", 0.0) or 0.0),
                )
                self.db.add(obs)

        # 冰山分析
        self.db.add(
            CoachIcebergAnalysis(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                evaluation_id=evaluation_id,
                analysis_date=evaluation_date,
                surface_blocks_json=json.dumps(
                    iceberg.get("surface_blocks", []), ensure_ascii=False
                ),
                deep_blocks_json=json.dumps(
                    iceberg.get("deep_blocks", []), ensure_ascii=False
                ),
                evidence_json=json.dumps({}, ensure_ascii=False),
                data_sufficiency=str(payload.get("data_sufficiency", "sufficient")),
                summary=str(payload.get("summary", "")),
            )
        )

        await self.db.flush()

        # Phase 3：积分 / 段位 / 等级 / 里程碑解锁 / 奖励
        self._last_progression = None
        try:
            from sales_agent.coach.progression import ProgressionService
            from sales_agent.coach.reward_service import RewardService

            progression = ProgressionService(self.db)
            prog_result = await progression.apply_progression(
                tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
                evaluation_id=evaluation_id, evaluation_date=evaluation_date,
                payload=payload, score_moves=score_moves,
                conversation_count=conversation_count, profile=profile,
            )

            # 奖励（仅当有里程碑解锁时）
            rewards: list[dict[str, Any]] = []
            unlocked = prog_result.get("milestones_unlocked", [])
            if unlocked:
                reward_svc = RewardService(
                    self.db, sender=self._reward_sender, rng=self._reward_rng,
                )
                rewards = await reward_svc.grant_for_milestones(
                    tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
                    evaluation_id=evaluation_id, evaluation_date=evaluation_date,
                    unlocked=unlocked,
                )

            # 回写评估记录的 points_delta
            if evaluation_row is not None:
                evaluation_row.points_delta = int(prog_result.get("daily_points", 0))
                await self.db.flush()

            self._last_progression = {
                "progression": prog_result, "rewards": rewards,
                "points_delta": prog_result.get("daily_points", 0),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("Progression/reward phase failed (non-fatal): %s", e)

    async def _ensure_profile(
        self, tenant_id: str, agent_id: str, user_id: str, initial_score: int
    ) -> CoachUserProfile:
        row = (
            await self.db.execute(
                select(CoachUserProfile).where(
                    CoachUserProfile.tenant_id == tenant_id,
                    CoachUserProfile.agent_id == agent_id,
                    CoachUserProfile.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CoachUserProfile(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                enabled=True,
                total_points=0,
                rank="bronze",
                level=0,
            )
            self.db.add(row)
            await self.db.flush()
        return row

    async def _ensure_score_row(
        self, tenant_id: str, agent_id: str, user_id: str, dimension: str, initial_score: int
    ) -> CoachCompetencyScore:
        row = (
            await self.db.execute(
                select(CoachCompetencyScore).where(
                    CoachCompetencyScore.tenant_id == tenant_id,
                    CoachCompetencyScore.agent_id == agent_id,
                    CoachCompetencyScore.user_id == user_id,
                    CoachCompetencyScore.dimension == dimension,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CoachCompetencyScore(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                dimension=dimension,
                score=initial_score,
                last_delta=0,
            )
            self.db.add(row)
            await self.db.flush()
        return row

    # ------------------------------------------------------------------
    # 输入聚合
    # ------------------------------------------------------------------

    async def _aggregate_input(
        self, tenant_id: str, agent_id: str, user_id: str, evaluation_date: str
    ) -> dict[str, Any]:
        """聚合当天该用户的消息（排除 coach_report 会话）。"""
        stmt = (
            select(ConversationMessage)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.tenant_id == tenant_id,
                ConversationMessage.agent_id == agent_id,
                ConversationMessage.user_id == user_id,
                ConversationMessage.role.in_(("user", "assistant")),
                ConversationMessage.created_at.like(f"{evaluation_date}%"),
                Conversation.task_type != "coach_report",
            )
            .order_by(ConversationMessage.created_at)
        )
        rows = (await self.db.execute(stmt)).scalars().all()

        # 按会话分组
        by_conv: dict[str, list[ConversationMessage]] = {}
        for m in rows:
            by_conv.setdefault(m.conversation_id, []).append(m)

        user_msg_count = sum(1 for m in rows if m.role == "user")

        block_lines: list[str] = []
        total_chars = 0
        for conv_id, msgs in by_conv.items():
            block_lines.append(f"[会话 {conv_id}]")
            for m in msgs:
                role = "销售" if m.role == "user" else "助手"
                line = f"{role}：{m.content}"
                if total_chars + len(line) > _MAX_INPUT_CHARS:
                    block_lines.append("（更多内容已截断）")
                    break
                block_lines.append(line)
                total_chars += len(line)
            else:
                continue
            break

        block = "\n".join(block_lines)
        summary = (
            f"会话数 {len(by_conv)}，用户消息 {user_msg_count} 条，"
            f"总消息 {len(rows)} 条。"
        )
        return {
            "conversation_count": len(by_conv),
            "user_message_count": user_msg_count,
            "summary": summary,
            "block": block,
        }

    async def _find_candidate_users(
        self, tenant_id: str, agent_id: str, evaluation_date: str, *, target_user_id: str | None
    ) -> list[str]:
        stmt = (
            select(ConversationMessage.user_id)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.tenant_id == tenant_id,
                ConversationMessage.agent_id == agent_id,
                ConversationMessage.role == "user",
                ConversationMessage.created_at.like(f"{evaluation_date}%"),
                Conversation.task_type != "coach_report",
            )
            .distinct()
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        users = [u for u in rows if u]
        if target_user_id is not None:
            users = [u for u in users if u == target_user_id]
        return users

    # ------------------------------------------------------------------
    # LLM 调用 + 解析（重试一次）
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        conversation_block: str,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[dict | None, str | None]:
        chat_model = self._resolve_chat_model()
        # 解析 prompt（接入 DB 版本管理；无 tenant 或失败时回退内置常量）
        user_prompt = build_evaluation_prompt(conversation_block)
        system_msg = "你是销售能力评估引擎，只输出 JSON。"
        if tenant_id:
            try:
                from sales_agent.llm.prompt_loader import get_prompt

                user_prompt = get_prompt("coach", "coach_daily_eval").template.format(conversation_block=conversation_block)
                system_msg = get_prompt("coach", "coach_daily_eval_system").template
            except Exception as e:  # noqa: BLE001
                logger.warning("Coach daily_eval prompt resolve failed, fallback to builtin: %s", e)
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt},
        ]
        last_err: str | None = None
        for attempt in range(2):
            try:
                p = get_call_params("daily_evaluator")
                raw = await chat_model.generate(
                    messages=messages,
                    temperature=p.temperature,
                    max_tokens=p.max_tokens,
                    response_format={"type": "json_object"},
                )
                parsed = parse_model_json(raw)
                if parsed is not None:
                    return parsed, None
                last_err = "无法从模型输出提取 JSON 对象"
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                logger.warning("Coach LLM call attempt %d failed: %s", attempt + 1, e)
        return None, last_err

    # ------------------------------------------------------------------
    # 评估记录读写
    # ------------------------------------------------------------------

    async def _find_success_evaluation(
        self, tenant_id: str, agent_id: str, user_id: str, evaluation_date: str
    ) -> CoachDailyEvaluation | None:
        return (
            await self.db.execute(
                select(CoachDailyEvaluation).where(
                    CoachDailyEvaluation.tenant_id == tenant_id,
                    CoachDailyEvaluation.agent_id == agent_id,
                    CoachDailyEvaluation.user_id == user_id,
                    CoachDailyEvaluation.evaluation_date == evaluation_date,
                    CoachDailyEvaluation.status == "success",
                )
            )
        ).scalar_one_or_none()

    async def _write_evaluation(
        self,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        evaluation_date: str,
        *,
        status: str,
        conversation_count: int,
        user_message_count: int,
        input_summary: str,
        result: dict,
        score_deltas: dict,
        iceberg: dict,
        latency_ms: int = 0,
        error: dict | None = None,
    ) -> CoachDailyEvaluation:
        row = CoachDailyEvaluation(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            evaluation_date=evaluation_date,
            status=status,
            conversation_count=conversation_count,
            user_message_count=user_message_count,
            input_summary=input_summary,
            result_json=json.dumps(result, ensure_ascii=False),
            score_deltas_json=json.dumps(score_deltas, ensure_ascii=False),
            iceberg_json=json.dumps(iceberg, ensure_ascii=False),
            points_delta=0,
            latency_ms=latency_ms,
            error_json=json.dumps(error, ensure_ascii=False) if error else None,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------

    def _resolve_date(self, date: str | None) -> str:
        if date:
            return date[:10]
        # 默认昨天（UTC ISO 日期）
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        return yesterday.isoformat()

    async def _load_settings(self, tenant_id: str, agent_id: str) -> CoachSettings | None:
        return (
            await self.db.execute(
                select(CoachSettings).where(
                    CoachSettings.tenant_id == tenant_id,
                    CoachSettings.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _get_int(settings: CoachSettings | None, attr: str, default: int) -> int:
        if settings is None:
            return default
        try:
            return int(getattr(settings, attr, default))
        except (TypeError, ValueError):
            return default


def parse_model_json(raw: str) -> dict | None:
    """从模型输出中提取首个 JSON 对象。"""
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 首个完整对象
    brace = 0
    start = -1
    for i, ch in enumerate(raw):
        if ch == "{":
            if brace == 0:
                start = i
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace == 0 and start >= 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None
