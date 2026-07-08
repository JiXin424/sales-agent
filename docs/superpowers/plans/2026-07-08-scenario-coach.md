# 场景教练（Scenario Coach）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-instance-opt-in "scenario coach" that intercepts a user's question, matches it via LLM against 6 preset sales scenarios (11 representative questions), and on a high-confidence match returns a preset answer (with a 《销冠智慧教练手册·2026年4月版》 source citation) — short-circuiting RAG/generation; on no match, the normal Online Graph runs unchanged.

**Architecture:** A self-contained `src/sales_agent/scenarios/` package owns the scenario data (a shipped markdown file), a markdown→registry loader, a pydantic decision model, an LLM matcher (mirroring `evidence_router`), and a matcher prompt. The Online Graph gets one new node (`scenario_coach`) + one new log node (`log_scenario_response`) inserted after `normalize_turn`, gated by a state flag `scenario_coach_enabled` set from a new `ScenarioCoachConfig` (env-flag `SCENARIO_COACH_ENABLED`, default off). Feature off → graph topology unchanged, zero overhead.

**Tech Stack:** Python 3 / asyncio, LangGraph (Online Graph), pydantic v2, SQLAlchemy async, pytest (asyncio_mode=auto). LLM via the existing `OpenAICompatibleChat.generate`. JSON parsing via existing `parse_model_json`. No new dependencies, no DB, no Alembic.

## Global Constraints

- **Backend is CommonJS-style Python** (this is a Python backend; the CommonJS/ESM rule in CLAUDE.md applies to the JS frontend, not touched here).
- **No DB changes**: scenario data ships as a markdown file inside `src/`; no Alembic migration, no seed.
- **Feature default OFF**: `ScenarioCoachConfig.enabled = False`. Disabled = identical graph behavior to today (lesson #35).
- **Fail-open everywhere** (lesson #34): any LLM/parse/load failure → treat as "no match" → normal pipeline. The feature is an enhancement, never a blocker. Stream must never crash because of it.
- **Match granularity = question-level (Q01–Q11)**, not scenario-level. 6 scenarios / 11 questions in the data file.
- **Confidence threshold default 0.8** (`ScenarioCoachConfig.confidence_threshold`), env-tunable via `SCENARIO_COACH_CONFIDENCE_THRESHOLD`.
- **answer_dict contract** (DO NOT deviate): `{"summary": str, "sections": [{"title": str, "content": str}], "sources": [{"title": str, "display_title": str, "source_type": str}]}`. Section keys are `title`/`content` (confirmed `src/sales_agent/services/response_formatter.py:22-28`), NOT heading/body.
- **source_type for scenario citations = `"scenario_coach"`**, label `"教练手册"` (added to `citation.py`).
- **Two input_state builders must set the flag** (lesson #35): `services/online_conversation.py:222` (HTTP path) AND `integrations/dingtalk/graph_stream.py:103` (DingTalk stream — the production main entry). Missing the stream one = feature silently off in prod.
- **Verification goes through the production entry** (lesson #4): `docker logs sales-agent-<tenant>-stream` after deploy; not just HTTP 200.
- **Worktree isolation** (lesson #38): implementation happens in a worktree branched from local `main` HEAD (which contains the spec + this plan), NOT from `origin/main` (which is behind). Set `worktree.baseRef=head` before creating the worktree.
- **Pre-existing bug to leave alone**: `graph/online/nodes.py` lines 26-48 and 50-70 are an identical duplicated import block. Add new imports to the FIRST block only (line 48 area). Do NOT fix the duplicate (out of scope; lesson: minimal impact).

---

## File Structure

**Create:**
- `src/sales_agent/scenarios/__init__.py` — package exports.
- `src/sales_agent/scenarios/data/销冠智慧教练手册.md` — the preset scenario data (copy of `/root/code/sales-agent/销冠智慧教练手册_第6-25页.md`).
- `src/sales_agent/scenarios/models.py` — `Scenario`, `ScenarioQuestion`, `AnswerSection`, `ScenarioMatchDecision` (pydantic).
- `src/sales_agent/scenarios/loader.py` — `ScenarioRegistry`, `parse_scenario_md`, `get_scenario_registry` (singleton).
- `src/sales_agent/scenarios/prompt.py` — `SCENARIO_MATCHER_PROMPT` constant.
- `src/sales_agent/scenarios/matcher.py` — `match_scenario` async function.
- `tests/unit/test_scenario_loader.py`
- `tests/unit/test_scenario_matcher.py`
- `tests/unit/test_scenario_config.py`
- `tests/unit/test_scenario_citation.py`
- `tests/unit/graph/test_scenario_coach_node.py`
- `tests/unit/graph/test_scenario_coach_graph.py`
- `changelog/2026-07-08.md`

**Modify:**
- `src/sales_agent/core/config.py` — add `ScenarioCoachConfig` + Settings field + 2 env-override blocks.
- `config/default.yaml` — add `scenario_coach:` section.
- `.env.example` — add 2 env entries.
- `src/sales_agent/graph/online/state.py` — add `scenario_coach_enabled: bool`.
- `src/sales_agent/services/online_conversation.py` — set flag in input_state.
- `src/sales_agent/integrations/dingtalk/graph_stream.py` — set flag in input_state.
- `src/sales_agent/integrations/dingtalk/citation.py` — add `"scenario_coach": "教练手册"` label.
- `src/sales_agent/graph/online/edges.py` — modify `route_online_message`; add `route_after_scenario`.
- `src/sales_agent/graph/online/nodes.py` — add `scenario_coach_node` + `log_scenario_response_node` + imports.
- `src/sales_agent/graph/online/graph.py` — register nodes + edges.
- `README.md` — update product-doc index (per CLAUDE.md rule).

---

## Task 1: Config (ScenarioCoachConfig + env overrides + yaml + .env.example)

**Files:**
- Modify: `src/sales_agent/core/config.py` (after line 196, after line 216, after line 373)
- Modify: `config/default.yaml` (after line 153)
- Modify: `.env.example` (after line 37)
- Test: `tests/unit/test_scenario_config.py`

**Interfaces:**
- Produces: `ScenarioCoachConfig` (fields `enabled: bool = False`, `confidence_threshold: float = 0.8`), accessible as `get_settings().scenario_coach`. Env overrides `SCENARIO_COACH_ENABLED` (bool) and `SCENARIO_COACH_CONFIDENCE_THRESHOLD` (float). This is the FIRST float-from-env override in the repo (no precedent — establish it modeled on the `int()` idiom at `config.py:357-359`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_scenario_config.py`:

```python
"""Tests for ScenarioCoachConfig env overrides."""

from __future__ import annotations

import importlib

import pytest


def _reload_settings(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from sales_agent.core import config as config_mod
    importlib.reload(config_mod)
    return config_mod.get_settings()


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Ensure a fresh settings singleton per test and clean env."""
    for k in ("SCENARIO_COACH_ENABLED", "SCENARIO_COACH_CONFIDENCE_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)
    from sales_agent.core import config as config_mod
    importlib.reload(config_mod)
    yield
    importlib.reload(config_mod)


def test_defaults_off():
    from sales_agent.core.config import get_settings
    s = get_settings()
    assert s.scenario_coach.enabled is False
    assert s.scenario_coach.confidence_threshold == 0.8


def test_env_enables(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_ENABLED": "true"})
    assert s.scenario_coach.enabled is True


def test_env_disabled_variants(monkeypatch):
    for val in ("0", "false", "no", "off", "False", "anything"):
        s = _reload_settings(monkeypatch, {"SCENARIO_COACH_ENABLED": val})
        assert s.scenario_coach.enabled is False


def test_env_threshold_float(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_CONFIDENCE_THRESHOLD": "0.65"})
    assert s.scenario_coach.confidence_threshold == 0.65


def test_env_threshold_invalid_ignored(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_CONFIDENCE_THRESHOLD": "not-a-number"})
    assert s.scenario_coach.confidence_threshold == 0.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scenario_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'scenario_coach'` (or import error).

- [ ] **Step 3: Add ScenarioCoachConfig class**

In `src/sales_agent/core/config.py`, AFTER the `GuidedFlowsConfig` class (which ends at line 196) and BEFORE `class Settings` (line 199), insert:

```python
class ScenarioCoachConfig(BaseModel):
    """场景教练：识别预设销售场景问题，命中即返回预设答案。默认关闭。"""

    enabled: bool = False
    confidence_threshold: float = 0.8
```

- [ ] **Step 4: Add Settings field**

In `src/sales_agent/core/config.py`, AFTER line 216 (`    guided_flows: GuidedFlowsConfig = GuidedFlowsConfig()`), insert:

```python
    scenario_coach: ScenarioCoachConfig = ScenarioCoachConfig()
```

- [ ] **Step 5: Add env-override blocks**

In `src/sales_agent/core/config.py`, AFTER the `TOPIC_ROUTING_ENABLED` block (which ends at line 373) and BEFORE the `PATH_ROUTER_ENABLE_LLM_ROUTER` block (line 375), insert:

```python
        # 环境变量覆盖 scenario_coach 配置
        scenario_coach_enabled = os.getenv("SCENARIO_COACH_ENABLED")
        if scenario_coach_enabled is not None:
            raw.setdefault("scenario_coach", {})["enabled"] = (
                scenario_coach_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )
        scenario_coach_threshold = os.getenv("SCENARIO_COACH_CONFIDENCE_THRESHOLD")
        if scenario_coach_threshold is not None:
            try:
                raw.setdefault("scenario_coach", {})["confidence_threshold"] = float(
                    scenario_coach_threshold
                )
            except ValueError:
                pass

```

- [ ] **Step 6: Add default.yaml section**

In `config/default.yaml`, AFTER the `topic_routing:` block (ends at line 153) and BEFORE `neo4j:` (line 155), insert (2-space indent, top-level key at column 0):

```yaml
# --- 场景教练（预设销售场景问答，默认关闭）---
# 设为 true 开启：用户问题与预设场景高度重合时返回预设答案（含手册来源）。
scenario_coach:
  enabled: false
  confidence_threshold: 0.8

```

- [ ] **Step 7: Add .env.example entries**

In `.env.example`, AFTER the `TOPIC_ROUTING_ENABLED=false` block (ends at line 37), insert:

```env
# --- 场景教练（预设销售场景问答）---
# Set SCENARIO_COACH_ENABLED=true to match user questions against preset sales
# scenarios and return a preset answer with a manual citation on high-confidence hits.
SCENARIO_COACH_ENABLED=false
# 命中置信度阈值（0-1），低于此值视为未命中、走正常 AI 回答。
SCENARIO_COACH_CONFIDENCE_THRESHOLD=0.8
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scenario_config.py -v`
Expected: 5 PASS.

- [ ] **Step 9: Commit**

```bash
git add src/sales_agent/core/config.py config/default.yaml .env.example tests/unit/test_scenario_config.py
git commit -m "feat(scenario-coach): add ScenarioCoachConfig with env-flag overrides"
```

---

## Task 2: Scenario data file + models + loader

**Files:**
- Create: `src/sales_agent/scenarios/__init__.py`
- Create: `src/sales_agent/scenarios/data/销冠智慧教练手册.md`
- Create: `src/sales_agent/scenarios/models.py`
- Create: `src/sales_agent/scenarios/loader.py`
- Test: `tests/unit/test_scenario_loader.py`

**Interfaces:**
- Consumes: the shipped markdown file (copied from `/root/code/sales-agent/销冠智慧教练手册_第6-25页.md`).
- Produces:
  - `models.py`: `AnswerSection(title: str, content: str)`, `ScenarioQuestion(id: str, text: str, tag: str, answer_summary: str, answer_sections: list[AnswerSection])`, `Scenario(id: str, name: str, subtitle: str, questions: list[ScenarioQuestion])`, `ScenarioMatchDecision(BaseModel)` with `matched_question_id: str | None = None`, `confidence: float = Field(default=0.0, ge=0, le=1)`, `reason_code: str = "unknown"`.
  - `loader.py`: `ScenarioRegistry` (dataclass with `scenarios`, `source_name`, `is_available()`, `list_questions() -> list[dict]`, `has_question(qid) -> bool`, `get_question(qid) -> ScenarioQuestion | None`); `parse_scenario_md(md_text: str) -> ScenarioRegistry` (raises `ValueError` on malformed/empty); `get_scenario_registry() -> ScenarioRegistry` (process singleton, fail-open to unavailable on any error).

- [ ] **Step 1: Copy the data file**

```bash
mkdir -p src/sales_agent/scenarios/data
cp /root/code/sales-agent/销冠智慧教练手册_第6-25页.md src/sales_agent/scenarios/data/销冠智慧教练手册.md
```

Verify it landed: `test -f src/sales_agent/scenarios/data/销冠智慧教练手册.md && head -5 src/sales_agent/scenarios/data/销冠智慧教练手册.md`.

- [ ] **Step 2: Create `src/sales_agent/scenarios/models.py`**

```python
"""Pydantic / dataclass models for the scenario coach."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class AnswerSection:
    """One rendered section of a preset answer: {title, content}."""

    title: str
    content: str


@dataclass
class ScenarioQuestion:
    """A single representative question (Q01..Q11) and its preset answer."""

    id: str
    text: str
    tag: str
    answer_summary: str
    answer_sections: list[AnswerSection] = field(default_factory=list)


@dataclass
class Scenario:
    """A scenario group (S1..S6) containing 1-3 questions."""

    id: str
    name: str
    subtitle: str
    questions: list[ScenarioQuestion] = field(default_factory=list)


class ScenarioMatchDecision(BaseModel):
    """LLM decision for scenario matching.

    matched_question_id is None when no preset question matches (or when
    confidence is below threshold / parse failed — the matcher normalizes
    all non-hit cases to None before returning).
    """

    matched_question_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason_code: str = "unknown"
```

- [ ] **Step 3: Create `src/sales_agent/scenarios/loader.py`**

```python
"""Load preset scenarios from the shipped markdown into a registry."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sales_agent.scenarios.models import AnswerSection, Scenario, ScenarioQuestion

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "data" / "销冠智慧教练手册.md"
_DEFAULT_SOURCE_NAME = "销冠智慧教练手册·2026年4月版"

# Scenario header: "## 场景一　客户嫌贵 / 比价"  (full-width space or regular space)
_SCENARIO_HEADER_RE = re.compile(r"^##\s+场景([一二三四五六七八九十]+)[\s　]+(.+?)\s*$")
# Question header: "### Q01　友商配赠更高……"
_QUESTION_HEADER_RE = re.compile(r"^###\s+(Q\d{2})[\s　]+(.+?)\s*$")
# Version line in the top blockquote: "> 第一版 · 2026 年 4 月版"
_VERSION_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


@dataclass
class ScenarioRegistry:
    """In-memory registry of preset scenarios. Fail-open: is_available()=False on load failure."""

    scenarios: list[Scenario] = field(default_factory=list)
    source_name: str = _DEFAULT_SOURCE_NAME
    _available: bool = True

    def is_available(self) -> bool:
        return self._available

    def list_questions(self) -> list[dict[str, str]]:
        return [
            {"id": q.id, "text": q.text}
            for s in self.scenarios
            for q in s.questions
        ]

    def has_question(self, question_id: str) -> bool:
        return any(
            q.id == question_id
            for s in self.scenarios
            for q in s.questions
        )

    def get_question(self, question_id: str) -> ScenarioQuestion | None:
        for s in self.scenarios:
            for q in s.questions:
                if q.id == question_id:
                    return q
        return None


def _is_section_header(line: str) -> str | None:
    """Return the section title if *line* starts a new answer section, else None.

    Recognized section headers (markdown shipped with the package):
      - "#### 价值还没立住"                       (需判断-type subsections)
      - "**步骤 1：先在微信里建立价值感**"        (流程型-type steps)
      - "**一、先判断：你面对的是哪种情况？**"     (structural intro headers)

    Lines like "- **方向**：先别谈价格" do NOT match (they do not END with "**").
    """
    s = line.strip()
    if s.startswith("#### "):
        return s[5:].strip()
    m = re.match(r"^\*\*\s*(步骤\s*\d+[：:].+?)\s*\*\*$", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"^\*\*\s*([一二三四五六七八九十]+、.+?)\s*\*\*$", s)
    if m:
        return m.group(1).strip()
    return None


def _split_sections(body_lines: list[str]) -> list[AnswerSection]:
    """Split a question body into AnswerSections by section headers.

    Content before the first header becomes a leading "概述" section if non-empty.
    """
    sections: list[AnswerSection] = []
    preamble: list[str] = []
    current_title: str | None = None
    current_body: list[str] = []

    for line in body_lines:
        title = _is_section_header(line)
        if title:
            if current_title is not None:
                sections.append(
                    AnswerSection(title=current_title, content="\n".join(current_body).strip())
                )
            elif preamble:
                sections.append(AnswerSection(title="概述", content="\n".join(preamble).strip()))
            current_title = title
            current_body = []
            preamble = []
        else:
            if current_title is None:
                preamble.append(line)
            else:
                current_body.append(line)

    if current_title is not None:
        sections.append(AnswerSection(title=current_title, content="\n".join(current_body).strip()))
    elif preamble:
        sections.append(AnswerSection(title="概述", content="\n".join(preamble).strip()))

    return [s for s in sections if s.content]


def _parse_source_name(lines: list[str]) -> str:
    """Derive the citation source name from the H1 + first blockquote."""
    title = ""
    for line in lines:
        if line.startswith("# "):
            raw_title = line[2:].strip()
            # strip trailing parenthetical: "销冠智慧教练手册（第 6–25 页）" -> "销冠智慧教练手册"
            title = re.sub(r"[（(].*?[)）]\s*$", "", raw_title).strip()
            break
    version = ""
    for line in lines:
        if line.startswith("> "):
            m = _VERSION_RE.search(line)
            if m:
                version = f"{m.group(1)}年{m.group(2)}月"
                break
    if title and version:
        return f"{title}·{version}版"
    if title:
        return title
    return _DEFAULT_SOURCE_NAME


def parse_scenario_md(md_text: str) -> ScenarioRegistry:
    """Parse preset scenarios from markdown text.

    Raises ValueError if no scenarios/questions can be parsed.
    """
    lines = md_text.splitlines()
    source_name = _parse_source_name(lines)

    scenarios: list[Scenario] = []
    current_scenario: Scenario | None = None
    current_question: ScenarioQuestion | None = None
    question_body: list[str] = []

    def _flush_question() -> None:
        nonlocal current_question, question_body
        if current_question is not None:
            # First body line is the tag blockquote (">需判断 · 先判断再应对")
            tag = ""
            body = list(question_body)
            if body and body[0].lstrip().startswith(">"):
                tag = body[0].lstrip()[1:].strip()
                body = body[1:]
            current_question.tag = tag
            current_question.answer_sections = _split_sections(body)
            current_question.answer_summary = tag or current_question.text
            assert current_scenario is not None
            current_scenario.questions.append(current_question)
        current_question = None
        question_body = []

    for line in lines:
        s = line.rstrip()
        m_sc = _SCENARIO_HEADER_RE.match(s)
        if m_sc:
            _flush_question()
            sc_id = f"S{_cn_num(m_sc.group(1))}"
            current_scenario = Scenario(id=sc_id, name=m_sc.group(2).strip(), subtitle="")
            scenarios.append(current_scenario)
            continue
        m_q = _QUESTION_HEADER_RE.match(s)
        if m_q:
            _flush_question()
            current_question = ScenarioQuestion(
                id=m_q.group(1),
                text=m_q.group(2).strip(),
                tag="",
                answer_summary="",
                answer_sections=[],
            )
            question_body = []
            continue
        # scenario subtitle = first blockquote directly under a ## 场景 line
        if current_scenario is not None and current_question is None and s.lstrip().startswith(">"):
            sub = s.lstrip()[1:].strip()
            if sub and not current_scenario.subtitle:
                current_scenario.subtitle = sub
            continue
        if current_question is not None:
            # stop collecting at a horizontal rule or next scenario boundary
            if s.strip() == "---":
                _flush_question()
                continue
            question_body.append(line)

    _flush_question()

    if not scenarios or not any(s.questions for s in scenarios):
        raise ValueError("no preset scenarios/questions found in markdown")

    return ScenarioRegistry(scenarios=scenarios, source_name=source_name, _available=True)


_CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_num(s: str) -> int:
    """Map a Chinese numeral string (一..十) to int."""
    if s in _CN_NUM_MAP:
        return _CN_NUM_MAP[s]
    # fallback: try int
    try:
        return int(s)
    except ValueError:
        return 0


_REGISTRY: ScenarioRegistry | None = None


def get_scenario_registry() -> ScenarioRegistry:
    """Return the process-singleton registry, loaded from the shipped markdown.

    Fail-open: on any error, returns an unavailable registry (is_available()=False)
    so the feature degrades to off without crashing the stream.
    """
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    try:
        _REGISTRY = parse_scenario_md(_DATA_PATH.read_text(encoding="utf-8"))
        logger.info(
            "scenario_coach loaded %d scenarios / %d questions",
            len(_REGISTRY.scenarios),
            len(_REGISTRY.list_questions()),
        )
    except Exception:
        logger.exception("scenario_coach: failed to load preset scenarios; feature disabled")
        _REGISTRY = ScenarioRegistry(scenarios=[], source_name=_DEFAULT_SOURCE_NAME, _available=False)
    return _REGISTRY
```

- [ ] **Step 4: Create `src/sales_agent/scenarios/__init__.py`**

```python
"""Scenario Coach: preset sales-scenario Q&A interception.

Self-contained package. Depends only on: settings (ScenarioCoachConfig),
a chat_model passed by the graph node, and the answer_dict contract.
"""

from sales_agent.scenarios.loader import (
    ScenarioRegistry,
    get_scenario_registry,
    parse_scenario_md,
)
from sales_agent.scenarios.models import (
    AnswerSection,
    Scenario,
    ScenarioMatchDecision,
    ScenarioQuestion,
)

__all__ = [
    "AnswerSection",
    "Scenario",
    "ScenarioMatchDecision",
    "ScenarioQuestion",
    "ScenarioRegistry",
    "get_scenario_registry",
    "parse_scenario_md",
]
```

- [ ] **Step 5: Write the failing test**

Create `tests/unit/test_scenario_loader.py`:

```python
"""Tests for the scenario markdown loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from sales_agent.scenarios.loader import parse_scenario_md, get_scenario_registry
from sales_agent.scenarios.models import ScenarioMatchDecision

_DATA_PATH = Path(__file__).resolve().parents[2] / "src" / "sales_agent" / "scenarios" / "data" / "销冠智慧教练手册.md"


def test_parses_real_manual():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.is_available()
    # 6 scenarios, 11 questions
    assert len(reg.scenarios) == 6
    questions = reg.list_questions()
    assert len(questions) == 11
    assert [q["id"] for q in questions] == [f"Q{i:02d}" for i in range(1, 12)]


def test_source_name_extracted():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.source_name == "销冠智慧教练手册·2026年4月版"


def test_question_fields_populated():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    q01 = reg.get_question("Q01")
    assert q01 is not None
    assert q01.text  # non-empty representative question
    assert q01.tag  # "需判断 · 先判断再应对"
    assert q01.answer_summary
    assert len(q01.answer_sections) >= 1
    # 需判断-type Q01 has #### subsections like "价值还没立住"
    titles = [s.title for s in q01.answer_sections]
    assert any("价值还没立住" in t for t in titles)
    # every section has title + content
    for s in q01.answer_sections:
        assert s.title
        assert s.content


def test_flow_type_question_has_step_sections():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    q07 = reg.get_question("Q07")  # 流程型
    assert q07 is not None
    titles = [s.title for s in q07.answer_sections]
    assert any(t.startswith("步骤 1") for t in titles)


def test_has_question_and_get():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.has_question("Q11") is True
    assert reg.has_question("Q99") is False
    assert reg.get_question("Q99") is None


def test_malformed_md_raises():
    with pytest.raises(ValueError):
        parse_scenario_md("# not a scenario manual\n\nnothing here")
    with pytest.raises(ValueError):
        parse_scenario_md("")


def test_get_registry_singleton_available():
    reg = get_scenario_registry()
    assert reg.is_available()
    assert len(reg.scenarios) == 6


def test_decision_model_defaults():
    d = ScenarioMatchDecision()
    assert d.matched_question_id is None
    assert d.confidence == 0.0
    assert d.reason_code == "unknown"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scenario_loader.py -v`
Expected: FAIL (module not found / import error until files exist — they do now, so this may pass; if a parsing assertion fails, fix the loader).

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scenario_loader.py -v`
Expected: 8 PASS. If `test_parses_real_manual` fails on the scenario count, debug by printing `[(s.id, s.name, len(s.questions)) for s in reg.scenarios]` — the `## 场景X` regex must match all six headers (note the full-width space `　` between 场景一 and the name).

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/scenarios tests/unit/test_scenario_loader.py
git commit -m "feat(scenario-coach): add scenario data, models, and markdown loader"
```

---

## Task 3: Scenario matcher (LLM 11-way classification + fail-open)

**Files:**
- Create: `src/sales_agent/scenarios/prompt.py`
- Create: `src/sales_agent/scenarios/matcher.py`
- Modify: `src/sales_agent/scenarios/__init__.py` (export `match_scenario`)
- Test: `tests/unit/test_scenario_matcher.py`

**Interfaces:**
- Consumes: `get_scenario_registry()` (Task 2), `ScenarioMatchDecision` (Task 2), `parse_model_json` from `sales_agent.services.structured_router_output`, a `chat_model` with `async generate(messages, temperature, max_tokens) -> str` (Task uses the existing `OpenAICompatibleChat`).
- Produces: `async match_scenario(message: str, *, chat_model, confidence_threshold: float, registry: ScenarioRegistry | None = None) -> ScenarioMatchDecision`. Pure (no settings dependency — threshold passed in). Returns `matched_question_id=None` for: empty message, unavailable registry, below-threshold confidence, invalid question id, parse failure, or any LLM exception (fail-open, lesson #34).

- [ ] **Step 1: Create `src/sales_agent/scenarios/prompt.py`**

```python
"""LLM prompt for scenario matching."""

from __future__ import annotations

SCENARIO_MATCHER_PROMPT = """你是销售场景意图识别器。你的任务是判断用户问题是否与下列某个"预设销售场景问题"意图高度重合。

## 预设场景问题列表

{questions_json}

## 判断原则

- 看**意图**，不看字面。用户用不同措辞问同一件事（如"客户嫌贵"与"友商配赠更高、价格更低，我们凭什么赢"）应判为重合。
- 只有当用户问题**确实在问该预设问题所描述的销售情境**时才匹配，置信度给高（≥0.8）。
- 泛泛问候、闲聊、与销售场景无关的问题，matched_question_id 必须为 null。
- 不确定时倾向不匹配（null），不要勉强匹配。

## 关键区分示例

1. 用户："客户说别家更便宜怎么办"
   → 匹配 Q01（友商配赠更高、价格更低……我们凭什么赢？），confidence=0.9

2. 用户："今天天气真好"
   → matched_question_id=null，confidence=0.1，reason_code="irrelevant"

## 输出 JSON 格式

你输出的必须是**纯 JSON 对象**，不要使用 markdown 代码块，不要包含任何其他内容：

{{
    "matched_question_id": "Q01 或 null",
    "confidence": 0.9,
    "reason_code": "简短英文，如 price_objection / irrelevant / below_threshold"
}}
"""
```

- [ ] **Step 2: Create `src/sales_agent/scenarios/matcher.py`**

```python
"""LLM-based scenario matcher (mirrors services/evidence_router.py)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sales_agent.scenarios.loader import ScenarioRegistry, get_scenario_registry
from sales_agent.scenarios.models import ScenarioMatchDecision
from sales_agent.scenarios.prompt import SCENARIO_MATCHER_PROMPT
from sales_agent.services.structured_router_output import parse_model_json

logger = logging.getLogger(__name__)


def _no_match(reason_code: str, confidence: float = 0.0) -> ScenarioMatchDecision:
    return ScenarioMatchDecision(
        matched_question_id=None, confidence=confidence, reason_code=reason_code
    )


async def match_scenario(
    message: str,
    *,
    chat_model: Any,
    confidence_threshold: float,
    registry: ScenarioRegistry | None = None,
) -> ScenarioMatchDecision:
    """Match a user message against preset scenario questions.

    Returns a ScenarioMatchDecision with matched_question_id set only when a
    preset question is matched at or above confidence_threshold. Fail-open:
    any error → no match (lesson #34).
    """
    if registry is None:
        registry = get_scenario_registry()
    if not registry.is_available():
        return _no_match("registry_unavailable")
    if not message or not message.strip():
        return _no_match("empty_message")

    questions = registry.list_questions()
    system_prompt = SCENARIO_MATCHER_PROMPT.format(
        questions_json=json.dumps(questions, ensure_ascii=False, indent=2)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"用户问题：{message}"},
    ]

    for attempt in range(2):
        try:
            response = await chat_model.generate(
                messages=messages,
                temperature=0.0,
                max_tokens=200,
            )
            decision = parse_model_json(response, ScenarioMatchDecision)

            # Validate the matched id is a real preset question.
            if decision.matched_question_id is not None and not registry.has_question(
                decision.matched_question_id
            ):
                logger.warning(
                    "scenario_matcher: unknown question id '%s'", decision.matched_question_id
                )
                decision.matched_question_id = None
                decision.reason_code = "unknown_question_id"

            # Apply the confidence threshold.
            if decision.matched_question_id is not None and decision.confidence < confidence_threshold:
                logger.debug(
                    "scenario_matcher: match %s below threshold %.2f (conf=%.2f)",
                    decision.matched_question_id,
                    confidence_threshold,
                    decision.confidence,
                )
                decision.matched_question_id = None
                decision.reason_code = "below_threshold"

            return decision

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "scenario_matcher parse failure (attempt %d/2): %s", attempt + 1, exc
            )
            if attempt == 0:
                messages.append(
                    {
                        "role": "user",
                        "content": "输出格式不符合 JSON 规范，请仅输出一个合法的 JSON 对象，不要包含其他任何内容。",
                    }
                )
                continue
            return _no_match("parse_failure")

        except Exception as exc:
            # LLM / network failure: fail-open immediately (lesson #34).
            logger.warning("scenario_matcher LLM failure: %s", exc)
            return _no_match("llm_failure")

    return _no_match("parse_failure")
```

- [ ] **Step 3: Export `match_scenario` from the package**

In `src/sales_agent/scenarios/__init__.py`, add to the imports and `__all__`:

```python
from sales_agent.scenarios.matcher import match_scenario
```

and append `"match_scenario"` to the `__all__` list.

- [ ] **Step 4: Write the failing test**

Create `tests/unit/test_scenario_matcher.py`:

```python
"""Tests for the scenario matcher."""

from __future__ import annotations

import pytest

from sales_agent.scenarios.matcher import match_scenario
from sales_agent.scenarios.models import ScenarioMatchDecision


class _FakeChatModel:
    """Mirrors test_evidence_router._FakeChatModel."""

    def __init__(self, response: str):
        self._response = response
        self.received_messages: list[dict] | None = None

    async def generate(self, messages, **kwargs):
        self.received_messages = messages
        return self._response


class _BoomChatModel:
    """Always raises — simulates LLM/network failure."""

    async def generate(self, messages, **kwargs):
        raise RuntimeError("boom")


def _q_json_sent(model: _FakeChatModel) -> bool:
    """True if the system prompt contained the preset question list (e.g. Q01)."""
    assert model.received_messages is not None
    system = model.received_messages[0]["content"]
    return "Q01" in system and "预设场景问题列表" in system


@pytest.mark.asyncio
async def test_match_above_threshold():
    model = _FakeChatModel(
        '{"matched_question_id":"Q01","confidence":0.9,"reason_code":"price_objection"}'
    )
    decision = await match_scenario(
        "客户说别家更便宜怎么办", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id == "Q01"
    assert decision.confidence == 0.9
    assert _q_json_sent(model)


@pytest.mark.asyncio
async def test_no_match_irrelevant():
    model = _FakeChatModel(
        '{"matched_question_id":null,"confidence":0.1,"reason_code":"irrelevant"}'
    )
    decision = await match_scenario(
        "今天天气真好", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None


@pytest.mark.asyncio
async def test_below_threshold_returns_none():
    model = _FakeChatModel(
        '{"matched_question_id":"Q01","confidence":0.6,"reason_code":"price_objection"}'
    )
    decision = await match_scenario(
        "客户嫌贵", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "below_threshold"


@pytest.mark.asyncio
async def test_unknown_question_id_returns_none():
    model = _FakeChatModel(
        '{"matched_question_id":"Q99","confidence":0.95,"reason_code":"x"}'
    )
    decision = await match_scenario(
        "whatever", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "unknown_question_id"


@pytest.mark.asyncio
async def test_invalid_json_retries_then_failopen():
    model = _FakeChatModel("not valid json at all")
    decision = await match_scenario(
        "客户嫌贵", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "parse_failure"


@pytest.mark.asyncio
async def test_llm_failure_failopen():
    decision = await match_scenario(
        "客户嫌贵", chat_model=_BoomChatModel(), confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "llm_failure"


@pytest.mark.asyncio
async def test_empty_message_no_llm_call():
    model = _FakeChatModel('{"matched_question_id":"Q01","confidence":0.99}')
    decision = await match_scenario(
        "   ", chat_model=model, confidence_threshold=0.8
    )
    assert decision.matched_question_id is None
    assert decision.reason_code == "empty_message"
    assert model.received_messages is None  # LLM never called
```

- [ ] **Step 5: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scenario_matcher.py -v`
Expected: FAIL with import error (matcher.py not yet present) — but it is present from Step 2, so run to confirm pass/fail of logic.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scenario_matcher.py -v`
Expected: 7 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/scenarios/prompt.py src/sales_agent/scenarios/matcher.py src/sales_agent/scenarios/__init__.py tests/unit/test_scenario_matcher.py
git commit -m "feat(scenario-coach): add LLM scenario matcher with fail-open"
```

---

## Task 4: Citation label for scenario_coach source type

**Files:**
- Modify: `src/sales_agent/integrations/dingtalk/citation.py` (line 10-13)
- Test: `tests/unit/test_scenario_citation.py`

**Interfaces:**
- Produces: `source_type_label("scenario_coach") == "教练手册"`. Combined with the node setting `source_type="scenario_coach"` on the citation source, the DingTalk footer renders `📖 引用来源\n[1] 销冠智慧教练手册·2026年4月版 · 教练手册`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_scenario_citation.py`:

```python
"""Tests for scenario_coach citation label."""

from __future__ import annotations

from sales_agent.integrations.dingtalk.citation import (
    format_citation_block,
    source_type_label,
)


def test_scenario_coach_label():
    assert source_type_label("scenario_coach") == "教练手册"


def test_scenario_coach_citation_block():
    sources = [
        {
            "title": "销冠智慧教练手册·2026年4月版",
            "display_title": "销冠智慧教练手册·2026年4月版",
            "source_type": "scenario_coach",
        }
    ]
    block = format_citation_block(sources)
    assert "销冠智慧教练手册·2026年4月版" in block
    assert "教练手册" in block
    assert "引用来源" in block


def test_existing_labels_unchanged():
    assert source_type_label("ontology") == "知识图谱"
    assert source_type_label("web") == "网络搜索"
    assert source_type_label(None) == "知识库"
    assert source_type_label("rag") == "知识库"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scenario_citation.py -v`
Expected: FAIL — `source_type_label("scenario_coach")` returns `"知识库"` (default), not `"教练手册"`.

- [ ] **Step 3: Add the label mapping**

In `src/sales_agent/integrations/dingtalk/citation.py`, the `_SOURCE_TYPE_LABELS` dict (lines 10-13) currently is:

```python
_SOURCE_TYPE_LABELS = {
    "ontology": "知识图谱",
    "web": "网络搜索",
}
```

Replace it with:

```python
_SOURCE_TYPE_LABELS = {
    "ontology": "知识图谱",
    "web": "网络搜索",
    "scenario_coach": "教练手册",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scenario_citation.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/integrations/dingtalk/citation.py tests/unit/test_scenario_citation.py
git commit -m "feat(scenario-coach): add scenario_coach citation label"
```

---

## Task 5: Online State flag + input_state wiring (lesson #35 coverage)

**Files:**
- Modify: `src/sales_agent/graph/online/state.py` (after line 34)
- Modify: `src/sales_agent/services/online_conversation.py` (after line 221)
- Modify: `src/sales_agent/integrations/dingtalk/graph_stream.py` (after line 103)
- Test: `tests/unit/graph/test_scenario_coach_graph.py` (state-field + wiring assertions; integration behavior is in Task 7)

**Interfaces:**
- Produces: a new `scenario_coach_enabled: bool` field on `OnlineConversationState`, set from `settings.scenario_coach.enabled` in BOTH input_state builders. When False (default), `route_online_message` (Task 7) returns the original `flow_action` → graph topology identical to today.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/graph/test_scenario_coach_graph.py` (this file grows in Task 7; for now just the wiring assertion):

```python
"""Tests for scenario_coach graph wiring (state flag + input_state coverage)."""

from __future__ import annotations

from types import SimpleNamespace

from sales_agent.graph.online.edges import route_online_message


def test_route_online_message_disabled_passes_through():
    """When scenario_coach_enabled is False/absent, routing is unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": False}) == "chat"
    assert route_online_message({"flow_action": "direct_chat"}) == "direct_chat"
    assert route_online_message({"flow_action": "duplicate"}) == "duplicate"
    assert route_online_message({}) == "chat"  # default


def test_route_online_message_enabled_diverts_chat_paths():
    """When enabled, chat/direct_chat divert to scenario_coach; others unchanged."""
    assert route_online_message({"flow_action": "chat", "scenario_coach_enabled": True}) == "scenario_coach"
    assert route_online_message({"flow_action": "direct_chat", "scenario_coach_enabled": True}) == "scenario_coach"
    # guided-flow / duplicate paths are NOT intercepted
    assert route_online_message({"flow_action": "start", "scenario_coach_enabled": True}) == "start"
    assert route_online_message({"flow_action": "duplicate", "scenario_coach_enabled": True}) == "duplicate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_graph.py -v`
Expected: FAIL — `route_online_message` currently returns `flow_action` unchanged (no diversion), so the `enabled_diverts` assertions fail.

- [ ] **Step 3: Add state field**

In `src/sales_agent/graph/online/state.py`, after line 34 (`    topic_routing_enabled: bool`), insert:

```python
    scenario_coach_enabled: bool
```

- [ ] **Step 4: Set the flag in the HTTP-path input_state**

In `src/sales_agent/services/online_conversation.py`, after line 221 (`        "topic_routing_enabled": settings.topic_routing.enabled,`), insert:

```python
        "scenario_coach_enabled": settings.scenario_coach.enabled,
```

- [ ] **Step 5: Set the flag in the DingTalk-stream-path input_state (lesson #35)**

In `src/sales_agent/integrations/dingtalk/graph_stream.py`, after line 103 (`        "topic_routing_enabled": settings.topic_routing.enabled,`), insert:

```python
        "scenario_coach_enabled": settings.scenario_coach.enabled,
```

- [ ] **Step 6: Implement `route_online_message` diversion**

In `src/sales_agent/graph/online/edges.py`, replace the `route_online_message` function (lines 8-14) with:

```python
def route_online_message(state: OnlineConversationState) -> str:
    """Return the destination node name based on ``flow_action``.

    When ``scenario_coach_enabled`` is set, ``chat`` and ``direct_chat``
    divert to the ``scenario_coach`` node first (the original flow_action
    is preserved in state so ``route_after_scenario`` can resume the
    correct downstream path on a miss). Returns one of ``"duplicate"``,
    ``"start"``, ``"cancel"``, ``"advance"``, ``"chat"``, ``"direct_chat"``,
    or ``"scenario_coach"``.
    """
    flow_action = state.get("flow_action", "chat")
    if state.get("scenario_coach_enabled", False) and flow_action in ("chat", "direct_chat"):
        return "scenario_coach"
    return flow_action
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_graph.py -v`
Expected: 2 PASS.

- [ ] **Step 8: Regression — confirm existing online-graph tests still pass**

Run: `.venv/bin/pytest tests/unit/graph/ -v`
Expected: all PASS (existing tests construct state without `scenario_coach_enabled` → defaults to absent/False → original routing → no behavior change). This is the lesson #35 safety property.

- [ ] **Step 9: Commit**

```bash
git add src/sales_agent/graph/online/state.py src/sales_agent/graph/online/edges.py src/sales_agent/services/online_conversation.py src/sales_agent/integrations/dingtalk/graph_stream.py tests/unit/graph/test_scenario_coach_graph.py
git commit -m "feat(scenario-coach): wire scenario_coach_enabled flag into state + both input builders"
```

---

## Task 6: scenario_coach_node + log_scenario_response_node

**Files:**
- Modify: `src/sales_agent/graph/online/nodes.py` (imports + two new node functions)
- Test: `tests/unit/graph/test_scenario_coach_node.py`

**Interfaces:**
- Consumes: `match_scenario` + `get_scenario_registry` (Task 2/3), `get_settings().scenario_coach.confidence_threshold` (Task 1), `_unpack_context` (existing), `conversation_logger.log_conversation` (existing), `OnlineConversationState` (existing).
- Produces:
  - `async scenario_coach_node(state, config) -> dict`: on hit returns `{"answer_dict": {...summary, sections, sources...}, "response_kind": "scenario", "last_event_id": ...}`; on miss returns `{"last_event_id": ...}` (passthrough, leaves flow_action intact for `route_after_scenario`). Testability seam: `ctx.get("scenario_matcher_override")` (an awaitable taking `message`/`chat_model`/`confidence_threshold`) bypasses the real matcher + settings.
  - `async log_scenario_response_node(state, config) -> dict`: persists the scenario answer via `conversation_logger.log_conversation(..., task_type="scenario", path="scenario")`; returns `{"last_event_id": ...}`. Mirrors `log_flow_output_node` (nodes.py:314-349).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/graph/test_scenario_coach_node.py`:

```python
"""Tests for scenario_coach_node and log_scenario_response_node."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_agent.graph.online.nodes import (
    log_scenario_response_node,
    scenario_coach_node,
)
from sales_agent.scenarios.models import ScenarioMatchDecision


def _build_config(context: dict) -> dict:
    return {"configurable": {"__pregel_runtime": SimpleNamespace(context=context)}}


def _base_state(**overrides) -> dict:
    state = {
        "tenant_id": "t1",
        "agent_id": "a1",
        "user_id": "u1",
        "channel": "dingtalk",
        "conversation_id": "c1",
        "message": "客户说别家更便宜怎么办",
        "event_id": "ev-1",
        "flow_action": "chat",
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_scenario_node_hit_sets_answer_dict_with_source():
    override = AsyncMock(
        return_value=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.9, reason_code="price_objection"
        )
    )
    cfg = _build_config({"chat_model": None, "db": None, "scenario_matcher_override": override})
    result = await scenario_coach_node(_base_state(), cfg)
    assert result["response_kind"] == "scenario"
    answer = result["answer_dict"]
    assert answer["summary"]
    assert isinstance(answer["sections"], list) and answer["sections"]
    # section contract: title + content
    for s in answer["sections"]:
        assert "title" in s and "content" in s
    # source citation: manual name + scenario_coach type
    assert answer["sources"] == [
        {
            "title": "销冠智慧教练手册·2026年4月版",
            "display_title": "销冠智慧教练手册·2026年4月版",
            "source_type": "scenario_coach",
        }
    ]
    assert result["last_event_id"] == "ev-1"


@pytest.mark.asyncio
async def test_scenario_node_miss_passthrough():
    override = AsyncMock(
        return_value=ScenarioMatchDecision(
            matched_question_id=None, confidence=0.1, reason_code="irrelevant"
        )
    )
    cfg = _build_config({"chat_model": None, "db": None, "scenario_matcher_override": override})
    result = await scenario_coach_node(_base_state(), cfg)
    assert "answer_dict" not in result
    assert "response_kind" not in result
    assert result == {"last_event_id": "ev-1"}


@pytest.mark.asyncio
async def test_log_scenario_response_node_persists():
    log_mock = AsyncMock()
    # Patch conversation_logger.log_conversation for the test.
    import sales_agent.graph.online.nodes as nodes_mod

    orig = nodes_mod.conversation_logger.log_conversation
    nodes_mod.conversation_logger.log_conversation = log_mock
    try:
        cfg = _build_config({"db": object()})
        state = _base_state(
            answer_dict={"summary": "s", "sections": [], "sources": []},
            original_message="客户嫌贵",
        )
        result = await log_scenario_response_node(state, cfg)
        assert result == {"last_event_id": "ev-1"}
        log_mock.assert_awaited_once()
        _, kwargs = log_mock.call_args
        assert kwargs["task_type"] == "scenario"
        assert kwargs["path"] == "scenario"
        assert kwargs["message"] == "客户嫌贵"
    finally:
        nodes_mod.conversation_logger.log_conversation = orig


@pytest.mark.asyncio
async def test_log_scenario_response_node_no_db_is_noop():
    cfg = _build_config({"db": None})
    result = await log_scenario_response_node(_base_state(), cfg)
    assert result == {"last_event_id": "ev-1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_node.py -v`
Expected: FAIL — `ImportError: cannot import name 'scenario_coach_node'` / `log_scenario_response_node`.

- [ ] **Step 3: Add imports to nodes.py**

In `src/sales_agent/graph/online/nodes.py`, in the FIRST import block (after line 48, BEFORE the duplicated block at line 50), add:

```python
from sales_agent.core.config import get_settings
from sales_agent.scenarios import get_scenario_registry, match_scenario
```

(Do NOT also add to the duplicated block at 50-70 — one copy is sufficient; Python's `from … import` is idempotent and the names remain in module scope. The duplicate is a pre-existing bug, left untouched per Global Constraints.)

- [ ] **Step 4: Add `scenario_coach_node`**

In `src/sales_agent/graph/online/nodes.py`, add this function (place it right after `log_flow_output_node`, before the `context_resolution_node` section, around line 350):

```python
# ====================================================================
# scenario_coach
# ====================================================================


async def scenario_coach_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Match the user message against preset sales scenarios.

    On a high-confidence match: set ``answer_dict`` (preset answer + manual
    source citation) and ``response_kind="scenario"`` so the graph
    short-circuits to the log node + END. On a miss: passthrough (return
    only ``last_event_id``), leaving ``flow_action`` intact so
    ``route_after_scenario`` resumes the normal downstream path.

    Fail-open: any matcher failure is treated as a miss (lesson #34).
    """
    ctx = _unpack_context(config)
    chat_model = ctx.get("chat_model") if ctx else None
    message = state.get("message", "")
    threshold = get_settings().scenario_coach.confidence_threshold

    matcher_fn = ctx.get("scenario_matcher_override") if ctx else None
    if matcher_fn is not None:
        decision = await matcher_fn(
            message=message, chat_model=chat_model, confidence_threshold=threshold
        )
    else:
        decision = await match_scenario(
            message=message, chat_model=chat_model, confidence_threshold=threshold
        )

    if decision.matched_question_id is None:
        logger.debug("scenario_coach: no match (reason=%s)", decision.reason_code)
        return {"last_event_id": state.get("event_id")}

    registry = get_scenario_registry()
    question = registry.get_question(decision.matched_question_id)
    if question is None:
        logger.warning("scenario_coach: matched id %s not in registry", decision.matched_question_id)
        return {"last_event_id": state.get("event_id")}

    answer_dict = {
        "summary": question.answer_summary,
        "sections": [
            {"title": s.title, "content": s.content} for s in question.answer_sections
        ],
        "sources": [
            {
                "title": registry.source_name,
                "display_title": registry.source_name,
                "source_type": "scenario_coach",
            }
        ],
    }
    logger.info(
        "scenario_coach: matched %s (confidence=%.2f)",
        decision.matched_question_id,
        decision.confidence,
    )
    return {
        "answer_dict": answer_dict,
        "response_kind": "scenario",
        "last_event_id": state.get("event_id"),
    }
```

- [ ] **Step 5: Add `log_scenario_response_node`**

In the same file, add this function immediately after `scenario_coach_node`:

```python
async def log_scenario_response_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Persist a scenario-coach preset answer to the conversation log.

    Mirrors ``log_flow_output_node`` but with task_type/path="scenario".
    """
    ctx = _unpack_context(config)
    db = ctx.get("db") if ctx else None

    if db is not None:
        answer_dict = state.get("answer_dict", {})
        try:
            await conversation_logger.log_conversation(
                db,
                tenant_id=state.get("tenant_id", ""),
                user_id=state.get("user_id", ""),
                channel=state.get("channel", "local"),
                agent_id=state.get("agent_id"),
                conversation_id=state.get("conversation_id", ""),
                message=state.get("original_message") or state.get("message", ""),
                task_type="scenario",
                answer_dict=answer_dict,
                path="scenario",
            )
        except Exception:
            logger.warning("Failed to log scenario response", exc_info=True)

    return {"last_event_id": state.get("event_id")}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_node.py -v`
Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/online/nodes.py tests/unit/graph/test_scenario_coach_node.py
git commit -m "feat(scenario-coach): add scenario_coach_node + log_scenario_response_node"
```

---

## Task 7: Graph wiring (register nodes + conditional edges + integration tests)

**Files:**
- Modify: `src/sales_agent/graph/online/edges.py` (add `route_after_scenario`)
- Modify: `src/sales_agent/graph/online/graph.py` (imports + node registration + edges)
- Test: `tests/unit/graph/test_scenario_coach_graph.py` (extend with integration tests)

**Interfaces:**
- Consumes: `scenario_coach_node`, `log_scenario_response_node` (Task 6), `route_online_message` diversion (Task 5).
- Produces: the wired graph. Flow when enabled:
  - `normalize_turn → [route_online_message] → scenario_coach → [route_after_scenario] → {scenario_hit: log_scenario_response → END | chat: context_resolution | direct_chat: direct_evidence_routing}`
  - When disabled: `route_online_message` returns `flow_action` → original mapping (`chat→context_resolution`, `direct_chat→direct_evidence_routing`) → scenario_coach never entered.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/unit/graph/test_scenario_coach_graph.py`:

```python
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from unittest.mock import AsyncMock

from sales_agent.graph.online.graph import build_online_graph
from sales_agent.scenarios.models import ScenarioMatchDecision
from sales_agent.services.structured_router_output import EvidenceDecision


def _evidence_override_decision() -> EvidenceDecision:
    """A minimal valid EvidenceDecision so direct_evidence_routing needs no LLM."""
    return EvidenceDecision(
        intent="general_sales_coaching",
        response_mode="direct",
        knowledge_policy="none",
        knowledge_scope=[],
        retrieval_query=None,
        confidence=0.5,
        reason_code="test",
    )


def _runtime_ctx(*, scenario_coach_enabled: bool, matcher_decision, chat_runner=None):
    """Build a runtime context dict with a scenario_matcher override + stub chat.

    evidence_router_override is always injected so the direct_chat miss path
    (direct_evidence_routing_node) does not need a real chat_model.
    """
    ctx = {
        "db": None,
        "chat_model": None,
        "now": None,
        "scenario_matcher_override": AsyncMock(return_value=matcher_decision),
        "evidence_router_override": AsyncMock(return_value=_evidence_override_decision()),
    }
    if chat_runner is not None:
        ctx["chat_runner"] = chat_runner
    return ctx


def _input(message: str, *, scenario_coach_enabled: bool) -> dict:
    return {
        "tenant_id": "t1",
        "agent_id": "a1",
        "user_id": "u1",
        "session_user_id": "u1",
        "channel": "dingtalk",
        "conversation_id": "c-scenario-1",
        "message": message,
        "entry_action": None,
        "event_id": "ev-1",
        "guided_flows_enabled": False,
        "topic_routing_enabled": False,
        "scenario_coach_enabled": scenario_coach_enabled,
    }


class _StubChatRunner:
    """Replaces the Chat subgraph; records that it ran (a 'miss' should reach it)."""

    def __init__(self):
        self.called = False

    async def ainvoke(self, chat_input, config=None, context=None):
        self.called = True
        return {"answer_dict": {"summary": "AI answer", "sections": [], "sources": []}}


@pytest.mark.asyncio
async def test_enabled_hit_returns_preset_and_skips_chat():
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    ctx = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.9, reason_code="price_objection"
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("客户说别家更便宜怎么办", scenario_coach_enabled=True),
        config={"configurable": {"thread_id": "c-scenario-1"}},
        context=ctx,
    )
    assert result["response_kind"] == "scenario"
    assert result["answer_dict"]["summary"]
    assert result["answer_dict"]["sources"][0]["source_type"] == "scenario_coach"
    # Chat subgraph (AI generation) must NOT have run on a hit.
    assert runner.called is False


@pytest.mark.asyncio
async def test_enabled_miss_falls_through_to_chat():
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    ctx = _runtime_ctx(
        scenario_coach_enabled=True,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id=None, confidence=0.1, reason_code="irrelevant"
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("今天天气真好", scenario_coach_enabled=True),
        config={"configurable": {"thread_id": "c-scenario-2"}},
        context=ctx,
    )
    # Miss → normal chat path runs.
    assert runner.called is True
    assert result.get("response_kind") in (None, "chat")
    assert result["answer_dict"]["summary"] == "AI answer"


@pytest.mark.asyncio
async def test_disabled_uses_original_path():
    """Feature off → scenario_coach never entered; chat runs normally (regression)."""
    graph = build_online_graph().compile(checkpointer=InMemorySaver())
    runner = _StubChatRunner()
    # matcher override present but must NOT be consulted when disabled.
    ctx = _runtime_ctx(
        scenario_coach_enabled=False,
        matcher_decision=ScenarioMatchDecision(
            matched_question_id="Q01", confidence=0.99
        ),
        chat_runner=runner,
    )
    result = await graph.ainvoke(
        _input("随便问个问题", scenario_coach_enabled=False),
        config={"configurable": {"thread_id": "c-scenario-3"}},
        context=ctx,
    )
    assert runner.called is True
    # No scenario response.
    assert result.get("response_kind") != "scenario"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_graph.py -v`
Expected: FAIL — `route_after_scenario` not defined / graph not wired; the enabled-hit test will not reach `response_kind == "scenario"`.

- [ ] **Step 3: Add `route_after_scenario` edge function**

In `src/sales_agent/graph/online/edges.py`, after `route_context_resolution`, add:

```python
def route_after_scenario(state: OnlineConversationState) -> str:
    """Return the next node after the scenario_coach node.

    - ``"scenario_hit"`` → log_scenario_response → END
    - otherwise resume the original path: ``"direct_chat"`` →
      direct_evidence_routing, ``"chat"`` → context_resolution.
    """
    if state.get("response_kind") == "scenario":
        return "scenario_hit"
    if state.get("flow_action") == "direct_chat":
        return "direct_chat"
    return "chat"
```

- [ ] **Step 4: Register nodes + edges in graph.py**

In `src/sales_agent/graph/online/graph.py`:

4a. Update the node import (lines 34-45) to add the two new nodes. After `normalize_turn_node,` in that import block, add:

```python
    log_scenario_response_node,
    scenario_coach_node,
```

4b. Update the edges import (line 33) to add `route_after_scenario`:

```python
from sales_agent.graph.online.edges import (
    route_after_scenario,
    route_context_resolution,
    route_online_message,
)
```

4c. In `build_online_graph()`, after the existing `builder.add_node(...)` calls (after line 88, `builder.add_node("log_flow_output", log_flow_output_node)`), add:

```python
    builder.add_node("scenario_coach", scenario_coach_node)
    builder.add_node("log_scenario_response", log_scenario_response_node)
```

4d. In the `normalize_turn` conditional-edges mapping (lines 94-105), add the `"scenario_coach"` key. The mapping becomes:

```python
    builder.add_conditional_edges(
        "normalize_turn",
        route_online_message,
        {
            "duplicate": "duplicate",
            "start": "guided_flow",
            "cancel": "guided_flow",
            "advance": "guided_flow",
            "chat": "context_resolution",
            "direct_chat": "direct_evidence_routing",
            "scenario_coach": "scenario_coach",
        },
    )
```

4e. After the `context_resolution` conditional edges block (after line 116), add the scenario_coach conditional edges:

```python
    # From scenario_coach: preset-answer hit -> log -> END, else resume normal path
    builder.add_conditional_edges(
        "scenario_coach",
        route_after_scenario,
        {
            "scenario_hit": "log_scenario_response",
            "chat": "context_resolution",
            "direct_chat": "direct_evidence_routing",
        },
    )
```

4f. After the guided-flow edge block (after line 131, `builder.add_edge("log_flow_output", END)`), add:

```python
    builder.add_edge("log_scenario_response", END)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/graph/test_scenario_coach_graph.py -v`
Expected: 5 PASS (2 edge-unit from Task 5 + 3 integration).

- [ ] **Step 6: Full graph regression**

Run: `.venv/bin/pytest tests/unit/graph/ -v`
Expected: all PASS. Pay attention to `test_online_graph.py` and `test_context_routing_nodes.py` — they must still pass (they don't set `scenario_coach_enabled` → diversion off → original behavior).

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/graph/online/edges.py src/sales_agent/graph/online/graph.py tests/unit/graph/test_scenario_coach_graph.py
git commit -m "feat(scenario-coach): wire scenario_coach node + log node into Online Graph"
```

---

## Task 8: Docs (changelog + README) + full verification

**Files:**
- Create: `changelog/2026-07-08.md`
- Modify: `README.md` (product-doc index / update-log pointer)

- [ ] **Step 1: Write the changelog**

Create `changelog/2026-07-08.md`:

```markdown
# 2026-07-08 升级日志

## 场景教练（Scenario Coach）新增

- **改动对象**：`src/sales_agent/scenarios/`（新包）、`graph/online/{nodes,edges,graph,state}.py`、`core/config.py`、`integrations/dingtalk/{graph_stream,citation}.py`、`services/online_conversation.py`、`config/default.yaml`、`.env.example`。
- **类型**：新功能（feature flag，默认关闭）。
- **影响范围**：Online Graph 新增 `scenario_coach` 拦截节点；仅在 `SCENARIO_COACH_ENABLED=true` 的实例生效，其余实例行为零变化。
- **改动明细**：
  - 新增自包含包 `scenarios/`：`data/销冠智慧教练手册.md`（6 场景 11 预设问题）、`loader.py`（md→registry 单例，fail-open）、`matcher.py`（LLM 11 选 1 + none 分类，复用 `parse_model_json`，置信度阈值默认 0.8）、`prompt.py`、`models.py`。
  - Online Graph 在 `normalize_turn` 后插入 `scenario_coach` 节点：命中→返回预设 `answer_dict`（含 `《销冠智慧教练手册·2026年4月版》` 来源）+ `response_kind="scenario"`→`log_scenario_response`→END；未命中→透传原路径（context_resolution / direct_evidence_routing）。
  - 新增 `ScenarioCoachConfig{enabled=False, confidence_threshold=0.8}`，env 覆盖 `SCENARIO_COACH_ENABLED`（bool）/ `SCENARIO_COACH_CONFIDENCE_THRESHOLD`（float，repo 首个 float-from-env 覆盖）。
  - `scenario_coach_enabled` 状态字段注入两个 input_state 构造点（`online_conversation.py` HTTP 路径 + `graph_stream.py` 钉钉 stream 生产路径）——覆盖 lesson #35 的 stream 入口。
  - `citation.py` 新增 `scenario_coach → 教练手册` 来源标签。
  - 无 DB 变更、无 Alembic migration、无 seed；场景内容随镜像分发。
- **原因**：针对单租户实例提供预设销售场景问答能力，命中即出预设答案（含手册来源），未命中走正常 AI；通过 env flag 实现实例级开关与跨机（CI/CD prod3）快速部署。
- **验证**：`.venv/bin/pytest tests/unit -q` 全绿；部署后需 `docker logs sales-agent-<tenant>-stream` 确认 `scenario_coach loaded 6 scenarios / 11 questions` 且无 crash（lesson #4 生产入口验证）。
```

- [ ] **Step 2: Update README**

In `README.md`, update the 「产品文档对照」/「更新日志」sections per CLAUDE.md rule: add a one-line pointer to `changelog/2026-07-08.md` for the scenario coach feature, and update any scenario/feature count summary if such a list exists. (Read the relevant README section first; if there is no per-feature list, just append the changelog pointer.)

Run: `rg -n "更新日志|changelog|产品文档对照" README.md` to locate the section, then add the pointer line.

- [ ] **Step 3: Run the full unit test suite**

Run: `.venv/bin/pytest tests/unit -q`
Expected: all PASS (no regressions). If any pre-existing test breaks due to the graph topology change, investigate — disabled-path must be behavior-identical (lesson #35).

- [ ] **Step 4: Smoke-import the wired graph**

Run: `.venv/bin/python -c "from sales_agent.graph.online.graph import build_online_graph; g=build_online_graph().compile(); print('nodes:', sorted(g.get_graph().nodes.keys()))"`
Expected: prints a node list including `scenario_coach` and `log_scenario_response`, no import errors.

- [ ] **Step 5: Commit docs**

```bash
git add changelog/2026-07-08.md README.md
git commit -m "docs(scenario-coach): changelog + README update"
```

- [ ] **Step 6: Final verification checklist (lesson #4 / #34 / #35)**

Confirm before declaring done:
- [ ] `SCENARIO_COACH_ENABLED` absent/false → all existing graph tests pass (disabled = original behavior).
- [ ] Feature on + hit → preset answer + manual source, chat subgraph NOT called.
- [ ] Feature on + miss → normal chat path runs.
- [ ] LLM failure / parse failure / registry-unavailable → no match, no crash (fail-open).
- [ ] Both input_state builders set `scenario_coach_enabled` (grep): `rg -n "scenario_coach_enabled" src/` shows `state.py`, `online_conversation.py`, `graph_stream.py`, `edges.py`.
- [ ] No DB/migration files added: `git status -- migrations/` clean.

```bash
rg -n "scenario_coach_enabled" src/
git status -- src/sales_agent/migrations/
```

- [ ] **Step 7: Merge worktree branch back to main (per CLAUDE.md worktree workflow)**

After all tests pass and the worktree branch is committed, merge back to `main` and exit the worktree (the executing skill handles this; do not push to origin / do not trigger CI deploy without explicit user instruction).

---

## Self-Review Notes (for the implementer)

- **Spec coverage**: every spec section maps to a task — §4 architecture (Tasks 2,3,6,7), §5 data+loader (Task 2), §6 matcher (Task 3), §7 graph wiring (Tasks 5,6,7), §8 source citation (Tasks 4,6), §9 config+migration (Tasks 1,5), §10 error handling (Task 3 fail-open + Task 6 node guards), §11 tests (each task), §12 upgrade path (out of scope, noted).
- **Type consistency**: `answer_dict.sections` elements use `title`/`content` everywhere (loader builds `{title,content}`, renderer reads `title`/`content`). `sources` use `title`/`display_title`/`source_type`. `ScenarioMatchDecision` field names (`matched_question_id`, `confidence`, `reason_code`) are identical in models.py, matcher.py, and the node/prompt.
- **Lesson #35**: the flag is set in BOTH input_state builders (HTTP + stream); default-off means any test/entry that omits it gets original behavior — safe.
- **Lesson #34**: matcher catches `Exception` (LLM failure) → fail-open; parse failures retry once then fail-open; node guards a missing registry question.
- **Lesson #27 (invariant note)**: scenario preset answers intentionally bypass `generate_node`/`PromptRegistry` — they are static coaching content from the manual, not LLM-generated, so the centralized-prompt invariant does not apply to this path. This is the designed bypass, not a regression.
