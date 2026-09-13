"""KGX interchange output.

The tests worth having here are about interoperability: that a consumer who knows only Biolink can
still use the file, that required fields Biolink 4.x added are actually present, and that we never
export an assertion the adjudicator declined to make.
"""

from __future__ import annotations

import json
import unittest

from evidence_graph.adjudicate import adjudicate
from evidence_graph.kgx import (
    AGENT_TYPES,
    BROAD_PREDICATE,
    KNOWLEDGE_LEVELS,
    LIST_DELIMITER,
    KgxGraph,
    artifact_curie,
    edge_id,
    from_adjudications,
    to_jsonl,
    to_tsv,
    validate_kgx,
)

ARTIFACTS = [
    {"id": "PMC111", "title": "Buffer screen", "artifact_type": "paper", "source_system": "pmc-oa",
     "structured_metadata": {"doi": "10.1000/aaa", "pmid": "111", "publication_year": "2024",
                             "license": "CC BY"}},
    {"id": "PMC222", "title": "pH optimisation", "artifact_type": "paper", "source_system": "pmc-oa",
     "structured_metadata": {"doi": "10.1000/bbb", "pmid": "222", "publication_year": "2025",
                             "license": "CC BY"}},
]


def _edge(etype="contradicts", *, quantity="ph", grounded=True):
    quote = "Lowest aggregation at pH 5.5." if grounded else "Unrelated."
    return {
        "source": "PMC111", "target": "PMC222", "type": etype, "quantity": quantity,
        "edge_class": "typed", "method": "regex",
        "evidence": [
            {"artifact_id": "PMC111", "chunk_id": "c1", "source_uri": "u1",
             "location": "Results, paragraph 3", "quote": quote, "trigger": "ph=5.5"},
            {"artifact_id": "PMC222", "chunk_id": "c2", "source_uri": "u2",
             "location": "Results, paragraph 7", "quote": "Lowest aggregation at pH 7.0.",
             "trigger": "ph=7.0"},
        ],
    }


def _graph(edges, **kwargs):
    return from_adjudications(adjudicate(edges), ARTIFACTS, **kwargs)


class CurieTests(unittest.TestCase):
    def test_doi_is_preferred_because_the_world_can_resolve_it(self):
        self.assertEqual(artifact_curie(ARTIFACTS[0]), "doi:10.1000/aaa")

    def test_pmc_id_is_used_when_there_is_no_doi(self):
        artifact = {"id": "PMC999", "structured_metadata": {}}
        self.assertEqual(artifact_curie(artifact), "PMC:PMC999")

    def test_local_ids_are_namespaced_so_they_cannot_look_resolvable(self):
        self.assertEqual(artifact_curie({"id": "note-1"}), "eg:note-1")

    def test_doi_urls_and_prefixes_are_normalised(self):
        self.assertEqual(
            artifact_curie({"id": "x", "structured_metadata": {"doi": "https://doi.org/10.1/z"}}),
            "doi:10.1/z",
        )

    def test_edge_ids_are_content_derived_and_stable(self):
        self.assertEqual(edge_id("a", "p", "b", "ph"), edge_id("a", "p", "b", "ph"))
        self.assertNotEqual(edge_id("a", "p", "b", "ph"), edge_id("a", "p", "b", "temp"))


class BuildTests(unittest.TestCase):
    def test_asserted_edge_becomes_a_valid_kgx_edge(self):
        graph = _graph([_edge()])
        self.assertEqual(validate_kgx(graph), [])
        [edge] = graph.edges
        self.assertEqual(edge["subject"], "doi:10.1000/aaa")
        self.assertEqual(edge["predicate"], "eg:contradicts")
        self.assertEqual(edge["object"], "doi:10.1000/bbb")

    def test_required_biolink_4x_fields_are_present_and_legal(self):
        # The two fields most often missing from real KGX files.
        [edge] = _graph([_edge()]).edges
        self.assertIn(edge["knowledge_level"], KNOWLEDGE_LEVELS)
        self.assertIn(edge["agent_type"], AGENT_TYPES)
        self.assertEqual(edge["agent_type"], "text_mining_agent")

    def test_every_edge_carries_a_real_biolink_predicate_as_a_fallback(self):
        # Biolink has no assertion-level predicates, so a Biolink-only consumer would otherwise have
        # to drop the edge entirely.
        [edge] = _graph([_edge()]).edges
        self.assertEqual(edge["broad_predicate"], BROAD_PREDICATE)

    def test_supporting_text_carries_the_verbatim_passages(self):
        [edge] = _graph([_edge()]).edges
        self.assertEqual(edge["has_evidence_count"], 2)
        self.assertIn("Lowest aggregation at pH 5.5.", edge["supporting_text"])
        self.assertIn("Results, paragraph 3", edge["eg:evidence_location"])

    def test_abstained_edges_are_never_exported(self):
        # Exporting an open question as an interchange edge would hand a collaborator exactly the
        # unverified assertion this library exists to withhold.
        graph = _graph([_edge(grounded=False)])
        self.assertEqual(graph.edges, ())
        self.assertEqual(graph.nodes, ())

    def test_conflicted_edges_are_exported_and_marked(self):
        graph = _graph([_edge("supports"), _edge("contradicts")])
        self.assertEqual(len(graph.edges), 2)
        for edge in graph.edges:
            self.assertEqual(edge["eg:adjudication"], "conflict")
            self.assertTrue(edge["eg:conflicts_with"])
        self.assertEqual(validate_kgx(graph), [])

    def test_conflicts_can_be_withheld(self):
        graph = _graph([_edge("supports"), _edge("contradicts")], include_conflicted=False)
        self.assertEqual(graph.edges, ())

    def test_only_nodes_actually_used_by_an_edge_are_emitted(self):
        graph = _graph([_edge()])
        self.assertEqual({n["id"] for n in graph.nodes}, {"doi:10.1000/aaa", "doi:10.1000/bbb"})
        self.assertEqual(graph.nodes[0]["category"], ["biolink:Publication"])

    def test_edges_are_sorted_so_a_re_export_is_byte_identical(self):
        edges = [_edge(quantity=q) for q in ("temperature", "ph", "concentration")]
        first, second = _graph(edges), _graph(list(reversed(edges)))
        self.assertEqual([e["id"] for e in first.edges], [e["id"] for e in second.edges])

    def test_illegal_agent_type_is_rejected_at_the_boundary(self):
        with self.assertRaises(ValueError):
            _graph([_edge()], agent_type="a_helpful_robot")


class SerialisationTests(unittest.TestCase):
    def test_tsv_has_a_header_and_core_columns_first(self):
        nodes_tsv, edges_tsv = to_tsv(_graph([_edge()]))
        self.assertEqual(nodes_tsv.splitlines()[0].split("\t")[:2], ["id", "category"])
        self.assertEqual(edges_tsv.splitlines()[0].split("\t")[:4],
                         ["id", "subject", "predicate", "object"])

    def test_tsv_multivalued_fields_use_the_pipe_delimiter(self):
        _, edges_tsv = to_tsv(_graph([_edge()]))
        header = edges_tsv.splitlines()[0].split("\t")
        row = edges_tsv.splitlines()[1].split("\t")
        publications = row[header.index("publications")]
        self.assertIn(LIST_DELIMITER, publications)

    def test_tsv_rows_never_contain_a_stray_tab_or_newline(self):
        messy = _edge()
        messy["evidence"][0]["quote"] = "Lowest aggregation\tat pH 5.5.\nSecond line."
        _, edges_tsv = to_tsv(_graph([messy]))
        header, *rows = edges_tsv.rstrip("\n").splitlines()
        for row in rows:
            self.assertEqual(len(row.split("\t")), len(header.split("\t")))

    def test_jsonl_keeps_arrays_as_arrays_and_text_intact(self):
        messy = _edge()
        messy["evidence"][0]["quote"] = "Lowest aggregation\tat pH 5.5."
        nodes_jsonl, edges_jsonl = to_jsonl(_graph([messy]))
        node = json.loads(nodes_jsonl.splitlines()[0])
        edge = json.loads(edges_jsonl.splitlines()[0])
        self.assertIsInstance(node["category"], list)
        self.assertIsInstance(edge["publications"], list)
        # Unlike TSV, the JSON form preserves the passage exactly.
        self.assertIn("Lowest aggregation\tat pH 5.5.", edge["supporting_text"])

    def test_empty_graph_serialises_to_empty_strings(self):
        self.assertEqual(to_tsv(KgxGraph()), ("", ""))
        self.assertEqual(to_jsonl(KgxGraph()), ("", ""))


class ValidationTests(unittest.TestCase):
    def test_dangling_edge_endpoint_is_caught(self):
        graph = _graph([_edge()])
        broken = KgxGraph(graph.nodes[:1], graph.edges)
        self.assertTrue(any("missing node" in i for i in validate_kgx(broken)))

    def test_illegal_knowledge_level_is_caught(self):
        graph = _graph([_edge()])
        broken = KgxGraph(graph.nodes, ({**graph.edges[0], "knowledge_level": "vibes"},))
        self.assertTrue(any("knowledge_level" in i for i in validate_kgx(broken)))

    def test_node_without_a_biolink_category_is_caught(self):
        graph = _graph([_edge()])
        broken = KgxGraph(({**graph.nodes[0], "category": ["Publication"]}, *graph.nodes[1:]),
                          graph.edges)
        self.assertTrue(any("non-Biolink category" in i for i in validate_kgx(broken)))

    def test_duplicate_node_ids_are_caught(self):
        graph = _graph([_edge()])
        self.assertTrue(any("duplicate" in i
                            for i in validate_kgx(KgxGraph(graph.nodes + graph.nodes[:1], ()))))


if __name__ == "__main__":
    unittest.main()
