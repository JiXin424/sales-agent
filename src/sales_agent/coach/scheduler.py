"""Coach 每日评估调度器。

在 worker 角色进程（PROCESS_ROLE=worker）后台运行（worker_runner.run 已调用
start_coach_scheduler）。每分钟检查所有 active agent，到达各自
CoachSettings.daily_evaluation_time（默认 23:00）时按其 timezone（默认
Asia/Shanghai）触发 DailyEvaluationService.run_for_agent。

设计要点：
- 无外部依赖（asyncio + zoneinfo）。
- 评估服务本身幂等（当天已成功则跳过计分）；调度器额外按
  (local_date, agent_id) 去重，避免同一分钟内重复调用。
- 单 agent 评估失败不影响其他 agent。
- Agent 无 CoachSettings 行时按默认值（enabled=True / 23:00 / Asia/Shanghai）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 兜底（本项目用 3.10）
    ZoneInfo = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60
_DEFAULT_EVAL_TIME = "23:00"
_DEFAULT_TZ = "Asia/Shanghai"
_FIRED_SET_MAX = 5000  # fired 集合粗略上限，超过则清空（防无限增长）

# 保持后台 task 引用，避免被 GC 回收
_task: asyncio.Task[None] | None = None


async def start_coach_scheduler() -> None:
    """启动 Coach 每日评估调度器（后台 task，立即返回，不阻塞调用方）。"""
    global _task
    if _task is not None and not _task.done():
        logger.info("Coach scheduler already running, skip")
        return
    _task = asyncio.create_task(_scheduler_loop())
    logger.info("Coach scheduler started (checks every %ds)", _CHECK_INTERVAL_SECONDS)


async def stop_coach_scheduler() -> None:
    """停止调度器（优雅关闭用）。"""
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


async def _scheduler_loop() -> None:
    """调度主循环：每分钟检查到点的 agent 并触发评估。"""
    fired: set[tuple[str, str]] = set()  # (local_date, agent_id)
    await asyncio.sleep(5)  # 启动缓冲，等 DB / 运行时就绪
    logger.info("Coach scheduler loop running")
    while True:
        try:
            fired = await _tick(fired)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Coach scheduler tick failed")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


async def _tick(fired: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """单次检查：找到点的 agent 并触发评估，返回更新后的 fired 集合。"""
    from sqlalchemy import select

    from sales_agent.coach.daily_evaluator import DailyEvaluationService
    from sales_agent.core.database import get_session_factory
    from sales_agent.models.agent import Agent
    from sales_agent.models.coach import CoachSettings

    now_utc = datetime.now(timezone.utc)
    factory = get_session_factory()

    # 1. 找出本轮到点、且本日尚未触发的 agent
    to_fire: list[tuple[str, str, str]] = []  # (tenant_id, agent_id, eval_date)
    async with factory() as db:
        agents = (
            await db.execute(select(Agent).where(Agent.status == "active"))
        ).scalars().all()
        for agent in agents:
            settings = (
                await db.execute(
                    select(CoachSettings).where(
                        CoachSettings.tenant_id == agent.tenant_id,
                        CoachSettings.agent_id == agent.id,
                    )
                )
            ).scalar_one_or_none()
            enabled = bool(settings.daily_evaluation_enabled) if settings else True
            if not enabled:
                continue
            eval_time = (settings.daily_evaluation_time if settings else None) or _DEFAULT_EVAL_TIME
            tz_name = (settings.timezone if settings else None) or _DEFAULT_TZ
            local_date, local_hhmm = _local_now(tz_name, now_utc)
            if local_hhmm == eval_time and (local_date, agent.id) not in fired:
                fired.add((local_date, agent.id))
                to_fire.append((agent.tenant_id, agent.id, local_date))

    # 2. 在独立 session 逐个评估（幂等；失败隔离）
    for tenant_id, agent_id, eval_date in to_fire:
        try:
            async with factory() as db:
                result = await DailyEvaluationService(db).run_for_agent(
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    date=eval_date,
                    dry_run=False,
                    force_recompute=False,
                )
            summary = result.get("summary", {})
            logger.info(
                "Coach scheduled eval done: agent=%s date=%s total=%s "
                "success=%s skipped=%s failed=%s",
                agent_id, eval_date, summary.get("total"),
                summary.get("success"), summary.get("skipped"), summary.get("failed"),
            )
        except Exception:
            logger.exception(
                "Coach scheduled eval failed: agent=%s date=%s", agent_id, eval_date
            )

    # 3. 粗略清理 fired 集合
    if len(fired) > _FIRED_SET_MAX:
        fired.clear()
    return fired


def _local_now(tz_name: str, now_utc: datetime) -> tuple[str, str]:
    """返回 (local_date_iso, "HH:MM") 按给定时区；时区无效时回落 UTC。"""
    tz: object = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
    local = now_utc.astimezone(tz)  # type: ignore[arg-type]
    return local.date().isoformat(), local.strftime("%H:%M")
