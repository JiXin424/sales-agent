"""回归测试：销售动作抽取器的 now 时区换算（parser._build_messages）。

守护 bug：`now` 为 UTC（容器默认时钟）但 prompt 声明 `Asia/Shanghai` 时，
LLM 会把相对时间按 +08:00 输出同一钟点数、换算回 UTC 早 8 小时，被
validate 判 `past_time` 拒绝建提醒。修复后 `_build_messages` 必须把 now
换算到声明时区，使 prompt 自洽。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sales_agent.llm.prompt_loader import load_prompts
from sales_agent.services.sales_actions.parser import _build_messages


@pytest.fixture(scope="module", autouse=True)
def _prompts_loaded():
    # get_prompt 需要 _PROMPTS 已加载
    load_prompts("config/llm_config.yaml")


def _now_line(messages) -> str:
    return messages[1]["content"].splitlines()[0]


def test_utc_now_converted_to_declared_timezone():
    """UTC now → prompt 的「当前时间」必须是声明时区的本地时刻（+08:00）。"""
    now_utc = datetime(2026, 7, 10, 16, 42, 31, tzinfo=timezone.utc)
    msgs = _build_messages("1分钟后提醒我测试", now_utc, "Asia/Shanghai")
    now_line = _now_line(msgs)
    # 同一时刻在 Asia/Shanghai 是次日 00:42:31+08:00
    assert "2026-07-11T00:42:31+08:00" in now_line
    assert "+00:00" not in now_line  # 不能再以 UTC 呈现


def test_naive_now_assumed_utc_then_converted():
    """naive now 视为 UTC 再换算，避免 astimezone 落到宿主机本地时区。"""
    naive = datetime(2026, 7, 10, 16, 42, 31)
    msgs = _build_messages("x", naive, "Asia/Shanghai")
    assert "2026-07-11T00:42:31+08:00" in _now_line(msgs)


def test_unknown_timezone_falls_back_without_raising():
    """未知时区名不得中断抽取，退回原 now。"""
    now_utc = datetime(2026, 7, 10, 16, 42, 31, tzinfo=timezone.utc)
    msgs = _build_messages("x", now_utc, "Not/AZone")
    # 不抛异常即可；now 行仍存在
    assert _now_line(msgs).startswith("当前时间：")


def test_system_prompt_is_extractor_template():
    now_utc = datetime(2026, 7, 10, 16, 42, 31, tzinfo=timezone.utc)
    msgs = _build_messages("提醒我给张总回电话", now_utc, "Asia/Shanghai")
    assert msgs[0]["role"] == "system"
    assert "销售动作" in msgs[0]["content"]
