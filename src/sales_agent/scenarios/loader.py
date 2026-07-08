"""Load preset scenarios from the shipped markdown into a registry."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sales_agent.scenarios.models import AnswerSection, Scenario, ScenarioQuestion

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "data" / "销冠智慧教练手册.md"
_DEFAULT_SOURCE_NAME = "销冠智慧教练手册·2026年4月版"

# Scenario header: "## 场景一　客户嫌贵 / 比价"  (full-width space or regular space)
_SCENARIO_HEADER_RE = re.compile(r"^##\s+场景([一二三四五六七八九十]+)[\s　]+(.+?)\s*$")
# Question header: "### Q01　友商配赠更高……"
_QUESTION_HEADER_RE = re.compile(r"^###\s+(Q\d{2})[\s　]*(.+?)\s*$")
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
            current_question.answer_summary = current_question.text
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
