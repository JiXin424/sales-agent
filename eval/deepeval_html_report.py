"""
HTML 报告生成器 —— 将 DeepEval 评估结果渲染为独立的交互式 HTML 文件。

纯 HTML/CSS/JS，无外部依赖，浏览器直接打开即可查看。
支持：汇总卡片、KB 对比图表、逐题详情（搜索/排序/展开）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def generate_html_report(
    results: list[dict],
    summaries: dict[str, dict],
    meta: dict,
    output_path: Path,
) -> None:
    """生成自包含的交互式 HTML 报告。

    Args:
        results: 评估详情列表（每条包含 question_id, kb_label, metric_scores 等）
        summaries: KB 汇总数据
        meta: 元信息（timestamp, total_questions 等）
        output_path: 输出文件路径
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results_json = json.dumps(results, ensure_ascii=False)
    summaries_json = json.dumps(summaries, ensure_ascii=False)
    meta_json = json.dumps(meta, ensure_ascii=False)
    ts = meta.get("timestamp", datetime.now().isoformat())

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sales Agent DeepEval 评估报告</title>
<style>
/* ── Reset & Base ── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;color:#1a1a2e;line-height:1.6}}
.container{{max-width:1400px;margin:0 auto;padding:24px}}

/* ── Header ── */
.header{{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);color:#fff;padding:32px 40px;border-radius:12px;margin-bottom:24px}}
.header h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
.header .subtitle{{opacity:0.7;font-size:14px}}

/* ── Summary Cards ── */
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:#fff;border-radius:10px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.08)}}
.card .label{{font-size:12px;text-transform:uppercase;color:#888;margin-bottom:4px;letter-spacing:0.5px}}
.card .value{{font-size:28px;font-weight:700;color:#1a1a2e}}
.card .sub{{font-size:12px;color:#999;margin-top:4px}}
.card.good .value{{color:#10b981}}
.card.warn .value{{color:#f59e0b}}
.card.bad .value{{color:#ef4444}}

/* ── KB Comparison ── */
.section{{background:#fff;border-radius:10px;padding:24px;margin-bottom:24px;box-shadow:0 1px 3px rgba(0,0,0,0.08)}}
.section h2{{font-size:20px;margin-bottom:16px;color:#1a1a2e;border-bottom:2px solid #e5e7eb;padding-bottom:8px}}
.comparison-table{{width:100%;border-collapse:collapse;font-size:14px}}
.comparison-table th{{background:#f8fafc;padding:10px 14px;text-align:left;font-weight:600;border-bottom:2px solid #e5e7eb;white-space:nowrap}}
.comparison-table td{{padding:10px 14px;border-bottom:1px solid #f1f5f9}}
.comparison-table tr:hover{{background:#f8fafc}}
.diff-positive{{color:#10b981;font-weight:600}}
.diff-negative{{color:#ef4444;font-weight:600}}
.diff-neutral{{color:#6b7280}}

/* ── Bar Chart ── */
.bar-chart{{margin:16px 0}}
.bar-row{{display:flex;align-items:center;margin:8px 0;gap:12px}}
.bar-label{{width:140px;font-size:13px;text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar-track{{flex:1;background:#f1f5f9;border-radius:4px;height:24px;overflow:hidden;position:relative}}
.bar-fill{{height:100%;border-radius:4px;transition:width 0.5s ease;display:flex;align-items:center;padding-left:8px;font-size:11px;font-weight:600;color:#fff;min-width:40px}}
.bar-fill.kb0{{background:linear-gradient(90deg,#3b82f6,#2563eb)}}
.bar-fill.kb1{{background:linear-gradient(90deg,#8b5cf6,#7c3aed)}}

/* ── Search & Filter ── */
.toolbar{{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.toolbar input,.toolbar select{{padding:8px 14px;border:1px solid #e5e7eb;border-radius:6px;font-size:13px;outline:none}}
.toolbar input:focus,.toolbar select:focus{{border-color:#3b82f6;box-shadow:0 0 0 2px rgba(59,130,246,0.15)}}
.toolbar input{{flex:1;min-width:200px}}
.toolbar select{{min-width:140px}}
.toolbar .count{{font-size:13px;color:#888;white-space:nowrap}}

/* ── Question Table ── */
.q-table{{width:100%;border-collapse:collapse;font-size:13px}}
.q-table th{{background:#f8fafc;padding:8px 10px;text-align:left;font-weight:600;border-bottom:2px solid #e5e7eb;cursor:pointer;user-select:none;white-space:nowrap;position:sticky;top:0}}
.q-table th:hover{{background:#eef2ff}}
.q-table th .sort-icon{{margin-left:4px;font-size:10px;opacity:0.4}}
.q-table th.sorted .sort-icon{{opacity:1}}
.q-table td{{padding:8px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top}}
.q-table tr:hover{{background:#f8fafc}}
.q-table .q-text{{max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}}
.q-table .q-text.expanded{{white-space:normal;max-width:none}}
.q-table .a-text{{max-width:400px;max-height:80px;overflow:hidden;cursor:pointer;color:#555;font-size:12px}}
.q-table .a-text.expanded{{max-height:none}}
.score-badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.score-badge.pass{{background:#d1fae5;color:#065f46}}
.score-badge.fail{{background:#fee2e2;color:#991b1b}}
.score-badge.na{{background:#f3f4f6;color:#9ca3af}}

/* ── Expandable Detail ── */
.detail-row{{display:none}}
.detail-row.open{{display:table-row}}
.detail-content{{padding:12px 16px;background:#fafbfc;border-bottom:2px solid #e5e7eb}}
.detail-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.detail-item{{font-size:13px}}
.detail-item .k{{color:#888;font-size:11px;text-transform:uppercase}}
.detail-item .v{{color:#1a1a2e;margin-top:2px;word-break:break-word}}
.reason-box{{background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:10px 14px;font-size:12px;color:#555;white-space:pre-wrap;max-height:120px;overflow-y:auto;margin-top:8px}}

/* ── Responsive ── */
@media (max-width:768px){{
    .header{{padding:20px}}
    .cards{{grid-template-columns:1fr 1fr}}
    .toolbar{{flex-direction:column}}
    .toolbar input{{width:100%}}
}}
</style>
</head>
<body>

<div class="container">
    <div class="header">
        <h1>📊 Sales Agent DeepEval 评估报告</h1>
        <div class="subtitle">生成时间: {ts}</div>
    </div>

    <div class="cards" id="summary-cards"></div>

    <div class="section">
        <h2>📈 KB 指标对比</h2>
        <table class="comparison-table" id="kb-compare"><thead></thead><tbody></tbody></table>
        <div class="bar-chart" id="bar-chart"></div>
    </div>

    <div class="section">
        <h2>📋 逐题评估详情</h2>
        <div class="toolbar">
            <input type="text" id="search" placeholder="🔍 搜索问题..." oninput="renderTable()">
            <select id="kb-filter" onchange="renderTable()">
                <option value="all">全部 KB</option>
            </select>
            <select id="score-filter" onchange="renderTable()">
                <option value="all">全部通过状态</option>
                <option value="pass">✅ 全部通过</option>
                <option value="partial">⚠️ 部分通过</option>
                <option value="fail">❌ 全部未通过</option>
                <option value="error">💥 错误</option>
            </select>
            <span class="count" id="result-count"></span>
        </div>
        <div style="overflow-x:auto">
            <table class="q-table" id="q-table"><thead></thead><tbody></tbody></table>
        </div>
    </div>
</div>

<script>
// ── Data ──
const RESULTS = {results_json};
const SUMMARIES = {summaries_json};
const META = {meta_json};

const KB_LIST = Object.keys(SUMMARIES);
const METRIC_NAMES = (() => {{
    const names = new Set();
    RESULTS.forEach(r => {{
        if (r.metric_scores) Object.keys(r.metric_scores).forEach(k => names.add(k));
    }});
    return [...names];
}})();

// ── Summary Cards ──
function renderSummaryCards() {{
    const container = document.getElementById('summary-cards');
    const totalQuestions = META.total_questions || RESULTS.length;
    let html = `
        <div class="card">
            <div class="label">总问题数</div>
            <div class="value">${{totalQuestions}}</div>
            <div class="sub">${{META.targets ? META.targets.length : 1}} 个 KB 实例</div>
        </div>`;

    KB_LIST.forEach(name => {{
        const s = SUMMARIES[name];
        if (!s) return;
        const passRate = s.total > 0 ? (s.success / s.total * 100).toFixed(1) : 'N/A';
        html += `
        <div class="card">
            <div class="label">${{name}} — 成功率</div>
            <div class="value">${{passRate}}%</div>
            <div class="sub">${{s.success}}/${{s.total}} 成功, ${{s.errors}} 错误</div>
        </div>`;
    }});

    // 平均分数卡片
    METRIC_NAMES.forEach(m => {{
        const scores = KB_LIST.map(n => SUMMARIES[n]?.avg_scores?.[m] || 0);
        const avg = scores.length > 0 ? scores.reduce((a,b)=>a+b,0)/scores.length : 0;
        const cls = avg >= 0.7 ? 'good' : avg >= 0.5 ? 'warn' : 'bad';
        html += `
        <div class="card ${{cls}}">
            <div class="label">平均 ${{m}}</div>
            <div class="value">${{avg.toFixed(2)}}</div>
            <div class="sub">${{KB_LIST.map((n,i) => `${{n}}: ${{scores[i].toFixed(2)}}`).join(' / ')}}</div>
        </div>`;
    }});

    container.innerHTML = html;
}}

// ── KB Comparison Table ──
function renderKBCompare() {{
    const thead = document.querySelector('#kb-compare thead');
    const tbody = document.querySelector('#kb-compare tbody');

    thead.innerHTML = `<tr>
        <th>指标</th>
        ${{KB_LIST.map(n => `<th>${{n}} 均分</th><th>${{n}} 通过率</th>`).join('')}}
        ${{KB_LIST.length >= 2 ? '<th>差异</th>' : ''}}
    </tr>`;

    tbody.innerHTML = METRIC_NAMES.map(m => {{
        const scores = KB_LIST.map(n => SUMMARIES[n]?.avg_scores?.[m]);
        const passes = KB_LIST.map(n => SUMMARIES[n]?.pass_rates?.[m]);
        let diffHtml = '';
        if (KB_LIST.length >= 2 && scores[0] != null && scores[1] != null) {{
            const diff = scores[0] - scores[1];
            const cls = diff > 0.05 ? 'diff-positive' : diff < -0.05 ? 'diff-negative' : 'diff-neutral';
            const arrow = diff > 0.05 ? '← KB1 更好' : diff < -0.05 ? '← KB2 更好' : '持平';
            diffHtml = `<td class="${{cls}}">${{(diff>0?'+':'')}}${{diff.toFixed(3)}} ${{arrow}}</td>`;
        }}
        return `<tr>
            <td><strong>${{m}}</strong></td>
            ${{KB_LIST.map((n,i) => `
                <td>${{scores[i] != null ? scores[i].toFixed(3) : '—'}}</td>
                <td>${{passes[i] != null ? (passes[i]*100).toFixed(0)+'%' : '—'}}</td>
            `).join('')}}
            ${{diffHtml}}
        </tr>`;
    }}).join('');
}}

// ── Bar Chart ──
function renderBarChart() {{
    const container = document.getElementById('bar-chart');
    let html = '<h3 style="font-size:15px;margin:20px 0 12px;color:#555">平均分数可视化</h3>';

    METRIC_NAMES.forEach(m => {{
        html += `<div class="bar-row"><div class="bar-label">${{m}}</div>`;
        KB_LIST.forEach((n, i) => {{
            const score = SUMMARIES[n]?.avg_scores?.[m] || 0;
            html += `<div class="bar-track" style="margin-right:4px">
                <div class="bar-fill kb${{i}}" style="width:${{Math.max(score*100,3)}}%">${{(score*100).toFixed(0)}}%</div>
            </div>`;
        }});
        html += '</div>';
    }});
    container.innerHTML = html;
}}

// ── Question Table ──
let currentSort = {{col: 'question_id', asc: true}};
let expandedRows = new Set();
let expandedAnswers = new Set();

function sortResults(col, asc) {{
    return [...RESULTS].sort((a,b) => {{
        let va = a[col] ?? '', vb = b[col] ?? '';
        if (col === 'latency_ms' || col === 'sources_count' || col === 'total_tokens') {{
            va = Number(va); vb = Number(vb);
        }}
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return asc ? -1 : 1;
        if (va > vb) return asc ? 1 : -1;
        return 0;
    }});
}}

function getPassStatus(r) {{
    if (r.error) return 'error';
    if (!r.metric_success || Object.keys(r.metric_success).length === 0) return 'na';
    const vals = Object.values(r.metric_success);
    if (vals.every(v => v)) return 'pass';
    if (vals.some(v => v)) return 'partial';
    return 'fail';
}}

function renderTable() {{
    const search = (document.getElementById('search').value || '').toLowerCase();
    const kbFilter = document.getElementById('kb-filter').value;
    const scoreFilter = document.getElementById('score-filter').value;

    let filtered = RESULTS.filter(r => {{
        if (kbFilter !== 'all' && r.kb_label !== kbFilter) return false;
        const status = getPassStatus(r);
        if (scoreFilter !== 'all' && status !== scoreFilter) return false;
        if (search && !(r.question||'').toLowerCase().includes(search) && !(r.question_id||'').toLowerCase().includes(search)) return false;
        return true;
    }});

    filtered = sortResults(currentSort.col, currentSort.asc).filter(r => filtered.includes(r));

    document.getElementById('result-count').textContent = `显示 ${{filtered.length}} / ${{RESULTS.length}} 条`;

    // KB filter options
    const kbSelect = document.getElementById('kb-filter');
    if (kbSelect.options.length <= 1) {{
        kbSelect.innerHTML = '<option value="all">全部 KB</option>' + KB_LIST.map(n => `<option value="${{n}}">${{n}}</option>`).join('');
    }}

    // Headers
    const cols = ['question_id','kb_label','question','task_type','latency_ms','ttft_ms','total_tokens','risk_level'];
    const thead = document.querySelector('#q-table thead');
    thead.innerHTML = `<tr>
        ${{cols.map(c => {{
            const labels = {{question_id:'ID',kb_label:'KB',question:'问题',task_type:'任务类型',latency_ms:'延迟',ttft_ms:'TTFT',total_tokens:'Token',risk_level:'风险'}};
            const sorted = currentSort.col === c ? ' sorted' : '';
            const arrow = sorted ? (currentSort.asc ? '▲' : '▼') : '↕';
            return `<th class="${{sorted}}" onclick="currentSort={{col:'${{c}}',asc:currentSort.col==='${{c}}'?!currentSort.asc:true}};renderTable()">${{labels[c]||c}} <span class="sort-icon">${{arrow}}</span></th>`;
        }}).join('')}}
        ${{METRIC_NAMES.map(m => `<th onclick="currentSort={{col:'${{m}}',asc:currentSort.col==='${{m}}'?!currentSort.asc:true}};renderTable()">${{m}} <span class="sort-icon">↕</span></th>`).join('')}}
        <th>状态</th>
    </tr>`;

    // Rows
    const tbody = document.querySelector('#q-table tbody');
    tbody.innerHTML = filtered.map((r, i) => {{
        const status = getPassStatus(r);
        const statusIcon = {{pass:'✅',partial:'⚠️',fail:'❌',error:'💥',na:'—'}}[status] || '—';
        const qid = r.question_id || '';
        const qText = (r.question || '').substring(0, 80);
        const isExpanded = expandedRows.has(qid + r.kb_label);

        const ttft = r.ttft_ms ? r.ttft_ms + 'ms' : '—';
        const tok = r.total_tokens ? (r.prompt_tokens||0)+'/'+(r.completion_tokens||0)+'/'+(r.total_tokens||0) : '—';

        let row = `<tr>
            <td>${{qid}}</td>
            <td>${{r.kb_label||''}}</td>
            <td class="q-text ${{isExpanded?'expanded':''}}" onclick="toggleExpand('${{qid}}','${{r.kb_label}}')" title="${{(r.question||'').replace(/"/g,'&quot;')}}">${{isExpanded ? (r.question||'') : qText}}</td>
            <td>${{r.task_type||''}}</td>
            <td>${{r.latency_ms||0}}ms</td>
            <td>${{ttft}}</td>
            <td>${{tok}}</td>
            <td>${{r.risk_level||'none'}}</td>
            ${{METRIC_NAMES.map(m => {{
                const score = r.metric_scores?.[m];
                const success = r.metric_success?.[m];
                const cls = score == null ? 'na' : success ? 'pass' : 'fail';
                return `<td><span class="score-badge ${{cls}}">${{score != null ? score.toFixed(2) : '—'}}</span></td>`;
            }}).join('')}}
            <td><span class="score-badge ${{status==='pass'?'pass':status==='error'?'fail':status==='partial'?'fail':'na'}}">${{statusIcon}}</span></td>
        </tr>`;

        // Expandable detail row
        if (isExpanded) {{
            row += `<tr class="detail-row open"><td colspan="${{8 + METRIC_NAMES.length}}" class="detail-content">
                <div class="detail-grid">
                    <div class="detail-item"><div class="k">完整回答</div><div class="v">${{(r.answer||'(无)').replace(/</g,'&lt;').replace(/>/g,'&gt;').substring(0, 2000)}}</div></div>
                    ${{r.reference ? `<div class="detail-item"><div class="k">参考答案</div><div class="v">${{r.reference.replace(/</g,'&lt;').replace(/>/g,'&gt;')}}</div></div>` : ''}}
                    <div class="detail-item"><div class="k">性能指标</div><div class="v">延迟: ${{r.latency_ms||0}}ms | TTFT: ${{r.ttft_ms||0}}ms | Token: 入${{r.prompt_tokens||0}}/出${{r.completion_tokens||0}}/总${{r.total_tokens||0}}</div></div>
                    <div class="detail-item"><div class="k">指标详情</div><div class="v">
                        ${{METRIC_NAMES.map(m => {{
                            const reason = r.metric_reasons?.[m] || '';
                            return `<div style="margin:4px 0"><strong>${{m}}:</strong> ${{(r.metric_scores?.[m]||0).toFixed(2)}}${{reason ? `<div class="reason-box">${{reason.replace(/</g,'&lt;').replace(/>/g,'&gt;')}}</div>` : ''}}</div>`;
                        }}).join('')}}
                    </div></div>
                    ${{r.error ? `<div class="detail-item"><div class="k">错误</div><div class="v" style="color:#ef4444">${{r.error}}</div></div>` : ''}}
                </div>
            </td></tr>`;
        }}

        return row;
    }}).join('');
}}

window.toggleExpand = function(qid, kb) {{
    const key = qid + kb;
    if (expandedRows.has(key)) expandedRows.delete(key);
    else expandedRows.add(key);
    renderTable();
}};

// ── Init ──
renderSummaryCards();
renderKBCompare();
renderBarChart();
renderTable();
</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[HTML] Report saved: {output_path}")
