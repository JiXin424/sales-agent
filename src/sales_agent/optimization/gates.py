"""Release gates: evaluate candidates against hard and soft thresholds.

Hard gates (safety, leakage, critical facts) block release regardless of
aggregate score gains. Soft gates (fixed-suite regression, latency, token
cost) require approval but do not block automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sales_agent.optimization.types import EvalComparison


@dataclass
class GateResult:
    """Result of evaluating a candidate against release gates."""

    allowed: bool = True
    hard_failures: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    target_improvement: float = 0.0
    fixed_regression: float = 0.0
    fact_errors: int = 0
    fabrication_count: int = 0
    safety_violations: int = 0
    cross_tenant_leakage: int = 0
    latency_p95_ms: float = 0.0
    token_total: int = 0
    error_count: int = 0


class ReleaseGates:
    """Staged evaluation gate checker.

    Configuration thresholds:
    - target_improvement_min: 0.20 (20% improvement)
    - fixed_regression_max: 0.02 (2% max regression)
    - max_fact_errors: 0
    - max_fabrication_increase: 0
    - max_safety_violations: 0
    - max_cross_tenant_leakage: 0
    """

    def __init__(
        self,
        target_improvement_min: float = 0.20,
        fixed_regression_max: float = 0.02,
        max_fact_errors: int = 0,
        max_fabrication_increase: int = 0,
        max_safety_violations: int = 0,
        max_cross_tenant_leakage: int = 0,
        max_latency_p95_ms: float = 30_000,
        max_token_total: int = 500_000,
    ) -> None:
        self.target_improvement_min = target_improvement_min
        self.fixed_regression_max = fixed_regression_max
        self.max_fact_errors = max_fact_errors
        self.max_fabrication_increase = max_fabrication_increase
        self.max_safety_violations = max_safety_violations
        self.max_cross_tenant_leakage = max_cross_tenant_leakage
        self.max_latency_p95_ms = max_latency_p95_ms
        self.max_token_total = max_token_total

    def evaluate(self, metrics: dict) -> GateResult:
        """Evaluate a candidate against all release gates.

        Args:
            metrics: Dict with keys:
                - target_improvement (float)
                - fixed_regression (float)
                - fact_errors (int)
                - fabrication_count (int)
                - safety_violations (int)
                - cross_tenant_leakage (int)
                - latency_p95_ms (float)
                - token_total (int)
                - error_count (int)
                - comparisons (list[EvalComparison])

        Returns:
            GateResult with allowed, hard_failures, and soft_warnings.
        """
        result = GateResult()

        # Target improvement (soft)
        target_improvement = metrics.get("target_improvement", 0.0)
        result.target_improvement = target_improvement
        if target_improvement < self.target_improvement_min:
            result.soft_warnings.append(
                f"Target improvement {target_improvement:.1%} < {self.target_improvement_min:.1%}"
            )

        # Fixed regression (soft)
        fixed_regression = metrics.get("fixed_regression", 0.0)
        result.fixed_regression = fixed_regression
        if fixed_regression > self.fixed_regression_max:
            result.soft_warnings.append(
                f"Fixed suite regression {fixed_regression:.1%} > {self.fixed_regression_max:.1%}"
            )

        # Hard gates — any failure blocks release
        result.fact_errors = metrics.get("fact_errors", 0)
        if result.fact_errors > self.max_fact_errors:
            result.hard_failures.append(
                f"Critical fact errors: {result.fact_errors}"
            )

        result.fabrication_count = metrics.get("fabrication_count", 0)
        if result.fabrication_count > self.max_fabrication_increase:
            result.hard_failures.append(
                f"Fabrication count increased: {result.fabrication_count}"
            )

        result.safety_violations = metrics.get("safety_violations", 0)
        if result.safety_violations > self.max_safety_violations:
            result.hard_failures.append(
                f"Safety violations: {result.safety_violations}"
            )

        result.cross_tenant_leakage = metrics.get("cross_tenant_leakage", 0)
        if result.cross_tenant_leakage > self.max_cross_tenant_leakage:
            result.hard_failures.append(
                f"Cross-tenant leakage: {result.cross_tenant_leakage}"
            )

        result.latency_p95_ms = metrics.get("latency_p95_ms", 0.0)
        if result.latency_p95_ms > self.max_latency_p95_ms:
            result.soft_warnings.append(
                f"Latency p95 {result.latency_p95_ms:.0f}ms > {self.max_latency_p95_ms:.0f}ms"
            )

        result.token_total = metrics.get("token_total", 0)
        if result.token_total > self.max_token_total:
            result.soft_warnings.append(
                f"Token total {result.token_total} > {self.max_token_total}"
            )

        result.error_count = metrics.get("error_count", 0)

        result.allowed = len(result.hard_failures) == 0

        return result
