"""销售动作编排服务（orchestration）。

:class:`SalesActionService` 把理解层（detector + LLM 抽取 + 校验）与持久化
状态机（:class:`SalesActionRepository`）粘合在一起，向 Online Graph（Task 4）
暴露单一入口 :meth:`handle_message`，返回类型化的
:class:`SalesActionOperationResult`。

职责（见 brief Step 5）：

- 检测快速意图（:func:`detect_fast_action_intent`）；
- 对 create/suggest/clarify 路径跑 LLM 抽取 + 校验（
  :func:`parse_sales_action_request` + :func:`validate_action_extraction`）；
- 合并多轮澄清的半成品（「哪天？」→ 用户回答 → 补全创建）；
- 解析「张总那个任务」式引用，按用户的活跃任务定位目标动作；
- 驱动仓储的 create/complete/cancel/snooze/list；
- 组装面向用户的 ``response_text``；
- 返回 :class:`SalesActionOperationResult`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sales_agent.services.sales_actions.contracts import (
    SalesActionDecision,
    SalesActionExtraction,
    SalesActionScope,
)
from sales_agent.services.sales_actions.detector import detect_fast_action_intent
from sales_agent.services.sales_actions.parser import parse_sales_action_request
from sales_agent.services.sales_actions.repository import (
    ActionStateResult,
    SalesActionRepository,
)
from sales_agent.services.sales_actions.time_parser import (
    parse_scheduled_at,
    validate_action_extraction,
)

logger = logging.getLogger(__name__)

# 快路由可直判的操作型意图（无需 LLM 抽取建提醒字段，但 complete/cancel/snooze
# 仍需定位目标动作；snooze 还需新时间）。
_STATE_INTENTS = {"complete_action", "cancel_action", "snooze_action", "list_actions"}


# ---------------------------------------------------------------------------
# Typed operation result (cross-task contract — Task 4 reads these fields)
# ---------------------------------------------------------------------------

@dataclass
class SalesActionOperationResult:
    """:meth:`SalesActionService.handle_message` 的返回值。

    ``response_kind``：

    - ``"sales_action"`` —— 已作为销售动作处理，``response_text`` 直接回复用户；
    - ``"chat"`` —— 不是销售动作，交回普通聊天管线（``response_text`` 为空）。

    ``operation`` ∈ {create, complete, cancel, snooze, list, clarify, suggest,
    ignore}；``status`` 为该操作的细粒度状态（created/done/cancelled/snoozed/
    clarify/suggested/listed/not_handled/not_found…）。
    """

    operation: str = "ignore"
    status: str = "not_handled"
    response_text: str = ""
    response_kind: str = "sales_action"
    action_id: str | None = None
    scheduled_at: datetime | None = None
    reason_code: str = ""
    # 附带的活跃任务摘要（list / suggest 时供 Task 4 渲染卡片）
    actions: list[dict[str, Any]] = field(default_factory=list)
    # On a ``clarify`` result: the partial extraction (``SalesActionExtraction``
    # serialized via ``model_dump()``) that the caller should persist across
    # turns (e.g. in graph checkpoint state) so the follow-up turn can merge
    # the user's answer against it. ``None`` on all non-clarify results.
    pending_partial: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Pending-clarification partial store
# ---------------------------------------------------------------------------

@dataclass
class _PendingPartial:
    """一次未决澄清的半成品抽取（缺时间/缺标题等），等待下一轮补全。"""

    extraction: SalesActionExtraction
    reason_code: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SalesActionService:
    """销售动作编排服务。

    Parameters
    ----------
    repo :
        已绑定到当前会话 :class:`AsyncSession` 的仓储实例。
    chat_model :
        支持 ``async generate(messages=..., response_format=...)`` 的模型实例
        （:class:`~sales_agent.llm.base.ChatModel`）。单元测试可注入假模型。
    """

    def __init__(self, repo: SalesActionRepository, chat_model: Any) -> None:
        self.repo = repo
        self.chat_model = chat_model
        # 多轮澄清的半成品，按 (tenant, agent, user, conversation) 缓存。
        # NOTE: 这只是同实例内的回退存储；跨轮真正传递半成品靠调用方
        # (Online Graph checkpoint state → ``handle_message(pending_partial=)``)，
        # 因为该节点每轮构造一个新的 service 实例，实例缓存会在第二轮丢失。
        self._pending: dict[tuple[str, str, str, str], _PendingPartial] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        *,
        scope: SalesActionScope,
        message: str,
        conversation_id: str,
        topic_id: str | None,
        source_event_id: str | None,
        now: datetime,
        pending_partial: dict[str, Any] | None = None,
    ) -> SalesActionOperationResult:
        """理解一条用户消息并驱动销售动作状态机。

        这是 Task 4（Online Graph）调用本服务的唯一入口。

        ``pending_partial`` 为可选的跨轮半成品抽取（上一轮 clarify 时由调用方
        从 checkpoint 透传进来），让本轮回答（如「下午3点」）能合并到已存的
        标题/客户上。不传则回退到实例内 ``self._pending`` 缓存。
        """
        fast = detect_fast_action_intent(message)

        # 操作型意图（完成/取消/推迟/列表）走状态机分支
        if fast in _STATE_INTENTS:
            return await self._handle_state_intent(
                scope, fast, message, conversation_id, source_event_id, now
            )

        # create / suggest / clarify / none —— 需要 LLM 抽取
        extraction = await self._extract_with_merge(
            scope, message, conversation_id, now, pending_partial=pending_partial
        )
        decision = validate_action_extraction(extraction, now=now)

        # LLM 识别出操作型意图但快路由没命中 → 交给状态机分支（复用已抽取结果，避免二次 LLM 调用）
        if decision.action == "ignore" and decision.reason_code == "deferred_to_service":
            return await self._handle_state_intent(
                scope, extraction.intent, message, conversation_id, source_event_id, now,
                extraction=extraction,
            )

        if decision.action == "ignore":
            # 真正的非动作 → 交回普通聊天
            self._discard_pending(scope, conversation_id)
            return SalesActionOperationResult(
                response_kind="chat",
                operation="ignore",
                status="not_handled",
                reason_code=decision.reason_code or "not_an_action",
            )

        if decision.action == "clarify":
            self._stash_pending(scope, conversation_id, extraction, decision.reason_code)
            return SalesActionOperationResult(
                operation="clarify",
                status="clarify",
                response_text=decision.response_text,
                reason_code=decision.reason_code,
                pending_partial=extraction.model_dump(),
            )

        if decision.action == "suggest":
            self._discard_pending(scope, conversation_id)
            return SalesActionOperationResult(
                operation="suggest",
                status="suggested",
                response_text=decision.response_text,
                reason_code=decision.reason_code,
                scheduled_at=decision.scheduled_at,
            )

        # create
        card = await self.repo.create_action(
            scope,
            title=decision.title,
            customer_name=decision.customer_name,
            action_type=decision.action_type,
            scheduled_at=decision.scheduled_at,  # type: ignore[arg-type]
            timezone=decision.timezone,
            conversation_id=conversation_id,
            topic_id=topic_id,
            source_event_id=source_event_id,
            source_kind="explicit_user",
            context_snapshot={"message": message},
            agent_advice="",
        )
        self._discard_pending(scope, conversation_id)
        return SalesActionOperationResult(
            operation="create",
            status="created",
            response_text=decision.response_text,
            action_id=card.id,
            scheduled_at=decision.scheduled_at,
            reason_code="created",
        )

    # ------------------------------------------------------------------
    # State-intent branch (complete / cancel / snooze / list)
    # ------------------------------------------------------------------

    async def _handle_state_intent(
        self,
        scope: SalesActionScope,
        intent: str,
        message: str,
        conversation_id: str,
        source_event_id: str | None,
        now: datetime,
        *,
        extraction: SalesActionExtraction | None = None,
    ) -> SalesActionOperationResult:
        # An operational message means the user has moved on from any pending
        # create-clarification flow — drop the stale partial so a later create
        # message doesn't merge against it. (snooze's own missing_time path
        # stashes a fresh partial below.)
        if intent != "snooze_action":
            self._discard_pending(scope, conversation_id)

        if intent == "list_actions":
            return await self._handle_list(scope)

        # complete / cancel / snooze —— 抽取（取 customer/title，snooze 取新时间）。
        # 复用调用方已算好的 extraction（来自 deferred_to_service 分支），避免二次 LLM 调用。
        if extraction is None:
            extraction = await parse_sales_action_request(
                message, self.chat_model, now, timezone="Asia/Shanghai"
            )
        target = await self._resolve_target_action(scope, extraction)

        if intent == "complete_action":
            if target is None:
                return self._clarify_target(scope)
            res = await self.repo.complete_action(
                scope, target.id, event_id=source_event_id
            )
            self._discard_pending(scope, conversation_id)
            return SalesActionOperationResult(
                operation="complete",
                status=res.status,
                response_text=("已完成：" + target.title) if res.status == "done" else "",
                action_id=target.id,
                reason_code=res.reason_code,
            )

        if intent == "cancel_action":
            if target is None:
                return self._clarify_target(scope)
            res = await self.repo.cancel_action(
                scope, target.id, event_id=source_event_id
            )
            self._discard_pending(scope, conversation_id)
            return SalesActionOperationResult(
                operation="cancel",
                status=res.status,
                response_text=("已取消：" + target.title) if res.status == "cancelled" else "",
                action_id=target.id,
                reason_code=res.reason_code,
            )

        # snooze_action
        new_time = parse_scheduled_at(extraction.scheduled_at, extraction.timezone)
        if target is None:
            return self._clarify_target(scope)
        if new_time is None or new_time <= now:
            self._stash_pending(scope, conversation_id, extraction, "missing_time")
            return SalesActionOperationResult(
                operation="clarify",
                status="clarify",
                response_text="你想推迟到什么时候？",
                reason_code="missing_time",
                action_id=target.id,
                pending_partial=extraction.model_dump(),
            )
        result = await self.repo.snooze_action(
            scope, target.id, event_id=source_event_id or "", new_time=new_time
        )
        # snooze_action may return ActionStateResult (not_found / already_terminal
        # under a concurrent transition) — surface that instead of faking success.
        if isinstance(result, ActionStateResult):
            self._discard_pending(scope, conversation_id)
            return SalesActionOperationResult(
                operation="snooze",
                status=result.status,
                response_text="",
                action_id=target.id,
                reason_code=result.reason_code,
            )
        when = new_time.strftime("%Y-%m-%d %H:%M")
        self._discard_pending(scope, conversation_id)
        return SalesActionOperationResult(
            operation="snooze",
            status="snoozed",
            response_text=f"已推迟「{target.title}」到 {when}",
            action_id=target.id,
            scheduled_at=new_time,
            reason_code="snoozed",
        )

    async def _handle_list(self, scope: SalesActionScope) -> SalesActionOperationResult:
        cards = await self.repo.list_actions(scope, status="pending")
        if not cards:
            return SalesActionOperationResult(
                operation="list",
                status="empty",
                response_text="目前没有待办的销售动作。",
                reason_code="no_pending_actions",
            )
        lines = ["你的待办销售动作："]
        for c in cards:
            when = c.scheduled_at.strftime("%Y-%m-%d %H:%M")
            who = f"（{c.customer_name}）" if c.customer_name else ""
            lines.append(f"- {when} {c.title}{who}")
        return SalesActionOperationResult(
            operation="list",
            status="listed",
            response_text="\n".join(lines),
            reason_code="listed",
            actions=[
                {
                    "action_id": c.id,
                    "title": c.title,
                    "customer_name": c.customer_name,
                    "scheduled_at": c.scheduled_at.isoformat(),
                    "status": c.status,
                }
                for c in cards
            ],
        )

    # ------------------------------------------------------------------
    # Reference resolution & clarification merge
    # ------------------------------------------------------------------

    async def _resolve_target_action(self, scope: SalesActionScope, extraction: SalesActionExtraction):
        """解析「张总那个任务」式引用：按 customer_name 命中用户的 pending 任务。

        命中唯一一条则返回；多条/无命中时，若用户只有一条 pending 也直接用之，
        否则返回 None（交回澄清）。
        """
        pending = await self.repo.list_actions(scope, status="pending")
        if not pending:
            return None

        customer = (extraction.customer_name or "").strip()
        if customer:
            matched = [c for c in pending if c.customer_name and customer in c.customer_name]
            if len(matched) == 1:
                return matched[0]
            if len(matched) > 1:
                return None

        # 无明确客户命中：若只有一条 pending，默认指向它
        if len(pending) == 1:
            return pending[0]
        return None

    def _clarify_target(self, scope: SalesActionScope) -> SalesActionOperationResult:
        return SalesActionOperationResult(
            operation="clarify",
            status="clarify",
            response_text="你有多个待办任务，能说一下是哪一个吗？（比如客户名字）",
            reason_code="ambiguous_target",
        )

    async def _extract_with_merge(
        self,
        scope: SalesActionScope,
        message: str,
        conversation_id: str,
        now: datetime,
        *,
        pending_partial: dict[str, Any] | None = None,
    ) -> SalesActionExtraction:
        """跑 LLM 抽取；若存在上一轮的澄清半成品，尝试合并补全。

        半成品的来源优先级：调用方透传的 ``pending_partial``（checkpoint
        真相源）→ 实例内 ``self._pending`` 缓存（同实例回退）。
        """
        extraction = await parse_sales_action_request(
            message, self.chat_model, now, timezone="Asia/Shanghai"
        )

        # Source of truth: caller-passed partial (graph checkpoint). Fall back
        # to the in-instance dict for callers that reuse one service instance.
        partial_extraction: SalesActionExtraction | None = None
        if pending_partial is not None:
            try:
                partial_extraction = SalesActionExtraction.model_validate(pending_partial)
            except Exception:
                logger.warning("invalid pending_partial payload; ignoring merge")
                partial_extraction = None
        if partial_extraction is None:
            partial = self._pending.get(self._key(scope, conversation_id))
            if partial is not None:
                partial_extraction = partial.extraction

        if partial_extraction is None:
            return extraction

        # 合并：用本轮抽取补全上一轮缺失的字段（典型：缺时间 → 用户回答时间）
        merged = self._merge_partial(partial_extraction, extraction)
        # 合并后若仍可建提醒则消费半成品；否则保留等待下一轮
        if merged.title and merged.scheduled_at:
            self._discard_pending(scope, conversation_id)
        return merged

    @staticmethod
    def _merge_partial(
        old: SalesActionExtraction, new: SalesActionExtraction
    ) -> SalesActionExtraction:
        """用 *new* 的非空字段补全 *old*（保留 old 的 intent/explicit_create）。"""
        return SalesActionExtraction(
            intent=old.intent if old.intent != "none" else new.intent,
            explicit_create=old.explicit_create or new.explicit_create,
            title=old.title or new.title,
            customer_name=old.customer_name or new.customer_name,
            action_type=old.action_type if old.action_type != "other" else new.action_type,
            time_text=old.time_text or new.time_text,
            scheduled_at=old.scheduled_at or new.scheduled_at,
            timezone=old.timezone or new.timezone,
            confidence=max(old.confidence, new.confidence),
            missing_fields=[f for f in new.missing_fields if f not in old.missing_fields] or old.missing_fields,
            needs_clarification=new.needs_clarification,
            clarification_question=new.clarification_question,
            success_criteria=old.success_criteria or new.success_criteria,
            pursuit_goal=old.pursuit_goal or new.pursuit_goal,
        )

    # ------------------------------------------------------------------
    # pending-partial store helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(scope: SalesActionScope, conversation_id: str) -> tuple[str, str, str, str]:
        return (scope.tenant_id, scope.agent_id, scope.user_id, conversation_id)

    def _stash_pending(
        self, scope: SalesActionScope, conversation_id: str,
        extraction: SalesActionExtraction, reason_code: str,
    ) -> None:
        self._pending[self._key(scope, conversation_id)] = _PendingPartial(
            extraction=extraction, reason_code=reason_code
        )

    def _discard_pending(self, scope: SalesActionScope, conversation_id: str) -> None:
        self._pending.pop(self._key(scope, conversation_id), None)
