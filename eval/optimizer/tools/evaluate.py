"""
评估节点：调用 deepeval_eval.py 对 Golden 题目跑评估，解析结果。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from eval.optimizer.state import MetricSnapshot, RoundMetrics


def _resolve_knowledge_engine(tenant_id: str, cwd: str | Path | None = None) -> str:
    """从 deploy/tenants.json 解析租户对应的 knowledge_engine。"""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    tenants_file = repo_root / "deploy" / "tenants.json"
    try:
        data = json.loads(tenants_file.read_text(encoding="utf-8"))
        for t in data.get("tenants", []):
            if t.get("id") == tenant_id:
                return t.get("knowledge_engine", "legacy_rag")
    except Exception:
        pass
    return "legacy_rag"


def run_evaluate(
    tenant_id: str,
    golden_file: str,
    output_dir: str,
    limit: int = 0,
    *,
    cwd: str | Path | None = None,
) -> tuple[RoundMetrics, str]:
    """运行 deepeval_eval.py，返回 (RoundMetrics, result_json_path)。

    Args:
        tenant_id: 租户 ID
        golden_file: Golden 文件路径（.json/.csv/.md）
        output_dir: 结果输出目录
        limit: 限制题数（0 = 全部）
        cwd: 工作目录

    Returns:
        (RoundMetrics, result_json_path)
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if cwd is None:
        cwd = repo_root

    script = repo_root / "eval" / "deepeval_eval.py"
    cmd = [
        sys.executable, str(script),
        "--tenant-id", tenant_id,
        "--golden-file", str(golden_file),
        "--output-dir", str(output_dir),
        "--concurrency", "3",
    ]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])

    # 加载 .env + 租户 secrets（Neo4j 等配置）
    env_file = Path(cwd) / ".env"
    env: dict[str, str] = {}
    for ef in [env_file, Path(cwd) / "secrets" / f"{tenant_id}.env"]:
        if ef.is_file():
            for line in ef.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    if env:
        import os
        full_env = os.environ.copy()
        full_env.update(env)
        # 设 TENANT_ID + 覆盖 KNOWLEDGE_ENGINE（从 tenants.json 读取）
        full_env["TENANT_ID"] = tenant_id
        _engine = _resolve_knowledge_engine(tenant_id, cwd)
        if _engine:
            full_env["KNOWLEDGE_ENGINE"] = _engine
        # 修正 Docker 内部 hostname → localhost（宿主机运行 eval 时）
        neo4j_uri = full_env.get("NEO4J_URI", "")
        if neo4j_uri and "://neo4j:" in neo4j_uri:
            full_env["NEO4J_URI"] = neo4j_uri.replace("://neo4j:", "://localhost:")
        env = full_env

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"deepeval_eval.py failed (rc={result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-2000:]}"
        )

    stdout = result.stdout

    # 解析结果 JSON 路径
    json_result_path = ""
    for line in stdout.splitlines():
        if "[JSON]" in line:
            json_result_path = line.split("]")[-1].strip()
            break

    if not json_result_path or not Path(json_result_path).is_file():
        raise RuntimeError(f"Cannot find eval result JSON from output:\n{stdout[-1000:]}")

    return _parse_eval_result(json_result_path), json_result_path


def _parse_eval_result(json_path: str) -> RoundMetrics:
    """解析 DeepEval 结果 JSON，提取汇总指标和逐题指标。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    # data 可能是 list[dict] 或 {"details": [...]} 或 {"results": [...]}
    if isinstance(data, dict):
        results_list = data.get("details") or data.get("results") or []
    else:
        results_list = data

    per_question: list[MetricSnapshot] = []
    total = len(results_list)
    success = 0

    for item in results_list:
        question_id = item.get("question_id", item.get("id", ""))
        question = item.get("question", item.get("input", ""))
        source_file = item.get("source_file", "")
        answer = item.get("actual_output", item.get("answer", ""))
        reference = item.get("expected_output", item.get("reference", ""))
        error = item.get("error", "")
        metric_data = item.get("metricsData") or item.get("metric_scores") or {}

        snapshot = MetricSnapshot(
            question_id=question_id,
            question=question[:200],
            source_file=source_file,
            answer=answer[:300] if answer else "",
            reference=reference[:300] if reference else "",
            contextual_relevancy=_safe_float(metric_data.get("Contextual Relevancy", 0)),
            contextual_recall=_safe_float(metric_data.get("Contextual Recall", 0)),
            contextual_precision=_safe_float(metric_data.get("Contextual Precision", 0)),
            faithfulness=_safe_float(metric_data.get("Faithfulness", 0)),
            correctness=_safe_float(_find_correctness(metric_data)),
            completeness=_safe_float(_find_completeness(metric_data)),
            answer_relevancy=_safe_float(metric_data.get("Answer Relevancy", 0)),
            task_completion=_safe_float(metric_data.get("Task Completion", 0)),
            answer_recall=_safe_float(metric_data.get("AnswerRecall (回答召回率)", 0)),
            error=error,
        )
        per_question.append(snapshot)
        if not error:
            success += 1

    # 汇总指标
    scores = _compute_avg_scores(per_question, total)
    # 质量 pass_rate：生成质量为主（Correctness + Faithfulness），检索质量为辅
    quality_pass = (
        scores.get("avg_correctness", 0) * 0.40
        + scores.get("avg_faithfulness", 0) * 0.30
        + scores.get("avg_contextual_recall", 0) * 0.15
        + scores.get("avg_contextual_relevancy", 0) * 0.15
    )
    pass_rate = quality_pass

    return RoundMetrics(
        round=0,  # 由调用者设置
        total=total,
        success=success,
        pass_rate=pass_rate,
        per_question=per_question,
        **scores,
    )


def _safe_float(val) -> float:
    """安全转 float，None → 0.0。"""
    try:
        if val is None:
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _find_correctness(metric_data: dict) -> float:
    """查找正确性分数（可能有中英文 key）。"""
    for key in ("正确性 (Correctness) [GEval]", "Correctness (GEval)", "Correctness", "correctness"):
        if key in metric_data:
            return metric_data[key]
    return 0.0


def _find_completeness(metric_data: dict) -> float:
    """查找完整性分数。"""
    for key in ("完整性 (Completeness) [GEval]", "Completeness (GEval)", "Completeness", "completeness"):
        if key in metric_data:
            return metric_data[key]
    return 0.0


def _compute_avg_scores(per_question: list[MetricSnapshot], total: int) -> dict[str, float]:
    """计算各指标均值。"""
    if total == 0:
        return {}
    sums: dict[str, float] = {}
    for snap in per_question:
        for field_name in [
            "contextual_relevancy", "contextual_recall", "contextual_precision",
            "faithfulness", "correctness", "completeness",
            "answer_relevancy", "task_completion", "answer_recall",
        ]:
            val = getattr(snap, field_name, 0.0) or 0.0
            sums[field_name] = sums.get(field_name, 0.0) + val

    return {
        "avg_contextual_recall": round(sums.get("contextual_recall", 0) / total, 4),
        "avg_contextual_relevancy": round(sums.get("contextual_relevancy", 0) / total, 4),
        "avg_contextual_precision": round(sums.get("contextual_precision", 0) / total, 4),
        "avg_faithfulness": round(sums.get("faithfulness", 0) / total, 4),
        "avg_correctness": round(sums.get("correctness", 0) / total, 4),
        "avg_completeness": round(sums.get("completeness", 0) / total, 4),
        "avg_answer_relevancy": round(sums.get("answer_relevancy", 0) / total, 4),
        "avg_answer_recall": round(sums.get("answer_recall", 0) / total, 4),
        "avg_task_completion": round(sums.get("task_completion", 0) / total, 4),
        "avg_latency_ms": 0.0,
    }
