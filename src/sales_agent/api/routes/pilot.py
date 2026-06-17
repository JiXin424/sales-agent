"""Pilot 验证与质量循环 API：指标、审查、知识缺口、评估、告警、报告。"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant_id}/admin/pilot", tags=["pilot"])


async def _verify_tenant(tenant_id: str, db: AsyncSession) -> None:
    """校验租户存在。"""
    resolver = TenantResolver(db)
    try:
        await resolver.resolve(tenant_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")


# ============================================================
# R2: Pilot 成功指标
# ============================================================


@router.get("/pilot-metrics")
async def get_pilot_metrics(
    tenant_id: str,
    db: DbSession,
    start_date: str = Query(..., description="ISO date, e.g. 2026-01-01"),
    end_date: str = Query(..., description="ISO date, e.g. 2026-06-11"),
):
    """获取 Pilot 成功指标。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.pilot_metrics_service import PilotMetricsService
    svc = PilotMetricsService(db)
    return await svc.get_metrics(tenant_id, start_date, end_date)


# ============================================================
# R3: 质量审查队列
# ============================================================


@router.post("/review-queue/scan")
async def scan_review_queue(
    tenant_id: str,
    db: DbSession,
):
    """扫描并创建审查条目。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.review_queue_service import ReviewQueueService
    svc = ReviewQueueService(db)
    count = await svc.scan_and_enqueue(tenant_id)
    return {"created": count}


@router.get("/review-queue")
async def list_review_queue(
    tenant_id: str,
    db: DbSession,
    status: str | None = Query(None),
    reason: str | None = Query(None),
    priority: str | None = Query(None),
    assignee: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出审查队列条目。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.review_queue_service import ReviewQueueService
    svc = ReviewQueueService(db)
    items, total = await svc.list_items(tenant_id, status, reason, priority, assignee, limit, offset)
    return {
        "items": [_review_item_to_dict(i) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/review-queue")
async def create_review_item(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """手动创建审查条目。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.review_queue_service import ReviewQueueService
    svc = ReviewQueueService(db)
    try:
        item = await svc.create_manual(
            tenant_id=tenant_id,
            conversation_id=body["conversation_id"],
            reason=body.get("reason", "manual_flag"),
            priority=body.get("priority", "medium"),
            notes=body.get("notes"),
            assignee=body.get("assignee"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _review_item_to_dict(item)


@router.get("/review-queue/{item_id}")
async def get_review_item(
    tenant_id: str,
    item_id: str,
    db: DbSession,
):
    """获取审查条目详情。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.review_queue_service import ReviewQueueService
    svc = ReviewQueueService(db)
    item = await svc.get_item(tenant_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return _review_item_to_dict(item)


@router.patch("/review-queue/{item_id}")
async def update_review_item(
    tenant_id: str,
    item_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """更新审查条目状态。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.review_queue_service import ReviewQueueService
    svc = ReviewQueueService(db)
    try:
        item = await svc.update_status(
            tenant_id=tenant_id,
            item_id=item_id,
            status=body["status"],
            assignee=body.get("assignee"),
            notes=body.get("notes"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _review_item_to_dict(item)


# ============================================================
# R4: 反馈分类
# ============================================================


@router.patch("/feedback/{feedback_id}/classify")
async def classify_feedback(
    tenant_id: str,
    feedback_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """为反馈分配根因分类。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.feedback_classification_service import FeedbackClassificationService
    svc = FeedbackClassificationService(db)
    try:
        fb = await svc.classify(tenant_id, feedback_id, body["categories"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "feedback_id": fb.id, "categories": json.loads(fb.categories_json)}


@router.get("/feedback-categories/summary")
async def get_feedback_categories_summary(
    tenant_id: str,
    db: DbSession,
):
    """获取反馈根因分类汇总。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.feedback_classification_service import FeedbackClassificationService
    svc = FeedbackClassificationService(db)
    return await svc.get_categories_summary(tenant_id)


@router.get("/feedback-categories/{category}")
async def list_feedback_by_category(
    tenant_id: str,
    category: str,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """按分类列出反馈。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.feedback_classification_service import FeedbackClassificationService
    svc = FeedbackClassificationService(db)
    try:
        items, total = await svc.list_by_category(tenant_id, category, limit, offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "items": [_feedback_to_dict(fb) for fb in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
# R5: 知识缺口
# ============================================================


@router.get("/knowledge-gaps")
async def list_knowledge_gaps(
    tenant_id: str,
    db: DbSession,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出知识缺口。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    items, total = await svc.list_gaps(tenant_id, status, limit, offset)
    return {
        "items": [_gap_to_dict(g) for g in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/knowledge-gaps")
async def create_knowledge_gap(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """创建知识缺口。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    try:
        gap = await svc.create(
            tenant_id=tenant_id,
            title=body["title"],
            description=body.get("description"),
            source_conversation_id=body.get("source_conversation_id"),
            source_feedback_id=body.get("source_feedback_id"),
            priority=body.get("priority", "medium"),
            keywords=body.get("keywords"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _gap_to_dict(gap)


@router.get("/knowledge-gaps/{gap_id}")
async def get_knowledge_gap(
    tenant_id: str,
    gap_id: str,
    db: DbSession,
):
    """获取知识缺口详情。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    gap = await svc.get_gap(tenant_id, gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="Knowledge gap not found")
    return _gap_to_dict(gap)


@router.patch("/knowledge-gaps/{gap_id}")
async def transition_knowledge_gap(
    tenant_id: str,
    gap_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """转换知识缺口状态。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    try:
        gap = await svc.transition(tenant_id, gap_id, body["status"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _gap_to_dict(gap)


@router.post("/knowledge-gaps/{gap_id}/link-document")
async def link_document_to_gap(
    tenant_id: str,
    gap_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """关联文档到知识缺口。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    try:
        gap = await svc.link_document(tenant_id, gap_id, body["document_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _gap_to_dict(gap)


@router.get("/knowledge-gaps-summary")
async def get_knowledge_gaps_summary(
    tenant_id: str,
    db: DbSession,
):
    """获取知识缺口汇总。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.knowledge_gap_service import KnowledgeGapService
    svc = KnowledgeGapService(db)
    return await svc.get_summary(tenant_id)


# ============================================================
# R6: 评估回归
# ============================================================


@router.get("/eval-suites")
async def list_eval_suites(
    tenant_id: str,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出评估套件。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    items, total = await svc.list_suites(tenant_id, limit, offset)
    return {
        "items": [_eval_suite_to_dict(s) for s in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/eval-suites")
async def create_eval_suite(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """从 JSONL fixture 加载评估套件。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    try:
        suite = await svc.load_suite(
            tenant_id=tenant_id,
            name=body["name"],
            fixture_path=body["fixture_path"],
            description=body.get("description"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _eval_suite_to_dict(suite)


@router.post("/eval-suites/{suite_id}/run")
async def run_eval_suite(
    tenant_id: str,
    suite_id: str,
    db: DbSession,
    body: dict[str, Any] | None = None,
):
    """运行评估套件。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    try:
        prompt_version_id = body.get("prompt_version_id") if body else None
        run = await svc.run_suite(tenant_id, suite_id, prompt_version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _eval_run_to_dict(run)


@router.get("/eval-runs")
async def list_eval_runs(
    tenant_id: str,
    db: DbSession,
    eval_suite_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出评估运行记录。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    items, total = await svc.list_runs(tenant_id, eval_suite_id, limit, offset)
    return {
        "items": [_eval_run_to_dict(r) for r in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/eval-runs/{run_id}")
async def get_eval_run(
    tenant_id: str,
    run_id: str,
    db: DbSession,
):
    """获取评估运行详情。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    run = await svc.get_run(tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return _eval_run_to_dict(run)


@router.get("/eval-runs/{run_id}/results")
async def get_eval_run_results(
    tenant_id: str,
    run_id: str,
    db: DbSession,
):
    """获取评估运行的所有结果。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.eval_runner_service import EvalRunnerService
    svc = EvalRunnerService(db)
    results = await svc.get_run_results(tenant_id, run_id)
    return {
        "items": [_eval_result_to_dict(r) for r in results],
        "total": len(results),
    }


# ============================================================
# R7: 变更对比
# ============================================================


@router.post("/compare")
async def compare_eval_runs(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """对比两次评估运行结果。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.change_comparison_service import ChangeComparisonService
    svc = ChangeComparisonService(db)
    return await svc.compare_eval_runs(
        tenant_id, body["before_run_id"], body["after_run_id"]
    )


@router.post("/compare-review-outcomes")
async def compare_review_outcomes(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """对比变更前后的审查条目分布。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.change_comparison_service import ChangeComparisonService
    svc = ChangeComparisonService(db)
    return await svc.compare_review_outcomes(
        tenant_id, body["before_date"], body["after_date"]
    )


@router.post("/compare-document-change")
async def compare_document_change(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """对比文档变更前后的质量变化。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.change_comparison_service import ChangeComparisonService
    svc = ChangeComparisonService(db)
    return await svc.compare_document_change(
        tenant_id, body["before_date"], body["after_date"]
    )


# ============================================================
# R8: 运维告警
# ============================================================


@router.get("/alert-rules")
async def list_alert_rules(
    tenant_id: str,
    db: DbSession,
    enabled_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出告警规则。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    items, total = await svc.list_rules(tenant_id, enabled_only, limit, offset)
    return {
        "items": [_alert_rule_to_dict(r) for r in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/alert-rules")
async def create_alert_rule(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """创建告警规则。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    try:
        rule = await svc.create_rule(
            tenant_id=tenant_id,
            name=body["name"],
            metric=body["metric"],
            threshold=body["threshold"],
            condition=body.get("condition", "gt"),
            window_minutes=body.get("window_minutes", 60),
            severity=body.get("severity", "warning"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _alert_rule_to_dict(rule)


@router.patch("/alert-rules/{rule_id}")
async def update_alert_rule(
    tenant_id: str,
    rule_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """更新告警规则。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    try:
        rule = await svc.update_rule(tenant_id, rule_id, **body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _alert_rule_to_dict(rule)


@router.post("/alert-rules/seed-defaults")
async def seed_default_alert_rules(
    tenant_id: str,
    db: DbSession,
):
    """创建默认告警规则。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    rules = await svc.seed_default_rules(tenant_id)
    return {"created": len(rules), "rules": [_alert_rule_to_dict(r) for r in rules]}


@router.get("/alerts")
async def list_alerts(
    tenant_id: str,
    db: DbSession,
    status: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出告警记录。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    items, total = await svc.list_alerts(tenant_id, status, severity, limit, offset)
    return {
        "items": [_alert_to_dict(a) for a in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/alerts/evaluate")
async def evaluate_alerts(
    tenant_id: str,
    db: DbSession,
):
    """评估所有告警规则。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    alerts = await svc.evaluate_rules(tenant_id)
    return {"triggered": len(alerts), "alerts": [_alert_to_dict(a) for a in alerts]}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    tenant_id: str,
    alert_id: str,
    db: DbSession,
):
    """确认告警。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    try:
        alert = await svc.acknowledge_alert(tenant_id, alert_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _alert_to_dict(alert)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    tenant_id: str,
    alert_id: str,
    db: DbSession,
):
    """解决告警。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.alert_service import AlertService
    svc = AlertService(db)
    try:
        alert = await svc.resolve_alert(tenant_id, alert_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _alert_to_dict(alert)


# ============================================================
# R9: Pilot 报告
# ============================================================


@router.post("/reports/generate")
async def generate_report(
    tenant_id: str,
    db: DbSession,
    body: dict[str, Any],
):
    """生成 Pilot 报告。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.pilot_report_service import PilotReportService
    svc = PilotReportService(db)
    report = await svc.generate_report(
        tenant_id,
        body["start_date"],
        body["end_date"],
        body.get("report_type", "weekly"),
    )
    return _report_to_dict(report)


@router.get("/reports")
async def list_reports(
    tenant_id: str,
    db: DbSession,
    report_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出 Pilot 报告。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.pilot_report_service import PilotReportService
    svc = PilotReportService(db)
    items, total = await svc.list_reports(tenant_id, report_type, limit, offset)
    return {
        "items": [_report_to_dict(r) for r in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/reports/{report_id}")
async def get_report(
    tenant_id: str,
    report_id: str,
    db: DbSession,
):
    """获取报告详情。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.pilot_report_service import PilotReportService
    svc = PilotReportService(db)
    report = await svc.get_report(tenant_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_to_dict(report)


# ============================================================
# R10: Pilot 退出决策
# ============================================================


@router.get("/status")
async def get_pilot_status(
    tenant_id: str,
    db: DbSession,
):
    """获取 Pilot 退出决策状态。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.pilot_status_service import PilotStatusService
    svc = PilotStatusService(db)
    return await svc.get_pilot_status(tenant_id)


# ============================================================
# 序列化辅助函数
# ============================================================


def _review_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "conversation_id": item.conversation_id,
        "feedback_id": item.feedback_id,
        "agent_run_id": item.agent_run_id,
        "reason": item.reason,
        "priority": item.priority,
        "status": item.status,
        "assignee": item.assignee,
        "notes": json.loads(item.notes_json) if item.notes_json else {},
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _feedback_to_dict(fb: Any) -> dict[str, Any]:
    return {
        "id": fb.id,
        "tenant_id": fb.tenant_id,
        "conversation_id": fb.conversation_id,
        "user_id": fb.user_id,
        "rating": fb.rating,
        "feedback_text": fb.feedback_text,
        "labels": json.loads(fb.labels_json) if fb.labels_json else [],
        "categories": json.loads(fb.categories_json) if fb.categories_json else [],
        "review_status": fb.review_status,
        "created_at": fb.created_at,
    }


def _gap_to_dict(gap: Any) -> dict[str, Any]:
    return {
        "id": gap.id,
        "tenant_id": gap.tenant_id,
        "source_conversation_id": gap.source_conversation_id,
        "source_feedback_id": gap.source_feedback_id,
        "linked_document_id": gap.linked_document_id,
        "title": gap.title,
        "description": gap.description,
        "status": gap.status,
        "priority": gap.priority,
        "keywords": json.loads(gap.keywords_json) if gap.keywords_json else [],
        "created_at": gap.created_at,
        "updated_at": gap.updated_at,
    }


def _eval_suite_to_dict(suite: Any) -> dict[str, Any]:
    return {
        "id": suite.id,
        "tenant_id": suite.tenant_id,
        "name": suite.name,
        "description": suite.description,
        "fixture_path": suite.fixture_path,
        "case_count": suite.case_count,
        "status": suite.status,
        "created_at": suite.created_at,
        "updated_at": suite.updated_at,
    }


def _eval_run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "tenant_id": run.tenant_id,
        "eval_suite_id": run.eval_suite_id,
        "status": run.status,
        "total_cases": run.total_cases,
        "passed": run.passed,
        "failed": run.failed,
        "skipped": run.skipped,
        "prompt_version_id": run.prompt_version_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_summary": run.error_summary,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _eval_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "id": result.id,
        "eval_case_id": result.eval_case_id,
        "conversation_id": result.conversation_id,
        "passed": result.passed == "true",
        "actual_task_type": result.actual_task_type,
        "actual_risk_level": result.actual_risk_level,
        "route_match": result.route_match == "true",
        "content_checks": json.loads(result.content_checks_json) if result.content_checks_json else {},
        "failure_reasons": json.loads(result.failure_reasons_json) if result.failure_reasons_json else [],
        "latency_ms": result.latency_ms,
        "created_at": result.created_at,
    }


def _alert_rule_to_dict(rule: Any) -> dict[str, Any]:
    return {
        "id": rule.id,
        "tenant_id": rule.tenant_id,
        "name": rule.name,
        "metric": rule.metric,
        "condition": rule.condition,
        "threshold": rule.threshold,
        "window_minutes": rule.window_minutes,
        "severity": rule.severity,
        "enabled": rule.enabled == "true",
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _alert_to_dict(alert: Any) -> dict[str, Any]:
    return {
        "id": alert.id,
        "tenant_id": alert.tenant_id,
        "alert_rule_id": alert.alert_rule_id,
        "severity": alert.severity,
        "metric": alert.metric,
        "threshold_value": alert.threshold_value,
        "observed_value": alert.observed_value,
        "status": alert.status,
        "first_seen_at": alert.first_seen_at,
        "last_seen_at": alert.last_seen_at,
        "created_at": alert.created_at,
        "updated_at": alert.updated_at,
    }


def _report_to_dict(report: Any) -> dict[str, Any]:
    return {
        "id": report.id,
        "tenant_id": report.tenant_id,
        "report_type": report.report_type,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "status": report.status,
        "summary": json.loads(report.summary_json) if report.summary_json else {},
        "report": json.loads(report.report_json) if report.report_json else {},
        "markdown_content": report.markdown_content,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }
