#!/usr/bin/env python3
"""多轮对话评估 — 从 DB 加载对话历史，用 Turn-level 指标评估。

从 conversation_messages 表加载真实对话记录，构建 ConversationalTestCase，
使用 deepeval 的 TurnFaithfulnessMetric 和 ConversationCompletenessMetric
逐轮评估对话质量。

用法:
  python eval/deepeval_conversation_eval.py --tenant-id taishan --limit 10
  python eval/deepeval_conversation_eval.py --conversation-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from deepeval import evaluate
from deepeval.test_case import ConversationalTestCase, Turn
from deepeval.metrics import (
    TurnFaithfulnessMetric,
    ConversationCompletenessMetric,
)


# ── DB 加载 ──────────────────────────────────────────────────────────────


async def load_conversations(
    tenant_id: str,
    limit: int = 10,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """从 DB 加载多轮对话。

    查询 conversation_messages 表，按 conversation_id 分组，
    每个组内的消息按 created_at 升序排列，仅保留 user/assistant 角色。
    至少包含 2 条消息（一轮完整对话）才会返回。

    Args:
        tenant_id: 租户 ID
        limit: 最多返回的对话数
        conversation_id: 可选，指定单个对话 ID

    Returns:
        [{"conversation_id": str, "turns": [{"role": str, "content": str}, ...]}, ...]
    """
    from sqlalchemy import select, func
    from sales_agent.core.database import get_session_factory
    from sales_agent.models.conversation import ConversationMessage

    factory = get_session_factory()
    async with factory() as db:
        # 获取最近有活动的 conversation_id 列表
        subq = (
            select(
                ConversationMessage.conversation_id,
                func.max(ConversationMessage.created_at).label("last_msg"),
            )
            .where(ConversationMessage.tenant_id == tenant_id)
            .group_by(ConversationMessage.conversation_id)
            .order_by(func.max(ConversationMessage.created_at).desc())
            .limit(limit)
        )
        if conversation_id:
            subq = subq.where(
                ConversationMessage.conversation_id == conversation_id
            )

        result = await db.execute(subq)
        conv_ids = [row[0] for row in result.fetchall()]

        conversations: list[dict[str, Any]] = []
        for cid in conv_ids:
            msg_result = await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == cid)
                .where(ConversationMessage.role.in_(["user", "assistant"]))
                .order_by(ConversationMessage.created_at.asc())
            )
            messages = msg_result.scalars().all()
            turns = [{"role": m.role, "content": m.content} for m in messages]
            if len(turns) >= 2:  # 至少一轮完整对话
                conversations.append(
                    {"conversation_id": cid, "turns": turns}
                )

    return conversations


# ── TestCase 构建 ────────────────────────────────────────────────────────


def build_conversational_test_case(
    conv: dict[str, Any],
) -> ConversationalTestCase:
    """将一段对话转换为 ConversationalTestCase。

    Args:
        conv: 包含 conversation_id 和 turns 列表的字典

    Returns:
        ConversationalTestCase：可直接传给 deepeval.evaluate()
    """
    turns = [
        Turn(role=t["role"], content=t["content"]) for t in conv["turns"]
    ]
    return ConversationalTestCase(
        turns=turns,
        scenario="钉钉单聊销售教练",
        chatbot_role="销售教练 AI 助手",
    )


# ── 评估流程 ──────────────────────────────────────────────────────────────


async def run_conversation_eval(
    tenant_id: str,
    limit: int = 10,
    conversation_id: str | None = None,
    judge_model: Any = None,
) -> list[dict]:
    """运行多轮对话评估。

    加载对话 → 构建 ConversationalTestCase → 使用 Turn-level 指标评估。

    Args:
        tenant_id: 租户 ID
        limit: 最多评估的对话数
        conversation_id: 可选，指定单个对话 ID
        judge_model: 裁判 LLM 模型实例

    Returns:
        list[dict]: 每个对话的评估结果
    """
    conversations = await load_conversations(tenant_id, limit, conversation_id)
    if not conversations:
        print("No conversations found.")
        return []

    results: list[dict] = []
    for conv in conversations:
        tc = build_conversational_test_case(conv)
        metrics = [
            TurnFaithfulnessMetric(
                threshold=0.5, include_reason=True, model=judge_model
            ),
            ConversationCompletenessMetric(
                threshold=0.5, include_reason=True, model=judge_model,
            ),
        ]
        try:
            ev = evaluate(test_cases=[tc], metrics=metrics)
            result: dict[str, Any] = {
                "conversation_id": conv["conversation_id"],
                "turns": len(conv["turns"]),
                "metrics": {},
            }
            for tr in ev.test_results:
                for md in tr.metrics_data:
                    result["metrics"][md.name] = {
                        "score": md.score,
                        "success": getattr(md, "success", False),
                        "reason": getattr(md, "reason", "")[:500],
                    }
            results.append(result)
        except Exception as e:
            results.append(
                {
                    "conversation_id": conv["conversation_id"],
                    "turns": len(conv["turns"]),
                    "error": str(e),
                }
            )

    return results


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="多轮对话评估 —— 从 DB 加载历史，用 Turn-level 指标评估"
    )
    p.add_argument(
        "--tenant-id",
        default="taishan",
        help="租户 ID（默认 taishan）",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="最多评估的对话数（默认 10）",
    )
    p.add_argument(
        "--conversation-id",
        default=None,
        help="指定单条对话 ID（不指定则按最近活动排序）",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="输出目录（默认 eval/results/conversation）",
    )
    args = p.parse_args()

    # 初始化 DB 引擎
    print("[INFO] Initializing DB connection...")
    try:
        from deepeval_test_cases import init_eval_db

        init_eval_db()
        print("[INFO] DB initialized")
    except Exception as e:
        print(f"[FATAL] DB init failed: {e}", file=sys.stderr)
        sys.exit(1)

    # 初始化裁判模型
    try:
        from deepeval_metrics import get_judge_model

        judge = get_judge_model()
        model_name = (
            judge.get_model_name()
            if hasattr(judge, "get_model_name")
            else str(judge)
        )
        print(f"[INFO] Judge model: {model_name}")
    except RuntimeError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(1)

    # 运行评估
    print(f"[EVAL] Loading conversations (tenant={args.tenant_id}, limit={args.limit})")
    results = asyncio.run(
        run_conversation_eval(
            tenant_id=args.tenant_id,
            limit=args.limit,
            conversation_id=args.conversation_id,
            judge_model=judge,
        )
    )

    if not results:
        return

    # 保存结果
    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(__file__).resolve().parent / "results" / "conversation"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"conversation_eval_{ts}.json"
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[SAVE] Results saved to {path}")

    # 汇总
    total = len(results)
    ok = sum(1 for r in results if "error" not in r)
    errors = total - ok
    print(f"\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Evaluated {total} conversations ({ok} ok, {errors} errors)")

    # 打印每个对话的评分
    for r in results:
        cid = r["conversation_id"]
        if "error" in r:
            print(f"  [{cid[:12]}...] {r['turns']} turns  ERROR: {r['error']}")
        else:
            scores = ", ".join(
                f"{k}={v['score']:.3f}" for k, v in r["metrics"].items()
            )
            print(f"  [{cid[:12]}...] {r['turns']} turns  {scores}")


if __name__ == "__main__":
    main()
