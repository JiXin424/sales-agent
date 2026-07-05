"""
每 5 轮触发的深度诊断 + Prompt 自主修复节点。

流程:
  deep_diagnose → [fix_prompt → evaluate_validation → validate_prompt → rollback?]
    → 正常工具 → judge
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from eval.optimizer.state import (
    ActionType,
    MetricSnapshot,
    OptimizerState,
    RoundMetrics,
)
from eval.optimizer.tools.evaluate import run_evaluate

logger = logging.getLogger(__name__)

# ── 拒绝模式 ──────────────────────────────────────────────────────────────
_REJECTION_PATTERNS: list[str] = [
    "这和销售没关系",
    "这个和销售无关",
    "跟销售没关系",
    "和咱们销售工作没关系",
    "和销售实战没关系",
    "和福利销售没关系",
    "这个问题跟销售",
    "不涉及销售",
    "不在我的知识范围内",
    "不在我的业务范围内",
    "不在我的专业范围内",
    "知识库中没有",
    "知识库里没有",
    "当前知识库没有",
    "没有找到相关信息",
    "没有找到可靠依据",
    "没有相关文档",
    "无法回答该问题",
    "我没办法直接给答案",
    "我没办法直接给你",
    "没法做直接",
    "我帮不上忙",
    "这不是我的专业",
    "这个问题太泛了",
    "这个问题太笼统",
    "需要考虑更多背景信息",
]


def detect_rejection_patterns(questions: list[MetricSnapshot]) -> int:
    """统计回答中包含拒绝模式的问题数。"""
    count = 0
    for snap in questions:
        answer = snap.answer or ""
        for pat in _REJECTION_PATTERNS:
            if pat in answer:
                count += 1
                break
    return count


def classify_failures(
    per_question: list[MetricSnapshot],
) -> dict[str, Any]:
    """将失败的题目按指标分类为: retrieval / prompt / knowledge_gap。

    Returns: {
        "retrieval_issues": [...],
        "prompt_issues": [...],
        "knowledge_gaps": [...],
        "other": [...],
        "needs_prompt_fix": bool,
        "total_analyzed": int,
    }
    """
    retrieval_issues: list[MetricSnapshot] = []
    prompt_issues: list[MetricSnapshot] = []
    knowledge_gaps: list[MetricSnapshot] = []
    other: list[MetricSnapshot] = []

    for snap in per_question:
        if snap.error:
            continue
        recall = snap.contextual_recall
        relevancy = snap.contextual_relevancy
        faith = snap.faithfulness
        corr = snap.correctness

        if recall < 0.3 and relevancy < 0.3:
            retrieval_issues.append(snap)
        elif recall >= 0.5 and relevancy >= 0.5 and (faith < 0.5 or corr < 0.4):
            prompt_issues.append(snap)
        elif recall < 0.3 and relevancy >= 0.5:
            knowledge_gaps.append(snap)
        else:
            other.append(snap)

    total = len(retrieval_issues) + len(prompt_issues) + len(knowledge_gaps) + len(other)
    prompt_hits = detect_rejection_patterns(prompt_issues + other)

    # 决策: prompt 问题 > 20% 或有明显拒绝模式（即使不在 prompt_issues 分类中）
    prompt_hits = detect_rejection_patterns(prompt_issues + other + retrieval_issues)
    needs_prompt_fix = (
        len(prompt_issues) >= 1
        and (
            len(prompt_issues) / max(total, 1) > 0.20
            or prompt_hits >= 1
        )
    ) or (
        # 即使没有典型 prompt_issues，只要有拒绝模式 + 足够样本
        prompt_hits >= 1 and total >= 3
    )

    return {
        "retrieval_issues": retrieval_issues[:10],
        "prompt_issues": prompt_issues[:10],
        "knowledge_gaps": knowledge_gaps[:10],
        "other": other[:10],
        "needs_prompt_fix": needs_prompt_fix,
        "total_analyzed": total,
        "prompt_hits": prompt_hits,
    }


# ── Graph node functions ──────────────────────────────────────────────────


async def node_deep_diagnose(state: OptimizerState) -> dict:
    """深度诊断节点: 每 5 轮分析低分题的根因分类。"""
    metrics: RoundMetrics = state["_metrics"]
    classification = classify_failures(metrics.per_question)

    logger.info(
        "[Round %d] DEEP_DIAGNOSE: analyzed %d questions → "
        "retrieval=%d, prompt=%d, knowledge_gap=%d, other=%d, "
        "prompt_hits=%d, needs_prompt_fix=%s",
        state["round"],
        classification["total_analyzed"],
        len(classification["retrieval_issues"]),
        len(classification["prompt_issues"]),
        len(classification["knowledge_gaps"]),
        len(classification["other"]),
        classification["prompt_hits"],
        classification["needs_prompt_fix"],
    )

    if classification["prompt_issues"]:
        sample = classification["prompt_issues"][0]
        logger.info(
            "[Round %d] DEEP_DIAGNOSE sample prompt issue: "
            "q=%s recall=%.2f relevancy=%.2f faith=%.2f corr=%.2f "
            "answer_preview=%r",
            state["round"],
            sample.question_id,
            sample.contextual_recall,
            sample.contextual_relevancy,
            sample.faithfulness,
            sample.correctness,
            (sample.answer or "")[:100],
        )

    debug_rounds = list(state.get("prompt_debug_rounds", []))
    debug_rounds.append(state["round"])

    return {
        "_deep_debug": classification,
        "prompt_debug_rounds": debug_rounds,
    }


async def node_fix_prompt(state: OptimizerState) -> dict:
    """根据深度诊断结果，LLM 生成 prompt 修复并写入 DB。"""
    classification = state.get("_deep_debug", {})
    prompt_issues = classification.get("prompt_issues", [])

    if not prompt_issues:
        logger.warning("[Round %d] PROMPT_FIX: no prompt issues, skipping")
        return {"_prompt_fix_info": {"skipped": True, "reason": "no prompt issues"}}

    tenant_id = state["tenant_id"]
    old_info = await _get_active_prompt_texts(tenant_id)

    if not old_info:
        logger.error("[Round %d] PROMPT_FIX: could not resolve current prompts, skipping")
        return {"_prompt_fix_info": {"skipped": True, "reason": "prompt resolution failed"}}

    # LLM 生成修复
    fix_result = await _generate_prompt_fix(old_info, prompt_issues, classification)
    if fix_result is None:
        return {"_prompt_fix_info": {"skipped": True, "reason": "LLM generation failed"}}

    # 确保可回滚
    category = fix_result["category"]
    key = fix_result["key"]
    rollback_id = await _ensure_rollback_target(tenant_id, category, key, old_info)

    # 写入 DB
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.prompt_registry import PromptRegistry

    factory = get_session_factory()
    try:
        async with factory() as db:
            registry = PromptRegistry(db)
            new_version = await registry.create_version(
                tenant_id=tenant_id,
                prompt_category=category,
                prompt_key=key,
                template_text=fix_result["new_template"],
                description=f"Optimizer auto-fix round {state['round']}: {fix_result.get('reasoning', '')[:200]}",
                required_placeholders=old_info.get("placeholders", []),
            )
            await registry.activate_version(tenant_id, new_version.id)
            await db.commit()
            new_version_id = new_version.id
    except Exception:
        logger.exception("[Round %d] PROMPT_FIX: DB write failed", state["round"])
        return {"_prompt_fix_info": {"skipped": True, "reason": "DB write failed"}}

    # 构建验证集
    fix_ids, regression_ids = _build_validation_set(prompt_issues, state)

    prompt_fix_info = {
        "category": category,
        "key": key,
        "old_version_id": rollback_id,
        "new_version_id": new_version_id,
        "reasoning": fix_result.get("reasoning", ""),
        "expected_improvement": fix_result.get("expected_improvement", ""),
        "fix_ids": fix_ids,
        "regression_ids": regression_ids,
    }

    logger.info(
        "[Round %d] PROMPT_FIX: %s/%s %s → %s (%d fix + %d regression questions)",
        state["round"], category, key,
        rollback_id[:12] if rollback_id else "builtin",
        new_version_id[:12],
        len(fix_ids), len(regression_ids),
    )

    return {"_prompt_fix_info": prompt_fix_info}


async def node_evaluate_validation(state: OptimizerState) -> dict:
    """在验证子集上运行评估。"""
    prompt_fix = state.get("_prompt_fix_info", {})
    if prompt_fix.get("skipped"):
        return {}

    fix_ids = prompt_fix.get("fix_ids", [])
    regression_ids = prompt_fix.get("regression_ids", [])
    all_ids = list(dict.fromkeys(fix_ids + regression_ids))

    if not all_ids:
        logger.warning("[Round %d] VALIDATION: empty question set, skipping")
        return {}

    # 从原始 golden 文件中筛选验证题目
    golden_file = state["golden_file"]
    validation_golden = _create_validation_golden(golden_file, all_ids)
    if validation_golden is None:
        logger.error("[Round %d] VALIDATION: failed to create validation golden", state["round"])
        return {}

    output_dir = str(
        Path(state["output_dir"]) / f"round_{state['round']:02d}_prompt_val" / "results"
    )

    logger.info(
        "[Round %d] VALIDATION: running eval on %d questions (%d fix + %d regression)",
        state["round"], len(all_ids), len(fix_ids), len(regression_ids),
    )

    try:
        val_metrics, _ = run_evaluate(
            tenant_id=state["tenant_id"],
            golden_file=validation_golden,
            output_dir=output_dir,
            limit=0,
        )
    except Exception:
        logger.exception("[Round %d] VALIDATION: evaluation failed", state["round"])
        return {"prompt_rollback_needed": True}

    return {
        "_prompt_fix_info": {
            **prompt_fix,
            "validation_metrics": val_metrics,
        },
    }


async def node_validate_prompt(state: OptimizerState) -> dict:
    """比较修前/修后指标，检测回归。"""
    prompt_fix = state.get("_prompt_fix_info", {})
    if prompt_fix.get("skipped"):
        return {}

    val_metrics: RoundMetrics | None = prompt_fix.get("validation_metrics")
    if val_metrics is None:
        return {"prompt_rollback_needed": True}

    # 修前指标: 从当前轮的完整评估中获取
    full_metrics: RoundMetrics = state["_metrics"]
    before_map = {s.question_id: s for s in full_metrics.per_question}
    after_map = {s.question_id: s for s in val_metrics.per_question}

    fix_ids = prompt_fix.get("fix_ids", [])
    regression_ids = prompt_fix.get("regression_ids", [])

    improvements = []
    regressions = []

    # 检查修复题目是否改善
    for qid in fix_ids:
        before = before_map.get(qid)
        after = after_map.get(qid)
        if before and after and not before.error and not after.error:
            before_score = before.correctness * 0.5 + before.faithfulness * 0.5
            after_score = after.correctness * 0.5 + after.faithfulness * 0.5
            if after_score > before_score + 0.1:
                improvements.append(qid)

    # 检查回归
    for qid in regression_ids:
        before = before_map.get(qid)
        after = after_map.get(qid)
        if before and after and not before.error and not after.error:
            if (after.correctness < max(before.correctness - 0.15, 0.4)
                    or after.faithfulness < max(before.faithfulness - 0.15, 0.4)):
                regressions.append(qid)

    needs_rollback = len(regressions) > 0

    logger.info(
        "[Round %d] PROMPT VALIDATION: %d/%d improved, %d/%d regressed → %s",
        state["round"],
        len(improvements), len(fix_ids),
        len(regressions), len(regression_ids),
        "ROLLBACK" if needs_rollback else "PASS",
    )

    if improvements:
        logger.info("[Round %d]   improved: %s", state["round"], improvements)
    if regressions:
        logger.warning("[Round %d]   regressed: %s", state["round"], regressions)

    return {
        "prompt_rollback_needed": needs_rollback,
        "_prompt_fix_info": {
            **prompt_fix,
            "validation_complete": True,
            "improvements": improvements,
            "regressions": regressions,
        },
    }


async def node_rollback_prompt(state: OptimizerState) -> dict:
    """回滚: 恢复旧 prompt 版本。"""
    prompt_fix = state.get("_prompt_fix_info", {})
    tenant_id = state["tenant_id"]
    old_id = prompt_fix.get("old_version_id")
    new_id = prompt_fix.get("new_version_id")

    if not old_id:
        logger.error("[Round %d] ROLLBACK: no old version to restore", state["round"])
        return {"prompt_rollback_needed": False}

    from sales_agent.core.database import get_session_factory
    from sales_agent.services.prompt_registry import PromptRegistry

    factory = get_session_factory()
    try:
        async with factory() as db:
            registry = PromptRegistry(db)
            await registry.activate_version(tenant_id, old_id)
            if new_id:
                try:
                    await registry.archive_version(tenant_id, new_id)
                except Exception:
                    pass
            await db.commit()
    except Exception:
        logger.exception("[Round %d] ROLLBACK: failed to restore old version", state["round"])
        return {"prompt_rollback_needed": False}

    logger.warning(
        "[Round %d] PROMPT ROLLBACK: %s/%s restored to %s",
        state["round"],
        prompt_fix.get("category", "?"),
        prompt_fix.get("key", "?"),
        old_id[:12],
    )

    return {
        "prompt_rollback_needed": False,
        "_rollback_info": {
            "rolled_back_category": prompt_fix.get("category"),
            "rolled_back_key": prompt_fix.get("key"),
            "restored_version_id": old_id,
            "reason": f"Regression: {len(prompt_fix.get('regressions', []))} questions degraded",
        },
    }


# ── Internal helpers ──────────────────────────────────────────────────────


async def _get_active_prompt_texts(tenant_id: str) -> dict[str, Any] | None:
    """获取当前 system_constraint 和 knowledge_qa 的 active 文本。"""
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.prompt_registry import PromptRegistry

    factory = get_session_factory()
    try:
        async with factory() as db:
            registry = PromptRegistry(db)
            system_text = await registry.resolve_prompt("system", "system_constraint", tenant_id)
            task_text = await registry.resolve_prompt("task", "knowledge_qa", tenant_id)
            return {
                "system_constraint": system_text,
                "knowledge_qa": task_text,
            }
    except Exception:
        logger.exception("Failed to resolve active prompts for %s", tenant_id)
        return None


async def _generate_prompt_fix(
    old_info: dict,
    prompt_issues: list[MetricSnapshot],
    classification: dict,
) -> dict | None:
    """调用 LLM 生成 prompt 修复方案。"""
    from eval.deepeval_metrics import get_judge_model

    # 构建低分题格式化文本
    cases_text = ""
    for i, snap in enumerate(prompt_issues[:5], 1):
        cases_text += (
            f"\n### Case {i}: {snap.question_id}\n"
            f"- Question: {snap.question[:200]}\n"
            f"- ContextualRecall: {snap.contextual_recall:.2f}\n"
            f"- ContextualRelevancy: {snap.contextual_relevancy:.2f}\n"
            f"- Faithfulness: {snap.faithfulness:.2f}\n"
            f"- Correctness: {snap.correctness:.2f}\n"
            f"- Agent answer: {(snap.answer or '')[:300]}\n"
            f"- Reference: {(snap.reference or '')[:300]}\n"
        )

    rejection_info = ""
    if classification.get("prompt_hits", 0) > 0:
        rejection_info = "\n## ⚠️ 检测到拒绝模式\n\nAgent 的回答中包含拒绝/回避语言，说明 Agent 忽略了检索到的知识库内容。\n"

    prompt = f"""你是一个 AI Agent prompt 调试专家。系统的 Agent 在检索成功获取到正确信息的情况下，仍然给出了低质量回答。你需要分析失败原因并修复相关 prompt。

## 当前 active 的 prompt

### system/system_constraint:
```
{old_info.get('system_constraint', '(unavailable)')}
```

### task/knowledge_qa:
```
{old_info.get('knowledge_qa', '(unavailable)')}
```

## 失败题目（检索成功但回答失败）
{rejection_info}
{cases_text}

## 任务

决定修改哪个 prompt（system/system_constraint 或 task/knowledge_qa）并生成修复版本。

规则:
1. 如果 Agent 拒绝回答或忽略检索内容 → 加强 system_constraint，强调检索到的内容必须使用
2. 如果 Agent 误解问题或生成错误回答 → 修正 knowledge_qa 的指令
3. **不要改变** prompt 的占位符 ({{message}}, {{context_block}}, {{retrieval_content}})
4. 新 template 必须是**完整**的替换文本
5. 保持简洁，只在关键位置添加 1-2 句明确指令

只返回 JSON（无 markdown 代码块）:
{{"category": "system" 或 "task", "key": "system_constraint" 或 "knowledge_qa", "new_template": "完整的修复后 prompt 文本", "reasoning": "问题分析", "expected_improvement": "预期改善"}}"""

    for attempt in range(3):
        try:
            model = get_judge_model()
            raw, cost = await model.a_generate(prompt=prompt)
            # 尝试多种解析方式
            result = None
            for parser in [_parse_json_strict, _parse_json_fenced, _parse_json_loose]:
                result = parser(raw)
                if result:
                    break

            if result and all(k in result for k in ("category", "key", "new_template")):
                logger.info("PROMPT_FIX LLM: category=%s key=%s (attempt %d)",
                           result["category"], result["key"], attempt + 1)
                return result
        except Exception:
            logger.warning("PROMPT_FIX LLM attempt %d failed", attempt + 1, exc_info=True)

    return None


def _parse_json_strict(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_json_fenced(raw: str) -> dict | None:
    import re
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
    if m:
        return _parse_json_strict(m.group(1))
    return None


def _parse_json_loose(raw: str) -> dict | None:
    import re
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


async def _ensure_rollback_target(
    tenant_id: str,
    category: str,
    key: str,
    old_info: dict,
) -> str | None:
    """确保有可回滚的目标版本。

    - 如果已有 active tenant 版本 → 返回其 ID
    - 如果只有 builtin → 从 builtin 创建快照并激活，返回其 ID
    """
    from sales_agent.core.database import get_session_factory
    from sales_agent.services.prompt_registry import PromptRegistry

    factory = get_session_factory()
    try:
        async with factory() as db:
            registry = PromptRegistry(db)
            active_id = await _get_active_version_id(db, tenant_id, category, key)
            if active_id:
                return active_id

            # 从 old_info 中的 builtin 文本创建快照
            builtin_text = old_info.get(key, "")
            if not builtin_text:
                return None

            snapshot = await registry.create_version(
                tenant_id=tenant_id,
                prompt_category=category,
                prompt_key=key,
                template_text=builtin_text,
                description=f"Auto-snapshot before optimizer prompt fix",
            )
            await registry.activate_version(tenant_id, snapshot.id)
            await db.commit()
            logger.info("Created rollback snapshot: %s/%s → %s", category, key, snapshot.id[:12])
            return snapshot.id
    except Exception:
        logger.exception("Failed to ensure rollback target")
        return None


async def _get_active_version_id(
    db, tenant_id: str, category: str, key: str
) -> str | None:
    """查询 tenant 的 active prompt version ID。"""
    from sqlalchemy import select
    from sales_agent.models.prompt import PromptVersion

    stmt = select(PromptVersion.id).where(
        PromptVersion.tenant_id == tenant_id,
        PromptVersion.prompt_category == category,
        PromptVersion.prompt_key == key,
        PromptVersion.status == "active",
    )
    result = await db.execute(stmt)
    row = result.first()
    return row[0] if row else None


def _build_validation_set(
    prompt_issues: list[MetricSnapshot],
    state: OptimizerState,
) -> tuple[list[str], list[str]]:
    """构建验证集: (fix_ids, regression_ids)。"""
    fix_ids = [snap.question_id for snap in prompt_issues[:5]]

    # 从历史中找通过的题目作为回归测试
    history = state.get("history", [])
    regression_ids = []
    seen = set(fix_ids)
    for action in reversed(history):
        if not action.metrics_before:
            continue
        for snap in action.metrics_before.per_question:
            if (snap.question_id not in seen
                    and not snap.error
                    and snap.correctness >= 0.7):
                regression_ids.append(snap.question_id)
                seen.add(snap.question_id)
                if len(regression_ids) >= 5:
                    break
        if len(regression_ids) >= 5:
            break

    return fix_ids, regression_ids


def _create_validation_golden(golden_file: str, question_ids: list[str]) -> str | None:
    """从原始 golden 文件中筛选指定 ID 的题目，写入临时文件。"""
    try:
        with open(golden_file, "r", encoding="utf-8") as f:
            all_goldens = json.load(f)
    except Exception:
        logger.exception("Failed to read golden file: %s", golden_file)
        return None

    id_set = set(question_ids)
    if isinstance(all_goldens, list):
        filtered = [g for g in all_goldens if g.get("id") in id_set]
    elif isinstance(all_goldens, dict):
        details = all_goldens.get("details", all_goldens.get("results", []))
        filtered = [g for g in details if g.get("id") in id_set]
    else:
        return None

    if not filtered:
        return None

    fd, path = tempfile.mkstemp(suffix=".json", prefix="val_golden_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False)

    return path
