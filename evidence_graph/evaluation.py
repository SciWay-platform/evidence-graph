"""Edge-level precision as a function of abstention — the number this library exists to move.

Knowledge-graph construction is almost always reported as recall or raw triple count. Both reward a
system for asserting more, which is the wrong incentive when a wrong edge costs a researcher more
than a missing one. The measurement here is the trade instead of one side of it:

    coverage   share of candidate edges the system was willing to assert
    precision  share of those assertions that were actually correct

Sweeping the assertion threshold traces a **risk-coverage curve**. A system with no abstention
mechanism is a single point on it, at coverage 1.0 — `baseline_precision` computes exactly that
point, so the comparison the curve is making is explicit rather than implied.

The gold standard is a mapping from `adjudicate.edge_key` to whether the edge is correct. Building
one is manual work and that is unavoidable: nobody can tell you whether "exp-078 contradicts exp-085
about pH" is right except someone who reads both sources.

Everything here is pure and stdlib. `risk_coverage_svg` renders the curve without a plotting
library so a result can be committed next to the code that produced it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evidence_graph.adjudicate import (
    DEFAULT_SCORER,
    EdgeScorer,
    Verdict,
    adjudicate,
    edge_key,
)

#: Thresholds swept by default: the full 0-10 scorer scale in half-point steps.
DEFAULT_THRESHOLDS: tuple[float, ...] = tuple(i / 2 for i in range(0, 23))


@dataclass(frozen=True)
class CurvePoint:
    """One threshold's worth of the risk-coverage trade."""

    threshold: float
    candidates: int
    asserted: int
    abstained: int
    conflicted: int
    correct: int
    #: Asserted edges the gold standard says are wrong. The quantity a lab actually cares about.
    false_assertions: int
    #: Edges that were abstained on but were in fact correct — the cost of caution.
    missed: int
    #: Asserted edges with no gold label, excluded from precision. Reported so a curve computed on a
    #: partially labelled set cannot be mistaken for a complete one.
    unlabelled: int

    @property
    def coverage(self) -> float:
        return self.asserted / self.candidates if self.candidates else 0.0

    @property
    def precision(self) -> float:
        """Share of *labelled* assertions that are correct. Undefined (0.0) when nothing asserted."""
        judged = self.correct + self.false_assertions
        return self.correct / judged if judged else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "candidates": self.candidates,
            "asserted": self.asserted,
            "abstained": self.abstained,
            "conflicted": self.conflicted,
            "correct": self.correct,
            "false_assertions": self.false_assertions,
            "missed": self.missed,
            "unlabelled": self.unlabelled,
            "coverage": round(self.coverage, 4),
            "precision": round(self.precision, 4),
        }


@dataclass(frozen=True)
class EvaluationReport:
    """A swept curve plus the no-abstention baseline it should be read against."""

    points: tuple[CurvePoint, ...]
    baseline_precision: float
    labelled: int
    unlabelled: int

    def best_at_precision(self, target: float) -> CurvePoint | None:
        """The highest-coverage point that still reaches `target` precision.

        This is the question an operator actually asks: "if I need 95% of asserted edges to be
        right, how much of the corpus can I keep?"
        """
        eligible = [p for p in self.points if p.asserted and p.precision >= target]
        return max(eligible, key=lambda p: (p.coverage, -p.threshold)) if eligible else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_precision_no_abstention": round(self.baseline_precision, 4),
            "labelled_candidates": self.labelled,
            "unlabelled_candidates": self.unlabelled,
            "points": [p.as_dict() for p in self.points],
        }


def baseline_precision(edges: Sequence[dict[str, Any]], gold: Mapping[str, bool]) -> float:
    """Precision if every candidate is asserted — a system with no abstention mechanism."""
    labelled = [gold[edge_key(e)] for e in edges if edge_key(e) in gold]
    return sum(labelled) / len(labelled) if labelled else 0.0


def risk_coverage(
    edges: Sequence[dict[str, Any]],
    gold: Mapping[str, bool],
    *,
    scorer: EdgeScorer | None = None,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> EvaluationReport:
    """Sweep the assertion threshold and measure coverage against precision at each step.

    Conflicted edges are counted but excluded from precision: the adjudicator did not assert them,
    and scoring a recorded disagreement as a right or wrong assertion would misrepresent what the
    system did.
    """
    scorer = scorer or DEFAULT_SCORER
    points: list[CurvePoint] = []

    for threshold in thresholds:
        result = adjudicate(list(edges), scorer=scorer, assert_at=threshold)
        correct = false = unlabelled = missed = 0
        for a in result.adjudications:
            label = gold.get(a.key)
            if a.verdict is Verdict.ASSERT:
                if label is None:
                    unlabelled += 1
                elif label:
                    correct += 1
                else:
                    false += 1
            elif a.verdict is Verdict.ABSTAIN and label:
                missed += 1
        points.append(
            CurvePoint(
                threshold=float(threshold),
                candidates=len(result.adjudications),
                asserted=len(result.asserted),
                abstained=len(result.abstained),
                conflicted=len(result.conflicted),
                correct=correct,
                false_assertions=false,
                missed=missed,
                unlabelled=unlabelled,
            )
        )

    keys = [edge_key(e) for e in edges]
    return EvaluationReport(
        points=tuple(points),
        baseline_precision=baseline_precision(edges, gold),
        labelled=sum(1 for k in keys if k in gold),
        unlabelled=sum(1 for k in keys if k not in gold),
    )


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def risk_coverage_svg(report: EvaluationReport, *, width: int = 520, height: int = 360) -> str:
    """Render the curve as a standalone SVG (no plotting library).

    Coverage on x, precision on y, with the no-abstention baseline drawn as a horizontal rule. The
    distance between the curve and that rule is the whole argument for abstention, so the chart is
    laid out to make it the first thing visible.
    """
    pad_l, pad_r, pad_t, pad_b = 56, 18, 22, 46
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    def x(coverage: float) -> float:
        return pad_l + coverage * plot_w

    def y(precision: float) -> float:
        return pad_t + (1.0 - precision) * plot_h

    drawn = [p for p in report.points if p.asserted]
    drawn.sort(key=lambda p: p.coverage)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(p.coverage):.1f},{y(p.precision):.1f}"
                    for i, p in enumerate(drawn))
    dots = "".join(
        f'<circle cx="{x(p.coverage):.1f}" cy="{y(p.precision):.1f}" r="3.2" fill="#1f6feb">'
        f"<title>threshold {p.threshold}: coverage {p.coverage:.2f}, precision {p.precision:.2f}"
        f" ({p.correct} correct, {p.false_assertions} wrong)</title></circle>"
        for p in drawn
    )
    grid = "".join(
        f'<line x1="{pad_l}" y1="{y(v):.1f}" x2="{pad_l + plot_w}" y2="{y(v):.1f}" '
        f'stroke="#d8dee4" stroke-width="1"/>'
        f'<text x="{pad_l - 8}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="10" '
        f'fill="#57606a">{v:.1f}</text>'
        for v in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    ticks = "".join(
        f'<text x="{x(v):.1f}" y="{pad_t + plot_h + 16}" text-anchor="middle" font-size="10" '
        f'fill="#57606a">{v:.2f}</text>'
        for v in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    base_y = y(report.baseline_precision)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">'
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        f"{grid}{ticks}"
        f'<line x1="{pad_l}" y1="{base_y:.1f}" x2="{pad_l + plot_w}" y2="{base_y:.1f}" '
        f'stroke="#cf222e" stroke-width="1.5" stroke-dasharray="5 4"/>'
        f'<text x="{pad_l + plot_w}" y="{base_y - 6:.1f}" text-anchor="end" font-size="10" '
        f'fill="#cf222e">no abstention: {report.baseline_precision:.2f}</text>'
        f'<path d="{path}" fill="none" stroke="#1f6feb" stroke-width="2"/>{dots}'
        f'<text x="{pad_l + plot_w / 2:.0f}" y="{height - 8}" text-anchor="middle" font-size="11" '
        f'fill="#24292f">coverage (share of candidate edges asserted)</text>'
        f'<text x="14" y="{pad_t + plot_h / 2:.0f}" font-size="11" fill="#24292f" '
        f'transform="rotate(-90 14 {pad_t + plot_h / 2:.0f})" text-anchor="middle">'
        f"precision of asserted edges</text>"
        f"</svg>"
    )
