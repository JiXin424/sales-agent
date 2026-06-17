"""管道阶段耗时追踪器。

记录请求处理各阶段的耗时，输出结构化的延迟日志。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PipelineTimings:
    """记录每个阶段的耗时（毫秒）。

    用法::

        timings = PipelineTimings()
        timings.start("routing")
        # ... do routing work ...
        timings.end("routing")
        print(timings.to_dict())  # {"routing": 35.2, ...}
    """

    stages: dict[str, float] = field(default_factory=dict)
    _starts: dict[str, float] = field(default_factory=dict, repr=False)

    def start(self, stage: str) -> None:
        """记录某阶段开始时间。"""
        self._starts[stage] = time.monotonic()

    def end(self, stage: str) -> None:
        """记录某阶段结束，计算耗时（毫秒）。"""
        start = self._starts.pop(stage, None)
        if start is not None:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.stages[stage] = round(elapsed_ms, 2)

    def to_dict(self) -> dict[str, float]:
        """输出含 total 和各阶段耗时的字典。"""
        result = dict(self.stages)
        result["total"] = self.total_ms
        return result

    @property
    def total_ms(self) -> float:
        """所有已记录阶段的总耗时。"""
        return round(sum(self.stages.values()), 2)
