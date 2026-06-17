"""PipelineTimings 单元测试。"""

import time

from sales_agent.services.latency_tracker import PipelineTimings


class TestPipelineTimings:
    """PipelineTimings 基础功能测试。"""

    def test_single_stage(self):
        """记录单个阶段耗时。"""
        timings = PipelineTimings()
        timings.start("routing")
        time.sleep(0.01)  # 10ms
        timings.end("routing")

        assert "routing" in timings.stages
        assert timings.stages["routing"] >= 8  # 至少 8ms（允许误差）

    def test_multiple_stages(self):
        """记录多个阶段耗时。"""
        timings = PipelineTimings()
        for stage in ["validation", "routing", "generation"]:
            timings.start(stage)
            time.sleep(0.005)
            timings.end(stage)

        assert len(timings.stages) == 3
        assert all(s in timings.stages for s in ["validation", "routing", "generation"])

    def test_total_ms(self):
        """总耗时为各阶段之和。"""
        timings = PipelineTimings()
        timings.stages = {"a": 100.0, "b": 200.0, "c": 50.0}
        assert timings.total_ms == 350.0

    def test_to_dict_includes_total(self):
        """to_dict 包含 total 字段。"""
        timings = PipelineTimings()
        timings.stages = {"routing": 35.0, "generation": 3500.0}
        d = timings.to_dict()

        assert d["routing"] == 35.0
        assert d["generation"] == 3500.0
        assert d["total"] == 3535.0

    def test_end_without_start_ignored(self):
        """没有 start 的 end 被忽略。"""
        timings = PipelineTimings()
        timings.end("nonexistent")
        assert "nonexistent" not in timings.stages

    def test_empty_timings(self):
        """空计时器。"""
        timings = PipelineTimings()
        assert timings.total_ms == 0.0
        assert timings.to_dict() == {"total": 0.0}
