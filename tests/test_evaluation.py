"""Risk-coverage measurement over adjudicated edges."""

from __future__ import annotations

import unittest
import xml.dom.minidom as minidom

from evidence_graph.adjudicate import EdgeScore, edge_key
from evidence_graph.evaluation import (
    baseline_precision,
    risk_coverage,
    risk_coverage_svg,
)


def _edge(quantity, *, grounded=True, source="a", target="b"):
    quote_a = "Lowest aggregation was observed at pH 5.5." if grounded else "Unrelated sentence."
    return {
        "source": source, "target": target, "type": "contradicts", "quantity": quantity,
        "edge_class": "typed", "method": "regex",
        "evidence": [
            {"artifact_id": source, "chunk_id": "c1", "source_uri": "u1",
             "quote": quote_a, "trigger": "ph=5.5"},
            {"artifact_id": target, "chunk_id": "c2", "source_uri": "u2",
             "quote": "Lowest aggregation was observed at pH 7.0.", "trigger": "ph=7.0"},
        ],
    }


class FixedScorer:
    """Scores by a lookup, so a test can place edges at chosen points on the curve."""

    def __init__(self, scores):
        self.scores = scores

    def __call__(self, edge):
        return EdgeScore(self.scores[edge["quantity"]], "fixed")


class BaselineTests(unittest.TestCase):
    def test_baseline_is_precision_with_everything_asserted(self):
        edges = [_edge("ph"), _edge("temp"), _edge("conc")]
        gold = {edge_key(edges[0]): True, edge_key(edges[1]): False, edge_key(edges[2]): True}
        self.assertAlmostEqual(baseline_precision(edges, gold), 2 / 3)

    def test_unlabelled_edges_do_not_count_toward_the_baseline(self):
        edges = [_edge("ph"), _edge("temp")]
        self.assertEqual(baseline_precision(edges, {edge_key(edges[0]): True}), 1.0)


class RiskCoverageTests(unittest.TestCase):
    def setUp(self):
        # Three good edges scoring high, two wrong ones scoring low: exactly the situation
        # abstention is supposed to exploit.
        self.edges = [_edge(q) for q in ("ph", "temp", "conc", "mass", "vol")]
        self.gold = {
            edge_key(self.edges[0]): True,
            edge_key(self.edges[1]): True,
            edge_key(self.edges[2]): True,
            edge_key(self.edges[3]): False,
            edge_key(self.edges[4]): False,
        }
        self.scorer = FixedScorer({"ph": 9.0, "temp": 8.0, "conc": 7.0, "mass": 3.0, "vol": 2.0})

    def test_raising_the_threshold_trades_coverage_for_precision(self):
        report = risk_coverage(self.edges, self.gold, scorer=self.scorer, thresholds=[0.0, 5.0])
        wide, strict = report.points
        self.assertEqual(wide.coverage, 1.0)
        self.assertAlmostEqual(wide.precision, 0.6)
        self.assertEqual(strict.coverage, 0.6)
        self.assertEqual(strict.precision, 1.0)
        self.assertEqual(strict.false_assertions, 0)

    def test_the_baseline_equals_the_zero_threshold_point(self):
        report = risk_coverage(self.edges, self.gold, scorer=self.scorer, thresholds=[0.0])
        self.assertAlmostEqual(report.baseline_precision, report.points[0].precision)

    def test_correct_edges_lost_to_caution_are_counted(self):
        report = risk_coverage(self.edges, self.gold, scorer=self.scorer, thresholds=[8.5])
        point = report.points[0]
        self.assertEqual(point.asserted, 1)
        self.assertEqual(point.missed, 2)  # temp and conc were right but abstained on

    def test_best_at_precision_answers_the_operator_question(self):
        report = risk_coverage(self.edges, self.gold, scorer=self.scorer)
        point = report.best_at_precision(1.0)
        self.assertIsNotNone(point)
        self.assertEqual(point.precision, 1.0)
        self.assertAlmostEqual(point.coverage, 0.6)  # the most we can keep at perfect precision

    def test_best_at_precision_returns_none_when_unreachable(self):
        report = risk_coverage(self.edges, {edge_key(e): False for e in self.edges},
                               scorer=self.scorer)
        self.assertIsNone(report.best_at_precision(0.9))

    def test_unlabelled_assertions_are_reported_not_silently_scored(self):
        report = risk_coverage(self.edges, {edge_key(self.edges[0]): True},
                               scorer=self.scorer, thresholds=[0.0])
        self.assertEqual(report.points[0].unlabelled, 4)
        self.assertEqual(report.points[0].precision, 1.0)
        self.assertEqual(report.unlabelled, 4)

    def test_conflicted_edges_are_excluded_from_precision(self):
        supports = _edge("ph")
        supports["type"] = "supports"
        edges = [*self.edges, supports]
        gold = {**self.gold, edge_key(supports): False}
        report = risk_coverage(edges, gold, scorer=FixedScorer(
            {"ph": 9.0, "temp": 8.0, "conc": 7.0, "mass": 3.0, "vol": 2.0}), thresholds=[0.0])
        point = report.points[0]
        self.assertEqual(point.conflicted, 2)  # the supports/contradicts pair on ph
        self.assertEqual(point.correct + point.false_assertions, 4)

    def test_report_serialises(self):
        report = risk_coverage(self.edges, self.gold, scorer=self.scorer, thresholds=[0.0, 5.0])
        data = report.as_dict()
        self.assertEqual(len(data["points"]), 2)
        self.assertIn("baseline_precision_no_abstention", data)


class SvgTests(unittest.TestCase):
    def test_curve_renders_as_well_formed_svg(self):
        edges = [_edge(q) for q in ("ph", "temp")]
        gold = {edge_key(edges[0]): True, edge_key(edges[1]): False}
        svg = risk_coverage_svg(risk_coverage(edges, gold, scorer=FixedScorer({"ph": 9.0, "temp": 2.0})))
        minidom.parseString(svg)
        self.assertIn("no abstention", svg)
        self.assertIn("precision of asserted edges", svg)


if __name__ == "__main__":
    unittest.main()
