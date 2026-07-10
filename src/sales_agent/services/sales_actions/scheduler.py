"""销售动作提醒调度器。

职责：

1. 认领到期提醒（含失败可重试，见 :meth:`claim_due_reminders`）；
2. 渲染卡片 Markdown 并通过钉钉卡片发送器投递；
3. 记录投递成功/失败（失败按有界退避重试）；
4. 按本地时间窗口（早 09:00 / 晚 18:30 Asia/Shanghai，来自配置）幂等创建
   digest 提醒，供下一轮扫描投递。

调度器面向 ``session_factory``（``async_sessionmaker``）与 ``sender_factory``
（零参可调用，返回具备 ``send_markdown_card`` / ``update_card`` 的发送器），
便于在生产注入 :class:`DingTalkCardSender`、在测试注入 FakeSender，
满足「真实钉钉业务路径测试只替换对外投递」的要求。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from sales_agent.services.sales_actions.card_renderer import (
    CardView,
    build_digest_idempotency_key as _build_digest_key,
    render_due_reminder,
    render_evening_digest,
    render_morning_digest,
)
from sales_agent.services.sales_actions.contracts import SalesActionScope
from sales_agent.services.sales_actions.repository import (
    SalesActionRepository,
    _bounded_backoff,
)

logger = logging.getLogger(__name__)

#: 提醒类型 → 属于 digest（多动作汇总）。
DIGEST_REMINDER_TYPES: frozenset[str] = frozenset({"morning_digest", "evening_digest"})


# ---------------------------------------------------------------------------
# 纯函数（单元可测）
# ---------------------------------------------------------------------------


def compute_sales_action_backoff_seconds(attempts: int) -> int:
    """有界指数退避（秒），委托给仓储共享实现，避免重复策略。"""
    return _bounded_backoff(attempts)


def build_digest_idempotency_key(  # noqa: D401 — re-export for scheduler tests
    kind: str,
    tenant_id: str,
    agent_id: str,
    user_id: str,
    day: date,
) -> str:
    """``{kind}:{tenant}:{agent}:{user}:{date.isoformat()}``（委托给渲染器）。"""
    return _build_digest_key(kind, tenant_id, agent_id, user_id, day)


def scope_from_reminder(reminder, card) -> SalesActionScope:
    """从提醒（+ 关联卡片）构造 :class:`SalesActionScope`。

    ``dingtalk_user_id`` 来自卡片；digest 提醒（action_id 为空）此处得 ``None``，
    由调度器在投递时按作用域 pending 卡片补齐。
    """
    return SalesActionScope(
        tenant_id=reminder.tenant_id,
        agent_id=reminder.agent_id,
        user_id=reminder.user_id,
        dingtalk_user_id=getattr(card, "dingtalk_user_id", None) if card is not None else None,
    )


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    """``"09:00"`` → ``(9, 0)``；非法返回 ``None``（用于禁用某窗口）。"""
    try:
        hh, mm = text.split(":", 1)
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return (h, m)
    return None


def within_digest_window(
    now_utc: datetime,
    tz_name: str,
    window_time: str,
    *,
    grace_hours: int = 2,
) -> bool:
    """``now_utc`` 在 ``tz_name`` 本地的 ``window_time`` 当日窗口内（含 grace）。

    用例：调度器每 30s 扫描，grace 内首次扫描创建 digest，幂等键保证当日只创建一次。
    """
    window = _parse_hhmm(window_time)
    if window is None:
        return False
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    local = now_utc.astimezone(tz)
    wh, wm = window
    window_start = local.replace(hour=wh, minute=wm, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=grace_hours)
    return window_start <= local <= window_end


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 运行结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulerRunResult:
    """单次调度扫描的汇总。"""

    delivered: int = 0
    failed: int = 0
    digests_created: int = 0


# ---------------------------------------------------------------------------
# 一次性扫描
# ---------------------------------------------------------------------------


async def run_sales_action_scheduler_once(
    session_factory,
    sender_factory,
    now: datetime,
    *,
    morning_time: str | None = None,
    evening_time: str | None = None,
    timezone_name: str | None = None,
    grace_hours: int | None = None,
    max_attempts: int | None = None,
    batch_size: int | None = None,
) -> SchedulerRunResult:
    """单次扫描：认领到期 → 投递 → 记录 → 幂等创建 digest。

    所有时间/退避参数缺省时从 ``settings.sales_actions`` 读取，便于生产直接调用；
    测试可显式传值以脱离配置。
    """
    cfg = _load_sales_action_config()
    morning_time = morning_time if morning_time is not None else cfg.morning_digest_time
    evening_time = evening_time if evening_time is not None else cfg.evening_digest_time
    tz_name = timezone_name or cfg.default_timezone
    grace = grace_hours if grace_hours is not None else 2
    max_att = max_attempts if max_attempts is not None else cfg.max_attempts
    batch = batch_size if batch_size is not None else cfg.batch_size

    sender = sender_factory()

    delivered = 0
    failed = 0

    # Pass 1 — claim → render → send → record, then COMMIT so deliveries are
    # durable BEFORE any digest work. This is the I-1 guarantee: a digest
    # collision/failure in pass 2 can never roll back an already-sent card.
    async with session_factory() as db:
        repo = SalesActionRepository(db)
        reminders = await repo.claim_due_reminders(
            now=now, limit=batch, max_attempts=max_att
        )

        for reminder in reminders:
            ok = await _deliver_one(repo, sender, reminder)
            if ok:
                delivered += 1
            else:
                failed += 1

        await db.commit()

    # Pass 2 — idempotent digest creation in its OWN transaction. Each insert
    # is wrapped in a SAVEPOINT (see ``_create_due_digests``) so a unique-
    # constraint collision — a concurrent worker that won the pre-check→commit
    # race — rolls back ONLY that savepoint, never the deliveries above.
    digests_created = 0
    async with session_factory() as db:
        repo = SalesActionRepository(db)
        digests_created = await _create_due_digests(
            repo,
            now=now,
            tz_name=tz_name,
            morning_time=morning_time,
            evening_time=evening_time,
            grace_hours=grace,
        )
        await db.commit()

    return SchedulerRunResult(
        delivered=delivered, failed=failed, digests_created=digests_created
    )


async def _deliver_one(repo: SalesActionRepository, sender, reminder) -> bool:
    """投递单条提醒。返回是否成功。"""
    if reminder.reminder_type in DIGEST_REMINDER_TYPES:
        return await _deliver_digest(repo, sender, reminder)
    return await _deliver_single(repo, sender, reminder)


async def _deliver_single(repo: SalesActionRepository, sender, reminder) -> bool:
    card = await repo.get_card_for_reminder(reminder)
    scope = scope_from_reminder(reminder, card)
    if card is None:
        await repo.record_delivery_failure(
            scope, reminder_id=reminder.id, error="no related card for reminder"
        )
        return False
    if not scope.dingtalk_user_id:
        await repo.record_delivery_failure(
            scope,
            reminder_id=reminder.id,
            action_id=card.id,
            error="card has no dingtalk_user_id; cannot address card",
        )
        return False
    try:
        md = render_due_reminder(CardView.from_card(card))
        out_track_id = await sender.send_markdown_card(
            scope.dingtalk_user_id, card.title, md
        )
        await repo.record_delivery_success(
            scope,
            reminder_id=reminder.id,
            action_id=card.id,
            card_instance_id=out_track_id,
            rendered_text=md,
        )
        return True
    except Exception as exc:  # 网络/钉钉错误
        logger.warning("sales action reminder %s delivery failed: %s", reminder.id, exc)
        await repo.record_delivery_failure(
            scope, reminder_id=reminder.id, action_id=card.id, error=str(exc)[:500]
        )
        return False


async def _deliver_digest(repo: SalesActionRepository, sender, reminder) -> bool:
    base_scope = scope_from_reminder(reminder, None)
    cards = await repo.list_cards_for_scope(base_scope, status="pending")
    if not cards:
        # 没有可汇总的 pending 动作：标记成功投递空摘要，避免反复重试。
        await repo.record_delivery_success(
            base_scope,
            reminder_id=reminder.id,
            action_id=None,
            rendered_text="(暂无可汇总动作)",
        )
        return True
    dingtalk_user_id = cards[0].dingtalk_user_id
    if not dingtalk_user_id:
        await repo.record_delivery_failure(
            base_scope,
            reminder_id=reminder.id,
            error="digest scope has no dingtalk_user_id",
        )
        return False
    scope = SalesActionScope(
        tenant_id=base_scope.tenant_id,
        agent_id=base_scope.agent_id,
        user_id=base_scope.user_id,
        dingtalk_user_id=dingtalk_user_id,
    )
    views = [CardView.from_card(c) for c in cards]
    if reminder.reminder_type == "morning_digest":
        md = render_morning_digest(views)
        title = "早安·今日待办"
    else:
        md = render_evening_digest(views)
        title = "晚间小结"
    try:
        out_track_id = await sender.send_markdown_card(dingtalk_user_id, title, md)
        await repo.record_delivery_success(
            scope,
            reminder_id=reminder.id,
            action_id=None,
            card_instance_id=out_track_id,
            rendered_text=md,
        )
        return True
    except Exception as exc:
        logger.warning("digest reminder %s delivery failed: %s", reminder.id, exc)
        await repo.record_delivery_failure(
            scope, reminder_id=reminder.id, error=str(exc)[:500]
        )
        return False


async def _create_due_digests(
    repo: SalesActionRepository,
    *,
    now: datetime,
    tz_name: str,
    morning_time: str,
    evening_time: str,
    grace_hours: int,
) -> int:
    """对每个仍有 pending 动作的作用域，按本地时间窗口幂等创建 digest 提醒。

    幂等性双保险（I-1）：

    1. 预检 ``reminder_idempotency_key_exists`` —— 常见单 worker 路径直接跳过，
       避免不必要的 savepoint 开销（纯优化）；
    2. 每个 insert 包裹 SAVEPOINT 并捕获 ``IntegrityError`` —— 硬保证：即便两个
       worker 同时通过预检、随后撞上 ``uq_sales_action_reminders_idempotency`` 唯一
       约束，也只回滚该 savepoint，绝不向上抛出、绝不回滚调用方已提交的投递。
    """
    scopes = await repo.list_active_action_scopes()
    created = 0
    # 本地日期用配置时区统一确定（digest 是按用户当日的概念）。
    local_now = _to_local(now, tz_name)
    today = local_now.date()
    windows = []
    if within_digest_window(now, tz_name, morning_time, grace_hours=grace_hours):
        windows.append("morning_digest")
    if within_digest_window(now, tz_name, evening_time, grace_hours=grace_hours):
        windows.append("evening_digest")

    for kind in windows:
        for scope in scopes:
            key = build_digest_idempotency_key(
                kind, scope.tenant_id, scope.agent_id, scope.user_id, today
            )
            # 预检（优化）：单 worker 常见路径跳过，避免 savepoint 开销。
            if await repo.reminder_idempotency_key_exists(key):
                continue
            # SAVEPOINT（硬保证）：预检与 insert 之间的竞争窗口里，另一 worker
            # 可能已提交同一 digest；此时 insert 撞唯一约束，savepoint 吸收
            # IntegrityError，调用方事务与已投递提醒不受影响。
            try:
                async with repo.db.begin_nested():
                    repo.add_digest_reminder(
                        scope,
                        reminder_type=kind,
                        remind_at=now,
                        idempotency_key=key,
                    )
            except IntegrityError:
                logger.debug(
                    "sales action %s digest already created concurrently (key=%s)",
                    kind,
                    key,
                )
                continue
            created += 1
    return created


def _to_local(now: datetime, tz_name: str) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        return now.astimezone(ZoneInfo(tz_name))
    except Exception:
        return now.astimezone(ZoneInfo("Asia/Shanghai"))


def _load_sales_action_config():
    from sales_agent.core.config import get_settings

    return get_settings().sales_actions


# ---------------------------------------------------------------------------
# 常驻循环
# ---------------------------------------------------------------------------


async def sales_action_scheduler_loop(
    session_factory,
    sender_factory,
    scan_interval_seconds: float,
    *,
    max_attempts: int | None = None,
    morning_time: str | None = None,
    evening_time: str | None = None,
    timezone_name: str | None = None,
    grace_hours: int | None = None,
    batch_size: int | None = None,
) -> None:
    """常驻扫描循环：每 ``scan_interval_seconds`` 执行一次 :func:`run_sales_action_scheduler_once`。

    发送器生命周期由本循环管理：起始构造一次、在 finally 中关闭，避免每轮重建
    httpx 连接池；每轮 once-run 通过 ``lambda: sender`` 复用同一实例。
    """
    sender = sender_factory()
    try:
        while True:
            try:
                await run_sales_action_scheduler_once(
                    session_factory,
                    lambda: sender,
                    _now_utc(),
                    morning_time=morning_time,
                    evening_time=evening_time,
                    timezone_name=timezone_name,
                    grace_hours=grace_hours,
                    max_attempts=max_attempts,
                    batch_size=batch_size,
                )
            except Exception:
                logger.exception("sales action scheduler loop iteration failed")
            await asyncio.sleep(scan_interval_seconds)
    finally:
        close = getattr(sender, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.debug("sales action sender close failed", exc_info=True)
