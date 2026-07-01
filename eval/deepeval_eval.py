#!/usr/bin/env python3
"""
Sales Agent DeepEval 评估脚本 —— 直接走 ChatPipeline（模拟钉钉用户流式链路）+ LLM-as-a-Judge 自动评分。

不再通过 HTTP API 调 Agent，而是与钉钉用户走完全相同的 ChatPipeline 代码路径。

功能:
  1. 从 questions.md + ground_truth_30q.json 加载问题
  2. 直接调用 ChatPipeline.execute()（channel=dingtalk_single），获取回答
  3. DingTalkMessageRenderer 渲染 → 评估"用户实际看到的内容"
  4. DeepEval 指标自动评分 + Arena 盲测
  5. 每 5 题写 checkpoint, 支持 --resume 恢复
  6. 输出 JSON / Markdown / CSV / HTML 四格式报告 + TTFT + Token

用法:
  # 单租户评估（5 题冒烟）
  python eval/deepeval_eval.py --tenant-id taishan --limit 5

  # 单租户完整评估
  python eval/deepeval_eval.py --tenant-id taishan

  # 两租户对比
  python eval/deepeval_eval.py --tenant-id taishan --tenant-id-2 taishankaifa2 --label-2 "泰山开发"

  # 指定 Agent
  python eval/deepeval_eval.py --tenant-id taishan --agent-id <uuid>
"""

from __future__ import annotations
import argparse, asyncio, json, re, sys, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from deepeval import evaluate
from deepeval.evaluate.types import EvaluationResult
from deepeval_test_cases import (
    QuestionItem, build_llm_test_case, call_agent_pipeline, load_all_questions, init_eval_db,
)
from deepeval_metrics import (
    get_judge_model, get_metrics_for_question, run_arena_comparison, set_global_judge_model,
)
from deepeval_html_report import generate_html_report

@dataclass
class SingleResult:
    question_id: str; question: str; reference: str; kb_label: str; model: str
    answer: str; rendered: str; task_type: str; risk_level: str; sources_count: int
    latency_ms: int; ttft_ms: int
    prompt_tokens: int; completion_tokens: int; total_tokens: int; error: str
    metric_scores: dict = field(default_factory=dict)
    metric_reasons: dict = field(default_factory=dict)
    metric_success: dict = field(default_factory=dict)

@dataclass
class KBComparison:
    label: str; model: str; total: int = 0; success: int = 0; errors: int = 0
    avg_scores: dict = field(default_factory=dict)
    pass_rates: dict = field(default_factory=dict)
    avg_latency_ms: float = 0.0; avg_ttft_ms: float = 0.0
    total_prompt_tokens: int = 0; total_completion_tokens: int = 0; total_tokens: int = 0

# ── checkpoint ──
def _write_checkpoint(path, kb_label, model, results, questions, skip_ids):
    data = {
        "kb_label": kb_label, "model": model,
        "completed_ids": sorted(set(r.question_id for r in results) | skip_ids),
        "total_questions": len(questions),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": [{
            "question_id": r.question_id, "question": r.question[:200],
            "answer": r.answer[:500], "rendered": r.rendered[:500],
            "latency_ms": r.latency_ms, "ttft_ms": r.ttft_ms,
            "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens, "error": r.error,
            "metric_scores": {k: round(v,4) for k,v in r.metric_scores.items()},
            "metric_success": r.metric_success,
        } for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  💾 checkpoint: {path.name} ({len(data['completed_ids'])}/{len(questions)})")

def _load_checkpoint(path):
    if not path.exists(): return set(), []
    data = json.loads(path.read_text(encoding="utf-8"))
    completed = set(data.get("completed_ids", []))
    results = []
    for rd in data.get("results", []):
        results.append(SingleResult(
            question_id=rd.get("question_id",""), question=rd.get("question",""),
            reference="", kb_label=data.get("kb_label",""), model=data.get("model",""),
            answer=rd.get("answer",""), rendered=rd.get("rendered",""),
            task_type="", risk_level="", sources_count=0,
            latency_ms=rd.get("latency_ms",0), ttft_ms=rd.get("ttft_ms",0),
            prompt_tokens=rd.get("prompt_tokens",0), completion_tokens=rd.get("completion_tokens",0),
            total_tokens=rd.get("total_tokens",0), error=rd.get("error",""),
            metric_scores=rd.get("metric_scores",{}), metric_success=rd.get("metric_success",{}),
        ))
    return completed, results

# ── 主评估 ──
async def run_eval(tenant_id, kb_label, questions, model=None, agent_id=None,
                   concurrency=3, checkpoint_path=None, resume_from=None):
    skip_ids = resume_from or set()
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def process_one(q):
        async with semaphore:
            api = await call_agent_pipeline(
                q, tenant_id=tenant_id, model=model, agent_id=agent_id,
            )
            tc = build_llm_test_case(q, api)
            metrics = get_metrics_for_question(q.has_reference)
            sr = SingleResult(
                question_id=q.id, question=q.text, reference=q.reference,
                kb_label=kb_label, model=model or "(default)",
                answer=api.answer_text[:3000], rendered=api.rendered_output[:3000],
                task_type=api.task_type, risk_level=api.risk_level,
                sources_count=len(api.sources),
                latency_ms=api.latency_ms, ttft_ms=api.ttft_ms,
                prompt_tokens=api.prompt_tokens, completion_tokens=api.completion_tokens,
                total_tokens=api.total_tokens, error=api.error)
            if api.error: return sr
            try:
                ev: EvaluationResult = evaluate(test_cases=[tc], metrics=metrics)
                for tr in ev.test_results:
                    for md in tr.metrics_data:
                        sr.metric_scores[md.name] = md.score
                        sr.metric_reasons[md.name] = getattr(md,"reason","") or ""
                        sr.metric_success[md.name] = getattr(md,"success", False)
            except Exception as e:
                sr.error = f"DeepEval error: {e}"
            return sr

    pending = [q for q in questions if q.id not in skip_ids]
    skip_count = len(questions) - len(pending)
    if skip_count: print(f"  ↳ 跳过已完成的 {skip_count} 题")

    tasks = [process_one(q) for q in pending]
    completed_total = skip_count
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        r = await coro; results.append(r); completed_total += 1
        status = "OK" if not r.error else "FAIL"
        scores_str = ", ".join(f"{k}={v:.2f}" for k,v in r.metric_scores.items())
        ttft = f"ttft={r.ttft_ms}ms" if r.ttft_ms else ""
        tok = f"tok={r.prompt_tokens}/{r.completion_tokens}/{r.total_tokens}" if r.total_tokens else ""
        print(f"  [{completed_total:3d}/{len(questions)}] {r.question_id} [{r.kb_label}] "
              f"{status} task={r.task_type:25s} latency={r.latency_ms:5d}ms "
              f"{ttft} {tok} {scores_str}"
              + (f"  ERROR: {r.error}" if r.error else ""))
        if checkpoint_path and completed_total % 5 == 0:
            _write_checkpoint(checkpoint_path, kb_label, model or "(default)",
                              results, questions, skip_ids)
    return results

# ── KB 汇总 ──
def compare_kbs(results):
    groups = {}
    for r in results: groups.setdefault((r.kb_label, r.model), []).append(r)
    out = {}
    for (label, model), group in groups.items():
        c = KBComparison(label=label, model=model, total=len(group))
        c.errors = sum(1 for r in group if r.error); c.success = c.total - c.errors
        lats = [r.latency_ms for r in group if not r.error]
        if lats: c.avg_latency_ms = sum(lats)/len(lats)
        ttfts = [r.ttft_ms for r in group if not r.error and r.ttft_ms > 0]
        if ttfts: c.avg_ttft_ms = sum(ttfts)/len(ttfts)
        c.total_prompt_tokens = sum(r.prompt_tokens for r in group if not r.error)
        c.total_completion_tokens = sum(r.completion_tokens for r in group if not r.error)
        c.total_tokens = sum(r.total_tokens for r in group if not r.error)
        all_m = set()
        for r in group: all_m.update(r.metric_scores.keys())
        for name in sorted(all_m):
            scores = [r.metric_scores[name] for r in group if name in r.metric_scores and not r.error]
            succs = [r.metric_success[name] for r in group if name in r.metric_success and not r.error]
            if scores: c.avg_scores[name] = sum(scores)/len(scores)
            if succs: c.pass_rates[name] = sum(succs)/len(succs)
        out[f"{label}"] = c
    return out

# ── 报告 ──
def write_json_report(results, summaries, questions, meta, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    rep = {
        "meta": meta,
        "summaries": {name: {
            "label": s.label, "model": s.model, "total": s.total,
            "success": s.success, "errors": s.errors,
            "avg_latency_ms": round(s.avg_latency_ms,1),
            "avg_ttft_ms": round(s.avg_ttft_ms,1),
            "total_prompt_tokens": s.total_prompt_tokens,
            "total_completion_tokens": s.total_completion_tokens,
            "total_tokens": s.total_tokens,
            "avg_scores": {k: round(v,4) for k,v in s.avg_scores.items()},
            "pass_rates": {k: round(v,4) for k,v in s.pass_rates.items()},
        } for name,s in summaries.items()},
        "details": [{
            "question_id": r.question_id, "question": r.question,
            "reference": r.reference, "kb_label": r.kb_label, "model": r.model,
            "answer": r.answer, "rendered": r.rendered,
            "task_type": r.task_type, "risk_level": r.risk_level,
            "sources_count": r.sources_count, "latency_ms": r.latency_ms,
            "ttft_ms": r.ttft_ms,
            "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens, "error": r.error,
            "metric_scores": {k: round(v,4) for k,v in r.metric_scores.items()},
            "metric_success": r.metric_success, "metric_reasons": r.metric_reasons,
        } for r in results],
    }
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[JSON] {path}")

def write_markdown_report(results, summaries, questions, meta, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("# Sales Agent DeepEval 评估报告\n")
    L.append(f"**时间**: {meta.get('timestamp','')}\n**问题数**: {meta.get('total_questions',0)}\n")

    all_m = sorted(set().union(*(s.avg_scores.keys() for s in summaries.values())))
    L.append("## KB 汇总对比\n")
    h = "| KB | 总数 | 成功 | 平均延迟 | 平均TTFT | Token(入/出/总) |" + "|".join(f" {m} " for m in all_m) + "|"
    L.append(h); L.append("|------|------|------|----------|----------|------|" + "|".join("------" for _ in all_m) + "|")
    for name, s in summaries.items():
        rate = f"{s.success/s.total*100:.1f}%" if s.total else "N/A"
        tok = f"{s.total_prompt_tokens}/{s.total_completion_tokens}/{s.total_tokens}"
        cells = "|".join(f" {s.avg_scores.get(m,0):.3f} " for m in all_m)
        L.append(f"| {name} | {s.total} | {s.success} ({rate}) | {s.avg_latency_ms:.0f}ms | {s.avg_ttft_ms:.0f}ms | {tok} |{cells}|")
    L.append("")

    for k, v in sorted(meta.items()):
        if not k.startswith("arena_"): continue
        L.append("## 🏆 Arena 盲测对比\n")
        L.append(f"### {v.get('pair',k)}\n")
        if v.get('arena_errors'): L.append(f"> {v['arena_errors']} 题判定出错，计入平局\n")
        wins = v.get("wins", {}); total = v.get("total", 0)
        L.append("| | 胜出 | 胜率 |"); L.append("|------|------|------|")
        for kb_name in wins:
            if kb_name == "tie": continue
            rate = f"{wins[kb_name]/total*100:.1f}%" if total else "N/A"
            L.append(f"| {kb_name} | {wins[kb_name]} | {rate} |")
        if wins.get("tie",0) > 0: L.append(f"| 平局 | {wins['tie']} | — |")
        L.append("")

    by_qid = {}
    for r in results: by_qid.setdefault(r.question_id, []).append(r)
    L.append("## 逐题对比\n")
    for qid in sorted(by_qid.keys(), key=lambda x: tuple(int(p) if p.isdigit() else p for p in re.split(r'(\d+)', x))):
        items = by_qid[qid]; q_text = items[0].question
        L.append(f"### {qid}\n> {q_text}\n")
        for item in items:
            if item.error:
                L.append(f"**{item.kb_label}** ❌ {item.error}\n")
            else:
                scores_str = ", ".join(f"{k}={v:.2f}" for k,v in item.metric_scores.items())
                t = f"ttft={item.ttft_ms}ms " if item.ttft_ms else ""
                tok = f"tok={item.prompt_tokens}/{item.completion_tokens}/{item.total_tokens}" if item.total_tokens else ""
                L.append(f"**{item.kb_label}** — task={item.task_type}, sources={item.sources_count}, "
                         f"latency={item.latency_ms}ms {t}{tok}, scores=[{scores_str}]\n")
                if item.answer: L.append(f"{item.answer[:2000]}\n")
        L.append("")
    errs = [r for r in results if r.error]
    if errs:
        L.append("## 错误详情\n")
        for r in errs: L.append(f"- **{r.kb_label}** / {r.question_id}: {r.error}\n")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")
    print(f"[MD] {path}")

def write_csv_report(results, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    import csv
    all_m = sorted(set().union(*(set(r.metric_scores.keys()) for r in results)))
    hdr = ["question_id","question","kb_label","model","task_type","risk_level",
           "sources_count","latency_ms","ttft_ms","prompt_tokens","completion_tokens",
           "total_tokens","answer","rendered","error"] + all_m
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(hdr)
        for r in results:
            w.writerow([r.question_id, r.question, r.kb_label, r.model,
                        r.task_type, r.risk_level, r.sources_count,
                        r.latency_ms, r.ttft_ms, r.prompt_tokens, r.completion_tokens,
                        r.total_tokens, r.answer, r.rendered, r.error]
                       + [r.metric_scores.get(m,"") for m in all_m])
    print(f"[CSV] {path}")

def _parse_models(m):
    if m == "all": return [None]
    return [x.strip() for x in m.split(",") if x.strip()]

# ── CLI ──
def main():
    p = argparse.ArgumentParser(description="Sales Agent DeepEval 评估（直接走 ChatPipeline 链路）")
    # 租户配置
    p.add_argument("--tenant-id", default="taishan", help="主租户 ID（默认 taishan）")
    p.add_argument("--label", default=None, help="主租户标签（默认用 tenant-id）")
    p.add_argument("--tenant-id-2", default=None, help="对比租户 ID")
    p.add_argument("--label-2", default=None, help="对比租户标签")
    p.add_argument("--agent-id", default=None, help="Agent ID（不指定则用租户默认）")
    p.add_argument("--agent-id-2", default=None, help="对比方 Agent ID")

    p.add_argument("--models", default="all")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--no-questions-md", action="store_true")
    p.add_argument("--no-ground-truth", action="store_true")
    p.add_argument("--judge-model", default=None)
    p.add_argument("--judge-api-key", default=None)
    p.add_argument("--judge-base-url", default=None)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--checkpoint-dir", default=None)
    args = p.parse_args()

    # ── 初始化 DB（必须在任何 pipeline 调用前） ──
    print("[INFO] Initializing DB connection...")
    try:
        init_eval_db()
        print("[INFO] DB initialized")
    except Exception as e:
        print(f"[FATAL] DB init failed: {e}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else (
        Path(__file__).resolve().parent / "results" / "deepeval")
    cp_dir = Path(args.checkpoint_dir) if args.checkpoint_dir else (output_dir / "checkpoints")

    try:
        judge = get_judge_model(model_name=args.judge_model,
                                api_key=args.judge_api_key, base_url=args.judge_base_url)
        set_global_judge_model(judge)
        actual = judge.get_model_name() if hasattr(judge, 'get_model_name') else (args.judge_model or 'gpt-4o')
        print(f"[INFO] Judge model: {actual}")
    except RuntimeError as e:
        print(f"[FATAL] {e}", file=sys.stderr); sys.exit(1)

    questions = load_all_questions(
        include_questions_md=not args.no_questions_md,
        include_ground_truth=False)
    if args.limit > 0: questions = questions[:args.limit]
    print(f"[INFO] Loaded {len(questions)} questions")

    # ── 构建评估目标列表 ──
    targets = []
    label1 = args.label or args.tenant_id
    targets.append((args.tenant_id, label1, args.agent_id))

    if args.tenant_id_2:
        label2 = args.label_2 or args.tenant_id_2
        targets.append((args.tenant_id_2, label2, args.agent_id_2))

    models = _parse_models(args.models)
    all_results = []

    for tenant_id, label, agent_id in targets:
        for model in models:
            md = model or "(default)"
            cp_path = cp_dir / f"{label}_{md}.json"
            done_ids, resume_results = set(), []
            if args.resume:
                done_ids, resume_results = _load_checkpoint(cp_path)
                all_results.extend(resume_results)
                if done_ids: print(f"[RESUME] {cp_path.name}: 已完成 {len(done_ids)} 题")
            remaining = len(questions) - len(done_ids)
            if remaining <= 0:
                print(f"[SKIP] {label}/{md}: 全部已完成"); continue

            print(f"\n{'='*60}")
            print(f"[EVAL] {label} | {md} | tenant={tenant_id} | agent={agent_id or 'default'}")
            print(f"[EVAL] {remaining}/{len(questions)} questions")
            print(f"{'='*60}")

            t0 = time.monotonic()
            results = asyncio.run(run_eval(
                tenant_id=tenant_id, kb_label=label, questions=questions,
                model=model, agent_id=agent_id, concurrency=args.concurrency,
                checkpoint_path=cp_path, resume_from=done_ids))
            elapsed = time.monotonic() - t0
            all_results.extend(results)
            ok = sum(1 for r in results if not r.error)
            fail = sum(1 for r in results if r.error)
            print(f"\n[DONE] {label}/{md}: {ok} ok, {fail} fail in {elapsed:.1f}s")

    summaries = compare_kbs(all_results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_questions": len(questions),
        "targets": [{"tenant_id": tid, "label": lbl, "agent_id": aid} for tid, lbl, aid in targets],
        "models": [m or "(default)" for m in models],
    }

    # Arena
    kb_labels = sorted(set(r.kb_label for r in all_results if not r.error))
    if len(kb_labels) >= 2:
        print(f"\n{'='*60}\nARENA — Blind A/B Comparison\n{'='*60}")
        by_qid = {}
        for r in all_results:
            if not r.error: by_qid.setdefault(r.question_id, {})[r.kb_label] = r
        for i in range(len(kb_labels)):
            for j in range(i+1, len(kb_labels)):
                ka, kb = kb_labels[i], kb_labels[j]
                pair_key = f"{ka} vs {kb}"
                arena_list = []
                wins = {ka: 0, kb: 0, "tie": 0}
                err_count = 0
                for qid, kbs in by_qid.items():
                    if ka not in kbs or kb not in kbs: continue
                    ra, rb = kbs[ka], kbs[kb]
                    arena = run_arena_comparison(
                        kb_a_label=ka, kb_a_answer=ra.rendered[:2000] or ra.answer[:2000],
                        kb_b_label=kb, kb_b_answer=rb.rendered[:2000] or rb.answer[:2000],
                        input_text=ra.question[:500])
                    arena["question_id"] = qid; arena["question"] = ra.question
                    arena_list.append(arena)
                    if arena.get("error"):
                        err_count += 1; wins["tie"] += 1
                        if err_count <= 3: print(f"  [{qid}] ERROR: {arena['error'][:200]}")
                    elif arena.get("winner") == ka: wins[ka] += 1
                    elif arena.get("winner") == kb: wins[kb] += 1
                    else: wins["tie"] += 1
                    w = arena.get("winner") or ("?" if not arena.get("error") else "err")
                    if err_count <= 3 or not arena.get("error"):
                        print(f"  [{qid}] {ka} vs {kb} → winner: {w}")
                total = wins[ka] + wins[kb] + wins["tie"]
                print(f"  --- {pair_key}: {ka}={wins[ka]}, {kb}={wins[kb]}, tie={wins['tie']} (total={total})")
                meta[f"arena_{pair_key}"] = {
                    "pair": pair_key, "wins": wins, "total": total,
                    "arena_errors": err_count, "details": arena_list}

    # 保存报告
    json_path = output_dir / f"deepeval_{timestamp}.json"
    md_path = output_dir / f"deepeval_{timestamp}.md"
    csv_path = output_dir / f"deepeval_{timestamp}.csv"
    html_path = output_dir / f"deepeval_{timestamp}.html"

    write_json_report(all_results, summaries, questions, meta, json_path)
    _rj = json.loads(json_path.read_text(encoding="utf-8"))
    generate_html_report(results=_rj.get("details",[]), summaries=_rj.get("summaries",{}),
                         meta=_rj.get("meta",{}), output_path=html_path)
    write_markdown_report(all_results, summaries, questions, meta, md_path)
    write_csv_report(all_results, csv_path)

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    for name, s in summaries.items():
        rate = f"{s.success/s.total*100:.1f}%" if s.total else "N/A"
        scores_str = ", ".join(f"{k}={v:.2f}" for k,v in s.avg_scores.items())
        tok = f"tok={s.total_prompt_tokens}/{s.total_completion_tokens}/{s.total_tokens}"
        print(f"  {name:30s}  success={s.success}/{s.total} ({rate})  "
              f"latency={s.avg_latency_ms:.0f}ms  ttft={s.avg_ttft_ms:.0f}ms  {tok}")
        print(f"  {'':30s}  scores: [{scores_str}]")

if __name__ == "__main__":
    main()
