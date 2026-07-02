"""Deterministic report renderers: JSON, Markdown, HTML, CSV.

All renderers consume a report document (dict) produced by the
IterationReportService and never query the database.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}"


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# ── JSON ─────────────────────────────────────────────────────────────────────

def render_json(report: dict[str, Any]) -> str:
    """Return the report as pretty-printed JSON (authoritative)."""
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)


# ── Markdown ─────────────────────────────────────────────────────────────────

def render_markdown(report: dict[str, Any]) -> str:
    """Render the report as a human-readable Markdown document."""
    rid = report.get("report_id", "unknown")
    rtype = report.get("report_type", "unknown")
    recommendation = report.get("recommendation", "—")
    before = report.get("effect_index_before")
    after = report.get("effect_index_after")
    delta = report.get("effect_index_delta")
    hard_gates = report.get("hard_gates", {})
    groups = report.get("groups", [])
    cases = report.get("cases", [])

    lines = [
        f"# Iteration Effect Report",
        f"",
        f"**Report ID:** `{rid}`",
        f"**Type:** {rtype}",
        f"**Recommendation:** `{recommendation}`",
        f"",
        f"## Effect Index",
        f"",
        f"| | Score |",
        f"|---|-------|",
        f"| Before | {_format_pct(before)} |",
        f"| After  | {_format_pct(after)} |",
        f"| Delta  | {_format_pct(delta)} |",
        f"",
    ]

    # Hard gates
    failed = hard_gates.get("failed", [])
    if failed:
        lines.append("## Hard Gates ❌")
        lines.append("")
        for gate in failed:
            lines.append(f"- **{gate}**")
    else:
        lines.append("## Hard Gates ✅")
    lines.append("")

    # Metric groups
    lines.append("## Metric Groups")
    lines.append("")
    for group in groups:
        gname = group.get("group_name", "unknown")
        gbefore = group.get("score_before")
        gafter = group.get("score_after")
        gdelta = group.get("delta")
        lines.append(f"### {gname} (weight: {group.get('weight', 0)})")
        lines.append("")
        lines.append(f"- Coverage: {group.get('coverage', 0)}/{group.get('total_metrics', 0)}")
        lines.append(f"- Score: {_format_pct(gbefore)} → {_format_pct(gafter)} ({_format_pct(gdelta)})")
        lines.append("")
        metrics = group.get("metrics", [])
        if metrics:
            lines.append("| Metric | Before | After | Delta | Gate |")
            lines.append("|--------|--------|-------|-------|------|")
            for m in metrics:
                gate_str = m.get("gate_result", "—") or "—"
                lines.append(
                    f"| {m['metric_name']} "
                    f"| {_format_value(m.get('before_value'))} "
                    f"| {_format_value(m.get('after_value'))} "
                    f"| {_format_pct(m.get('delta'))} "
                    f"| {gate_str} |"
                )
        lines.append("")

    # Cases
    if cases:
        lines.append("## Per-Case Classification")
        lines.append("")
        lines.append("| Case | Classification | Cause | Before | After |")
        lines.append("|------|---------------|-------|--------|-------|")
        for c in cases:
            lines.append(
                f"| {c.get('case_id', '—')} "
                f"| {c.get('classification', '—')} "
                f"| {c.get('cause', '—') or '—'} "
                f"| {'✓' if c.get('before_pass') else '✗' if c.get('before_pass') is False else '—'} "
                f"| {'✓' if c.get('after_pass') else '✗' if c.get('after_pass') is False else '—'} |"
            )
        lines.append("")

    return "\n".join(lines)


# ── HTML ─────────────────────────────────────────────────────────────────────

def render_html(report: dict[str, Any]) -> str:
    """Render the report as a standalone HTML page.

    All user content is escaped. The JSON data is embedded as ``<script>``
    so rich clients can hydrate it directly.
    """
    import html as _html

    md = render_markdown(report)

    # Simple Markdown→HTML conversion (headers, bold, tables, lists)
    html_lines: list[str] = []
    in_table = False
    in_list = False

    for line in md.split("\n"):
        stripped = line.strip()

        # Headers
        if stripped.startswith("### "):
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{_html.escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{_html.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            html_lines.append(f"<h1>{_html.escape(stripped[2:])}</h1>")
        # Table rows
        elif stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            is_sep = all(c.replace("-", "").replace(" ", "") == "" for c in cells)
            if is_sep:
                continue
            if not in_table:
                html_lines.append("<table><thead>")
                html_lines.append(
                    "<tr>" + "".join(f"<th>{_html.escape(c)}</th>" for c in cells) + "</tr>"
                )
                html_lines.append("</thead><tbody>")
                in_table = True
            else:
                html_lines.append(
                    "<tr>" + "".join(f"<td>{_html.escape(c)}</td>" for c in cells) + "</tr>"
                )
        else:
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            # Bold
            import re
            line_html = _html.escape(stripped)
            line_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line_html)
            line_html = re.sub(r"`(.+?)`", r"<code>\1</code>", line_html)
            # List items
            if stripped.startswith("- "):
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                html_lines.append(f"<li>{line_html[2:]}</li>")
            else:
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                if stripped:
                    html_lines.append(f"<p>{line_html}</p>")

    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)
    json_data = _html.escape(json.dumps(report, ensure_ascii=False))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Iteration Effect Report – {_html.escape(str(report.get('report_id', '')))}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e0e0e0; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  h1 {{ font-size: 1.75rem; }}
  h2 {{ font-size: 1.25rem; margin-top: 2rem; }}
  h3 {{ font-size: 1.05rem; color: #555; }}
  code {{ background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; font-size: 0.9em; }}
  strong {{ color: #c00; }}
</style>
</head>
<body>
{body}
<script type="application/json" id="report-data">
{json_data}
</script>
</body>
</html>"""


# ── CSV ──────────────────────────────────────────────────────────────────────

def render_csv(report: dict[str, Any]) -> str:
    """Render the report as CSV with one record type column."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Summary row
    writer.writerow(["type", "report_id", "report_type", "recommendation",
                      "effect_before", "effect_after", "effect_delta"])
    writer.writerow(["summary", report.get("report_id"), report.get("report_type"),
                      report.get("recommendation"),
                      report.get("effect_index_before"),
                      report.get("effect_index_after"),
                      report.get("effect_index_delta")])

    # Metric rows
    writer.writerow([])
    writer.writerow(["type", "group", "metric", "direction", "before", "after", "delta", "gate_result"])
    for group in report.get("groups", []):
        for m in group.get("metrics", []):
            writer.writerow(["metric", group.get("group_name"), m.get("metric_name"),
                              m.get("direction"), m.get("before_value"),
                              m.get("after_value"), m.get("delta"),
                              m.get("gate_result")])

    # Case rows
    writer.writerow([])
    writer.writerow(["type", "case_id", "classification", "cause", "before_pass", "after_pass",
                      "score_delta", "rank_delta", "latency_delta_ms", "token_delta"])
    for c in report.get("cases", []):
        writer.writerow(["case", c.get("case_id"), c.get("classification"),
                          c.get("cause"), c.get("before_pass"),
                          c.get("after_pass"), c.get("score_delta"),
                          c.get("rank_delta"), c.get("latency_delta_ms"),
                          c.get("token_delta")])

    return output.getvalue()


# ── Renderer registry ────────────────────────────────────────────────────────

RENDERERS: dict[str, Any] = {
    "json": render_json,
    "markdown": render_markdown,
    "html": render_html,
    "csv": render_csv,
}
