"""The whole spine in one call."""

from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

from evidence_graph.grounding import load_vocabulary
from evidence_graph.kgx import validate_kgx
from evidence_graph.maintain import EdgeStatus
from evidence_graph.pipeline import MAX_TERM_WORDS, build, candidate_terms

T1, T2 = "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"

ARTIFACTS = [
    {"id": "A1", "title": "Buffer screen", "artifact_type": "paper", "source_system": "test",
     "structured_metadata": {"doi": "10.1000/a"}},
    {"id": "A2", "title": "pH optimisation", "artifact_type": "paper", "source_system": "test",
     "structured_metadata": {"doi": "10.1000/b"}},
]


def _chunks(second_value: str = "7.0"):
    return [
        {"id": "c1", "artifact_id": "A1", "source_uri": "u1", "page_or_row_reference": "Results, 1",
         "chunk_text": "Aggregation of the antibody was lowest at pH 5.5 in L-histidine buffer."},
        {"id": "c2", "artifact_id": "A2", "source_uri": "u2", "page_or_row_reference": "Results, 2",
         "chunk_text": f"Aggregation of the antibody was lowest at pH {second_value} with sucrose."},
    ]


def _vocabulary(root: Path):
    with gzip.open(root / "chebi.tsv.gz", "wt", encoding="utf-8") as out:
        out.write("CHEBI:15971\tL-histidine\thistidine\t\n")
        out.write("CHEBI:17992\tsucrose\t\t\n")
    with gzip.open(root / "go.tsv.gz", "wt", encoding="utf-8") as out:
        out.write("GO:0042571\timmunoglobulin complex\tantibody\tcellular_component\n")
    load_vocabulary.cache_clear()
    return load_vocabulary(str(root))


class CandidateTermTests(unittest.TestCase):
    def test_phrases_up_to_the_limit_are_offered(self):
        terms = candidate_terms("polysorbate 80 was added")
        self.assertIn("polysorbate", terms)
        # Ontology labels routinely end in a number; a letters-only tokenizer can never build them.
        self.assertIn("polysorbate 80", terms)
        self.assertTrue(max(len(t.split()) for t in terms) <= MAX_TERM_WORDS)

    def test_terms_are_deduplicated_but_keep_order(self):
        terms = candidate_terms("sucrose and sucrose")
        self.assertEqual(terms.count("sucrose"), 1)
        self.assertEqual(terms[0], "sucrose")

    def test_empty_text_yields_nothing(self):
        self.assertEqual(candidate_terms(""), [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vocab = _vocabulary(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()
        load_vocabulary.cache_clear()

    def test_end_to_end_produces_a_valid_graph_and_a_review_queue(self):
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        self.assertEqual(validate_kgx(result.graph), [])
        self.assertGreater(len(result.graph.edges), 0)
        self.assertIsInstance(result.open_questions, list)

    def test_grounded_concepts_become_biolink_native_nodes(self):
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        node_ids = {n["id"] for n in result.graph.nodes}
        self.assertIn("CHEBI:15971", node_ids)
        self.assertIn("CHEBI:17992", node_ids)
        mention_edges = [e for e in result.graph.edges if e["predicate"] == "biolink:mentions"]
        self.assertTrue(mention_edges)
        # These edges map cleanly onto Biolink, unlike the assertion-level relations.
        for edge in mention_edges:
            self.assertEqual(edge["knowledge_level"], "text_co_occurrence")

    def test_concept_nodes_carry_a_category_chain(self):
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        node = next(n for n in result.graph.nodes if n["id"] == "CHEBI:15971")
        self.assertEqual(node["category"][0], "biolink:SmallMolecule")
        self.assertIn("biolink:Entity", node["category"])

    def test_mentions_are_joined_to_documents_by_curie(self):
        # Mentions are keyed by artifact id and nodes by CURIE; a broken join silently produces
        # zero concept edges, which is what happened the first time.
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        subjects = {e["subject"] for e in result.graph.edges if e["predicate"] == "biolink:mentions"}
        self.assertTrue(subjects <= {"doi:10.1000/a", "doi:10.1000/b"})
        self.assertTrue(subjects)

    def test_ambiguous_surfaces_are_not_asserted_as_mentions(self):
        root = Path(self._tmp.name)
        with gzip.open(root / "mondo.tsv.gz", "wt", encoding="utf-8") as out:
            out.write("MONDO:9999999\tsucrose intolerance\tsucrose\t\n")  # collides with CHEBI
        load_vocabulary.cache_clear()
        vocab = load_vocabulary(str(root))
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=vocab)
        objects = {e["object"] for e in result.graph.edges if e["predicate"] == "biolink:mentions"}
        self.assertNotIn("CHEBI:17992", objects)
        self.assertNotIn("MONDO:9999999", objects)

    def test_grounding_can_be_switched_off(self):
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab, ground_concepts=False)
        self.assertEqual(result.mentions, ())
        self.assertEqual(result.grounding.summary()["terms"], 0)

    def test_a_rerun_folds_into_the_previous_history(self):
        first = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        second = build(ARTIFACTS, _chunks(), at=T2, history=first.history, vocabulary=self.vocab)
        statuses = {h.status for h in second.history.values()}
        self.assertIn(EdgeStatus.UNCHANGED, statuses)
        self.assertEqual(second.changelog(since=T1), [])

    def test_a_corpus_that_stops_supporting_an_edge_weakens_it(self):
        first = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        # Both papers now report the same value, so the contradiction disappears.
        second = build(ARTIFACTS, _chunks("5.5"), at=T2, history=first.history,
                       vocabulary=self.vocab)
        contradicts = [h for k, h in second.history.items() if "contradicts" in k]
        self.assertTrue(contradicts)
        self.assertTrue(all(h.status is EdgeStatus.WITHDRAWN for h in contradicts))
        # Withdrawn, not deleted: the record that it was once asserted survives.
        self.assertTrue(all(len(h.revisions) == 2 for h in contradicts))

    def test_abstained_edges_stay_out_of_the_graph_but_in_the_queue(self):
        result = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab)
        exported = {(e["subject"], e["predicate"], e["object"]) for e in result.graph.edges}
        for question in result.open_questions:
            self.assertTrue(question["evidence"], "an open question must carry its evidence")
            self.assertNotIn(question["key"], exported)

    def test_summary_reports_every_stage(self):
        summary = build(ARTIFACTS, _chunks(), at=T1, vocabulary=self.vocab).summary()
        self.assertEqual(set(summary), {"edges", "grounding", "graph", "open_questions"})


if __name__ == "__main__":
    unittest.main()
