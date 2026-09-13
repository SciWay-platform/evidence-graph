"""The assert / abstain / conflict gate.

The tests that matter here are the negative ones. A gate that asserts everything passes any test
asking "did it assert the good edge"; what has to be demonstrated is that it declines the edges a
proposer got wrong, and that declining is recorded rather than silent.
"""

from __future__ import annotations

import unittest

from evidence_graph.adjudicate import (
    DEFAULT_ASSERT_AT,
    Adjudication,
    EdgeScore,
    GroundingScorer,
    Verdict,
    adjudicate,
    edge_key,
    open_questions,
)


def _edge(etype="contradicts", *, source="a", target="b", quantity="ph", evidence=None):
    if evidence is None:
        evidence = [
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1", "location": "p.1",
             "quote": "Lowest aggregation was observed at pH 5.5.", "trigger": "ph=5.5"},
            {"artifact_id": "b", "chunk_id": "c2", "source_uri": "u2", "location": "p.2",
             "quote": "Lowest aggregation was observed at pH 7.0.", "trigger": "ph=7.0"},
        ]
    return {"source": source, "target": target, "type": etype, "edge_class": "typed",
            "quantity": quantity, "evidence": evidence, "method": "regex"}


class GroundingScorerTests(unittest.TestCase):
    def test_well_grounded_edge_scores_full_marks(self):
        score = GroundingScorer()(_edge())
        self.assertEqual(score.score, 10.0)
        self.assertTrue(all(score.checks.values()))

    def test_edge_with_no_evidence_scores_zero(self):
        score = GroundingScorer()(_edge(evidence=[]))
        self.assertEqual(score.score, 0.0)
        self.assertIn("no evidence", score.rationale)

    def test_trigger_absent_from_its_own_quote_is_caught(self):
        # The defect this check exists for: the proposer says pH 5.5 fired, but the passage it cites
        # says nothing of the sort. The edge would be uncitable.
        broken = _edge()
        broken["evidence"][0]["quote"] = "The buffer was prepared fresh each morning."
        score = GroundingScorer()(broken)
        self.assertFalse(score.checks["trigger_verbatim"])
        self.assertIn("ph=5.5", score.rationale)

    def test_missing_quote_or_locator_is_caught(self):
        broken = _edge()
        broken["evidence"][1]["quote"] = ""
        self.assertFalse(GroundingScorer()(broken).checks["evidence_complete"])

    def test_one_sided_edge_types_are_not_asked_for_two_sided_evidence(self):
        # `decided` records what ONE document decided; there is no second passage to cite. Demanding
        # one penalised a correctly formed edge (4433 abstentions on the first real corpus).
        decided = _edge("decided", evidence=[
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1",
             "quote": "We selected pH 7.0 as the baseline.", "trigger": "decision on ph"},
        ])
        score = GroundingScorer()(decided)
        self.assertNotIn("both_endpoints", score.checks)
        self.assertGreaterEqual(score.score, DEFAULT_ASSERT_AT)

    def test_comparing_edge_citing_only_one_endpoint_is_penalised(self):
        one_sided = _edge(evidence=[
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1",
             "quote": "Lowest aggregation at pH 5.5.", "trigger": "ph=5.5"},
        ])
        score = GroundingScorer()(one_sided)
        self.assertFalse(score.checks["both_endpoints"])
        self.assertIn("b", score.rationale)

    def test_type_cue_is_required_where_the_type_implies_one(self):
        no_cue = _edge("decided", evidence=[
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1",
             "quote": "The buffer was prepared fresh.", "trigger": ""},
            {"artifact_id": "b", "chunk_id": "c2", "source_uri": "u2",
             "quote": "Aggregation was measured by SEC.", "trigger": ""},
        ])
        self.assertFalse(GroundingScorer()(no_cue).checks["type_cue_present"])

        with_cue = _edge("decided", evidence=[
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1",
             "quote": "We selected pH 7.0 as the baseline.", "trigger": ""},
            {"artifact_id": "b", "chunk_id": "c2", "source_uri": "u2",
             "quote": "Baseline confirmed.", "trigger": ""},
        ])
        self.assertTrue(GroundingScorer()(with_cue).checks["type_cue_present"])


class AdjudicationTests(unittest.TestCase):
    def test_grounded_edge_is_asserted(self):
        result = adjudicate([_edge()])
        self.assertEqual(result.asserted[0].verdict, Verdict.ASSERT)
        self.assertEqual(result.coverage, 1.0)

    def test_ungrounded_edge_abstains_and_is_not_discarded(self):
        result = adjudicate([_edge(evidence=[])])
        self.assertEqual(len(result.asserted), 0)
        self.assertEqual(len(result.abstained), 1)
        # The point: the candidate survives, with its evidence, for review.
        self.assertEqual(result.abstained[0].edge["source"], "a")

    def test_abstention_rationale_names_the_threshold(self):
        broken = _edge()
        broken["evidence"][0]["quote"] = "Unrelated sentence."
        result = adjudicate([broken])
        self.assertEqual(result.abstained[0].verdict, Verdict.ABSTAIN)
        self.assertIn(str(DEFAULT_ASSERT_AT), result.abstained[0].rationale)

    def test_incompatible_candidates_become_a_conflict_not_a_winner(self):
        # Two proposers (or one, on different passages) disagree about the same pair. Resolving that
        # by score would silently discard half the evidence.
        result = adjudicate([_edge("supports"), _edge("contradicts")])
        self.assertEqual(len(result.conflicted), 2)
        self.assertEqual(len(result.asserted), 0)
        self.assertEqual(
            set(result.conflicted[0].conflicts_with),
            {edge_key(_edge("contradicts"))},
        )

    def test_conflict_is_scoped_to_the_same_pair_and_quantity(self):
        # A supports edge about pH and a contradicts edge about temperature are not in conflict.
        result = adjudicate([_edge("supports", quantity="ph"),
                             _edge("contradicts", quantity="temperature")])
        self.assertEqual(len(result.conflicted), 0)
        self.assertEqual(len(result.asserted), 2)

    def test_conflict_ignores_edge_direction(self):
        result = adjudicate([_edge("supports", source="a", target="b"),
                             _edge("contradicts", source="b", target="a")])
        self.assertEqual(len(result.conflicted), 2)

    def test_threshold_is_the_abstention_dial(self):
        edges = [_edge()]
        self.assertEqual(adjudicate(edges, assert_at=9.0).coverage, 1.0)
        self.assertEqual(adjudicate(edges, assert_at=10.5).coverage, 0.0)

    def test_caller_supplied_scorer_is_used(self):
        class AlwaysZero:
            def __call__(self, edge):
                return EdgeScore(0.0, "a model said no")

        result = adjudicate([_edge()], scorer=AlwaysZero())
        self.assertEqual(len(result.abstained), 1)
        self.assertIn("a model said no", result.abstained[0].rationale)

    def test_input_order_is_preserved_for_reproducibility(self):
        edges = [_edge(quantity=q) for q in ("ph", "temperature", "concentration")]
        result = adjudicate(edges)
        self.assertEqual([a.edge["quantity"] for a in result.adjudications],
                         ["ph", "temperature", "concentration"])

    def test_summary_counts_every_candidate(self):
        result = adjudicate([_edge(), _edge(evidence=[]), _edge("supports", quantity="t"),
                             _edge("contradicts", quantity="t")])
        s = result.summary()
        self.assertEqual(s["candidates"], 4)
        self.assertEqual(s["asserted"] + s["abstained"] + s["conflicted"], 4)


class OpenQuestionTests(unittest.TestCase):
    def test_queue_carries_the_evidence_and_the_reason(self):
        broken = _edge()
        broken["evidence"][0]["quote"] = "Unrelated."
        [item] = open_questions(adjudicate([broken]))
        self.assertEqual(item["type"], "contradicts")
        self.assertIn("ph=5.5", item["why_not_asserted"])
        self.assertEqual(len(item["evidence"]), 2)

    def test_asserted_edges_are_not_in_the_queue(self):
        self.assertEqual(open_questions(adjudicate([_edge()])), [])


class AdjudicationShapeTests(unittest.TestCase):
    def test_key_identifies_an_edge_across_runs(self):
        a = Adjudication(edge=_edge(), verdict=Verdict.ASSERT, score=10.0, rationale="")
        self.assertEqual(a.key, "a|contradicts|b|ph")
        self.assertTrue(a.asserted)

    def test_verdict_serialises_as_a_plain_string(self):
        self.assertEqual(f"{Verdict.ABSTAIN}", "abstain")


if __name__ == "__main__":
    unittest.main()


class TriggerSemanticsTests(unittest.TestCase):
    """Regression tests for the overloaded `trigger` field.

    On the first real corpus (32 PMC articles, 18k candidate edges) the scorer abstained on 88% of
    candidates and blamed an unverifiable trigger in 100% of those cases. Two faults: descriptive
    notes like "decision on ph" were treated as claims about the quote, and the normalised label
    (`concentration_molar`) was required to appear in prose, which it never does.
    """

    def _score(self, trigger, quote, *, other_trigger="ph=7.0"):
        edge = _edge(evidence=[
            {"artifact_id": "a", "chunk_id": "c1", "source_uri": "u1", "quote": quote,
             "trigger": trigger},
            {"artifact_id": "b", "chunk_id": "c2", "source_uri": "u2",
             "quote": "Lowest aggregation was observed at pH 7.0.", "trigger": other_trigger},
        ])
        return GroundingScorer()(edge)

    def test_measurement_trigger_requires_only_the_value(self):
        # The label is normalised and never appears in the text; requiring it failed every edge.
        score = self._score("concentration_molar=25 mM", "Arginine was added at 25 mM.")
        self.assertTrue(score.checks["trigger_verbatim"])

    def test_measurement_trigger_still_catches_a_value_that_is_absent(self):
        score = self._score("ph=5.5", "The buffer was prepared fresh each morning.")
        self.assertFalse(score.checks["trigger_verbatim"])

    def test_descriptive_notes_are_not_treated_as_claims(self):
        for note in ("decision on ph", "protocol reference", "supports", "contradicts"):
            with self.subTest(note=note):
                score = self._score(note, "We selected pH 7.0 as the baseline.",
                                    other_trigger=note)
                self.assertNotIn("trigger_verbatim", score.checks,
                                 f"{note!r} is a note, so there is nothing to verify")

    def test_a_checkable_trigger_is_still_checked_alongside_notes(self):
        # A mixed edge must not get a free pass just because one of its triggers is a note.
        score = self._score("decision on ph", "Unrelated sentence.", other_trigger="ph=9.9")
        self.assertFalse(score.checks["trigger_verbatim"])

    def test_entity_triggers_are_checkable(self):
        self.assertTrue(self._score("mentions mAb-X", "mAb-X was formulated at pH 6.0.")
                        .checks["trigger_verbatim"])
        self.assertFalse(self._score("supports re: mAb-X", "The buffer was prepared fresh.")
                         .checks["trigger_verbatim"])

    def test_an_edge_of_notes_only_is_judged_on_its_other_checks(self):
        # Previously such an edge scored 0 on a check it could never pass.
        score = self._score("decision on ph", "We selected pH 7.0 as the baseline.",
                            other_trigger="decision on ph")
        self.assertNotIn("trigger_verbatim", score.checks)
        self.assertGreaterEqual(score.score, 6.0)
