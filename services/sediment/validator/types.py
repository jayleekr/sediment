"""Core types for validator output."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

Severity = Literal["blocker", "major", "minor"]
SEVERITY_WEIGHT: dict[str, int] = {"blocker": 10, "major": 3, "minor": 1}


@dataclass
class CheckResult:
    id: str
    title: str
    layer: str
    severity: Severity
    passed: bool
    actual: Any = None
    expected: Any = None
    message: str = ""
    duration_ms: int = 0
    artifacts: list[str] = field(default_factory=list)  # screenshot paths, etc.

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHT[self.severity]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PhaseReport:
    phase_id: str
    phase_name: str
    iteration: int = 1
    timestamp: str = ""
    results: list[CheckResult] = field(default_factory=list)
    elapsed_s: float = 0.0
    cost_usd: float = 0.0

    # ── derived metrics ─────────────────────────────────────────
    @property
    def score(self) -> int:
        return sum(r.weight for r in self.results if r.passed)

    @property
    def max_score(self) -> int:
        return sum(r.weight for r in self.results)

    @property
    def score_pct(self) -> float:
        return (self.score / self.max_score * 100) if self.max_score else 0.0

    @property
    def blockers_total(self) -> int:
        return sum(1 for r in self.results if r.severity == "blocker")

    @property
    def blockers_passed(self) -> int:
        return sum(1 for r in self.results if r.severity == "blocker" and r.passed)

    @property
    def all_blockers_passed(self) -> bool:
        return self.blockers_total == self.blockers_passed

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def layer_pct(self, layer: str) -> float:
        layer_results = [r for r in self.results if r.layer == layer]
        if not layer_results:
            return 100.0
        s = sum(r.weight for r in layer_results if r.passed)
        m = sum(r.weight for r in layer_results)
        return (s / m * 100) if m else 0.0

    def passes(self, blockers_pct: float = 100, total_pct: float = 90, layer_pct: float = 80) -> bool:
        if not self.all_blockers_passed:
            return False
        if self.score_pct < total_pct:
            return False
        layers = {r.layer for r in self.results}
        for L in layers:
            if self.layer_pct(L) < layer_pct:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "phase_id": self.phase_id,
            "phase_name": self.phase_name,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "elapsed_s": self.elapsed_s,
            "cost_usd": self.cost_usd,
            "score": self.score,
            "max_score": self.max_score,
            "score_pct": round(self.score_pct, 1),
            "blockers_passed": self.blockers_passed,
            "blockers_total": self.blockers_total,
            "all_blockers_passed": self.all_blockers_passed,
            "passed": self.passes(),
            "results": [r.to_dict() for r in self.results],
        }
