"""Re-running over a changed corpus, without losing what the graph used to say."""

from __future__ import annotations

import unittest

from evidence_graph.adjudicate import adjudicate
from evidence_graph.maintain import (
    EdgeStatus,
    changelog,
    contested_edges,
    evidence_fingerprint,
    live_edges,
    reconcile,
)

T1, T2, T3 = "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"


def _edge(etype="contradicts", *, quantity="ph", quote_a="Lowest aggregation at pH 5.5.",
          trigger_a="ph=5.5", source="a", target="b"):
    return {
        "source": source, "target": target, "type": etype, "quantity": quantity,
        "edge_class": "typed", "method": "regex",
        "evidence": [
            {"artifact_id": source, "chunk_id": "c1", "source_uri": "u1",
             "quote": quote_a, "trigger": trigger_a},
            {"artifact_id": target, "chunk_id": "c2", "source_uri": "u2",
             "quote": "Lowest aggregation at pH 7.0.", "trigger": "ph=7.0"},
        ],
    }


def _run(history, edges, at):
    return reconcile(history, adjudicate(edges), at=at)


class FingerprintTests(unittest.TestCase):
    def test_same_evidence_in_any_order_fingerprints_alike(self):
        edge = _edge()
        shuffled = {**edge, "evidence": list(reversed(edge["evidence"]))}
        self.assertEqual(evidence_fingerprint(edge), evidence_fingerprint(shuffled))

    def test_changed_quote_changes_the_fingerprint(self):
        self.assertNotEqual(
            evidence_fingerprint(_edge()),
            evidence_fingerprint(_edge(quote_a="Aggregation minimised at pH 5.5.")),
        )


class ReconcileTests(unittest.TestCase):
    def test_first_run_marks_everything_new(self):
        history = _run(None, [_edge()], T1)
        [h] = history.values()
        self.assertEqual(h.status, EdgeStatus.NEW)
        self.assertEqual(h.current.revision, 1)
        self.assertTrue(h.live)

    def test_identical_rerun_is_unchanged_and_does_not_lose_history(self):
        history = _run(_run(None, [_edge()], T1), [_edge()], T2)
        [h] = history.values()
        self.assertEqual(h.status, EdgeStatus.UNCHANGED)
        self.assertEqual(len(h.revisions), 2)
        self.assertEqual([r.at for r in h.revisions], [T1, T2])

    def test_same_conclusion_on_new_passages_is_restated(self):
        first = _run(None, [_edge()], T1)
        second = _run(first, [_edge(quote_a="Aggregation was lowest at pH 5.5 in triplicate.")], T2)
        [h] = second.values()
        self.assertEqual(h.status, EdgeStatus.RESTATED)
        self.assertTrue(h.live)

    def test_losing_support_weakens_rather_than_deletes(self):
        first = _run(None, [_edge()], T1)
        # The passage no longer carries the value the proposer claims fired.
        second = _run(first, [_edge(quote_a="The buffer was prepared fresh.")], T2)
        [h] = second.values()
        self.assertEqual(h.status, EdgeStatus.WEAKENED)
        self.assertFalse(h.live)
        self.assertIn("no longer reaches", h.current.reason)
        # The record of having been asserted survives.
        self.assertEqual(h.revisions[0].status, EdgeStatus.NEW)

    def test_regaining_support_reinstates(self):
        first = _run(None, [_edge()], T1)
        second = _run(first, [_edge(quote_a="Unrelated.")], T2)
        third = _run(second, [_edge()], T3)
        [h] = third.values()
        self.assertEqual(h.status, EdgeStatus.REINSTATED)
        self.assertTrue(h.live)
        self.assertEqual(len(h.revisions), 3)

    def test_new_contradicting_evidence_contests_rather_than_overwrites(self):
        # The headline maintenance case: an asserted edge meets a contradicting one in a later run.
        first = _run(None, [_edge("supports")], T1)
        second = _run(first, [_edge("supports"), _edge("contradicts")], T2)
        supports = second["a|supports|b|ph"]
        self.assertEqual(supports.status, EdgeStatus.CONTRADICTED)
        self.assertIn("disagrees with itself", supports.current.reason)
        self.assertEqual(supports.current.conflicts_with, ("a|contradicts|b|ph",))
        # Both sides are kept.
        self.assertEqual(len(contested_edges(second)), 2)
        self.assertNotIn("a|supports|b|ph", [e["source"] for e in live_edges(second)])

    def test_vanished_candidate_is_withdrawn_not_deleted(self):
        first = _run(None, [_edge(), _edge(quantity="temperature")], T1)
        second = _run(first, [_edge()], T2)
        gone = second["a|contradicts|b|temperature"]
        self.assertEqual(gone.status, EdgeStatus.WITHDRAWN)
        self.assertFalse(gone.live)
        self.assertEqual(gone.current.reason, "no longer proposed by the corpus")

    def test_withdrawal_is_recorded_once(self):
        first = _run(None, [_edge()], T1)
        second = _run(first, [], T2)
        third = _run(second, [], T3)
        [h] = third.values()
        self.assertEqual(len([r for r in h.revisions if r.status is EdgeStatus.WITHDRAWN]), 1)

    def test_reconcile_does_not_mutate_the_input_history(self):
        first = _run(None, [_edge()], T1)
        snapshot = {k: len(v.revisions) for k, v in first.items()}
        _run(first, [_edge(quote_a="Unrelated.")], T2)
        self.assertEqual({k: len(v.revisions) for k, v in first.items()}, snapshot)


class ViewTests(unittest.TestCase):
    def test_live_edges_is_the_current_graph(self):
        history = _run(_run(None, [_edge(), _edge(quantity="temperature")], T1),
                       [_edge(), _edge(quantity="temperature", quote_a="Unrelated.")], T2)
        self.assertEqual([e["quantity"] for e in live_edges(history)], ["ph"])

    def test_changelog_orders_by_time_not_per_edge_revision_number(self):
        # An edge first seen in a later run is at revision 1, so ordering by revision number would
        # bury the newest change under long-lived edges.
        first = _run(None, [_edge()], T1)
        second = _run(first, [_edge(quote_a="Unrelated."), _edge(quantity="temperature")], T2)
        entries = changelog(second)
        self.assertEqual({e["at"] for e in entries[:2]}, {T2})
        self.assertEqual(entries[-1]["at"], T1)
        self.assertNotIn("unchanged", [e["status"] for e in entries])

    def test_changelog_can_report_only_what_happened_after_a_given_run(self):
        first = _run(None, [_edge()], T1)
        second = _run(first, [_edge(), _edge(quantity="temperature")], T2)
        self.assertEqual([e["quantity"] for e in changelog(second, since=T1)], ["temperature"])

    def test_history_serialises(self):
        [h] = _run(None, [_edge()], T1).values()
        data = h.as_dict()
        self.assertEqual(data["status"], "new")
        self.assertTrue(data["live"])
        self.assertEqual(len(data["revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
