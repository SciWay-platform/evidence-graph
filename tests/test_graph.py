"""Module tests for the per-project knowledge graph (scripts/app/graph.py).

The graph is the bring-your-own counterpart to the demo's curated relations file: a deterministic,
stdlib-only co-occurrence concept map built straight from a project's chunks. These tests pin the
contract the /api/projects/<id>/graph endpoint and the workspace UI depend on: document nodes for
every artifact, salient concept nodes, mentions + co-occurrence edges, stopword filtering, honest
stats, graceful empty handling, and byte-for-byte determinism (no randomness in the build).
"""

import unittest

from evidence_graph.graph import build_project_graph

_ARTS = [
    {"id": "a1", "title": "Buffer screen", "artifact_type": "note", "source_system": "paste"},
    {"id": "a2", "title": "pH study", "artifact_type": "paper", "source_system": "upload"},
]
_CHUNKS = [
    {"id": "a1::c1", "artifact_id": "a1",
     "chunk_text": "Histidine buffer at pH 5.5 reduced aggregation of the antibody."},
    {"id": "a1::c2", "artifact_id": "a1",
     "chunk_text": "Aggregation increased at pH 7.0 with citrate buffer."},
    {"id": "a2::c1", "artifact_id": "a2",
     "chunk_text": "The antibody aggregation was lowest in histidine buffer formulations."},
]


class GraphBuildTests(unittest.TestCase):
    def test_documents_and_concepts_are_nodes(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        docs = [n for n in g["nodes"] if n["kind"] == "document"]
        concepts = [n for n in g["nodes"] if n["kind"] == "concept"]
        self.assertEqual({d["id"] for d in docs}, {"a1", "a2"})       # every artifact -> a node
        labels = {c["label"] for c in concepts}
        # salient domain terms surface; shared across docs ("buffer", "aggregation", "histidine")
        self.assertIn("buffer", labels)
        self.assertIn("aggregation", labels)
        self.assertIn("histidine", labels)

    def test_concept_ids_are_namespaced(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        for c in (n for n in g["nodes"] if n["kind"] == "concept"):
            self.assertTrue(c["id"].startswith("concept::"))

    def test_concepts_are_typed_against_the_ontology(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        by_label = {n["label"]: n for n in g["nodes"] if n["kind"] == "concept"}
        # every concept node carries an entity_type; ontology terms get a scientific type + canonical
        self.assertTrue(all("entity_type" in n for n in by_label.values()))
        self.assertEqual(by_label["histidine"]["entity_type"], "excipient")
        self.assertEqual(by_label["aggregation"]["entity_type"], "property")
        self.assertEqual(by_label["aggregation"]["canonical"], "aggregation")
        # a non-scientific token stays a generic "term"
        self.assertEqual(by_label["buffer"]["entity_type"], "term")
        # stats expose the typed-concept breakdown for the legend
        stats = g["stats"]
        self.assertGreaterEqual(stats["typed_concepts"], 2)
        self.assertEqual(stats["concept_type_counts"].get("excipient", 0), 2)  # histidine + citrate

    def test_mentions_edges_link_document_to_concept(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        mentions = [e for e in g["edges"] if e["type"] == "mentions"]
        self.assertTrue(mentions)
        # a1 mentions "buffer"
        self.assertIn(("a1", "concept::buffer"),
                      {(e["source"], e["target"]) for e in mentions})

    def test_cooccurrence_edges_between_concepts(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        co = [e for e in g["edges"] if e["type"] == "co-occurs"]
        self.assertTrue(co)
        # buffer and aggregation co-occur (chunk a1::c2 and a2::c1) -> weight >= 1
        pairs = {frozenset((e["source"], e["target"])): e["weight"] for e in co}
        self.assertIn(frozenset(("concept::buffer", "concept::aggregation")), pairs)
        self.assertGreaterEqual(pairs[frozenset(("concept::buffer", "concept::aggregation"))], 1)

    def test_stopwords_and_pure_integers_excluded(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        labels = {n["label"] for n in g["nodes"] if n["kind"] == "concept"}
        for noise in ("the", "at", "of", "was", "with"):
            self.assertNotIn(noise, labels)
        # pure integers are dropped; decimals like 5.5 are kept (informative measurements)
        self.assertNotIn("5", labels)

    def test_doc_freq_counts_distinct_artifacts(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        by_label = {n["label"]: n for n in g["nodes"] if n["kind"] == "concept"}
        # histidine appears in a1 and a2 -> doc_freq 2; citrate only in a1 -> doc_freq 1
        self.assertEqual(by_label["histidine"]["doc_freq"], 2)
        self.assertEqual(by_label["citrate"]["doc_freq"], 1)

    def test_stats_are_honest_and_present(self):
        g = build_project_graph(_ARTS, _CHUNKS)
        s = g["stats"]
        self.assertEqual(s["documents"], 2)
        self.assertGreater(s["concepts"], 0)
        self.assertIn("not LLM", s["method"])  # honest labelling, not overclaimed

    def test_deterministic(self):
        self.assertEqual(build_project_graph(_ARTS, _CHUNKS), build_project_graph(_ARTS, _CHUNKS))

    def test_empty_corpus_returns_documents_no_concepts(self):
        g = build_project_graph(_ARTS, [])  # artifacts but no chunks
        self.assertEqual([n for n in g["nodes"] if n["kind"] == "concept"], [])
        self.assertEqual(g["stats"]["concepts"], 0)
        # the answer-shape is still valid (nodes/edges/stats keys present)
        self.assertEqual(set(g), {"nodes", "edges", "stats"})

    def test_no_artifacts_no_chunks(self):
        g = build_project_graph([], [])
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])

    def test_concept_cap_respected(self):
        chunks = [{"id": f"a1::c{i}", "artifact_id": "a1",
                   "chunk_text": " ".join(f"term{i}_{j}word" for j in range(5))} for i in range(60)]
        g = build_project_graph([{"id": "a1", "title": "big", "artifact_type": "note"}], chunks,
                                max_concepts=10)
        self.assertLessEqual(len([n for n in g["nodes"] if n["kind"] == "concept"]), 10)

    def test_lexical_edges_tagged_edge_class(self):
        # The co-occurrence / mentions layer is tagged edge_class="lexical" so the SPA can toggle /
        # de-emphasize it independently of the typed relation structure.
        g = build_project_graph(_ARTS, _CHUNKS)
        for e in g["edges"]:
            if e["type"] in ("mentions", "co-occurs"):
                self.assertEqual(e.get("edge_class"), "lexical", e)


class GraphTypedEdgePayloadTests(unittest.TestCase):
    """Pins the /graph payload contract the SPA graph view depends on: typed edges carry `type`, both
    endpoint node ids, provenance, and are cleanly distinguished (edge_class) from co-occurrence."""

    _ARTS = [
        {"id": "exp1", "title": "Buffer screen", "artifact_type": "note", "source_uri": "paste:exp1"},
        {"id": "exp2", "title": "pH repeat", "artifact_type": "note", "source_uri": "paste:exp2"},
    ]
    _CHUNKS = [
        {"id": "exp1::c1", "artifact_id": "exp1",
         "chunk_text": "Histidine buffer at pH 5.5 gave the lowest aggregation.",
         "page_or_row_reference": "line 1"},
        {"id": "exp2::c1", "artifact_id": "exp2",
         "chunk_text": "In the repeat aggregation was lowest at pH 7.0.",
         "page_or_row_reference": "line 2"},
    ]

    def _graph(self):
        return build_project_graph(self._ARTS, self._CHUNKS)

    def test_typed_edges_present_and_tagged(self):
        g = self._graph()
        typed = [e for e in g["edges"] if e.get("edge_class") == "typed"]
        self.assertTrue(typed, "expected at least one typed edge (pH 5.5 vs 7.0 contradiction)")
        # the pH disagreement surfaces as a contradicts edge between the two documents
        self.assertIn("contradicts", {e["type"] for e in typed})

    def test_typed_edge_carries_type_endpoints_and_provenance(self):
        g = self._graph()
        node_ids = {n["id"] for n in g["nodes"]}
        e = next(e for e in g["edges"] if e.get("edge_class") == "typed")
        self.assertIn("type", e)
        # both endpoints are real node ids in the same payload (so the UI can locate + draw them)
        self.assertIn(e["source"], node_ids)
        self.assertIn(e["target"], node_ids)
        # provenance: each evidence item names an artifact, a chunk, a location and a verbatim quote
        self.assertTrue(e["evidence"])
        for v in e["evidence"]:
            self.assertIn(v["artifact_id"], {"exp1", "exp2"})
            self.assertTrue(v["chunk_id"])
            self.assertTrue(v["quote"])

    def test_typed_vs_cooccurrence_are_distinguished(self):
        g = self._graph()
        classes = {e.get("edge_class") for e in g["edges"]}
        # both families are present and distinguishable by edge_class (never conflated)
        self.assertIn("typed", classes)
        self.assertIn("lexical", classes)
        for e in g["edges"]:
            self.assertIn(e.get("edge_class"), {"typed", "lexical"})

    def test_document_nodes_carry_source_uri_for_inspection(self):
        g = self._graph()
        docs = [n for n in g["nodes"] if n["kind"] == "document"]
        self.assertTrue(all("source_uri" in n for n in docs))

    def test_stats_report_typed_edge_counts(self):
        s = self._graph()["stats"]
        self.assertIn("typed_edges", s)
        self.assertIn("typed_edge_counts", s)
        self.assertGreaterEqual(s["typed_edges"], 1)
        self.assertIn("cross-document entity linking", s["method"])


if __name__ == "__main__":
    unittest.main()
