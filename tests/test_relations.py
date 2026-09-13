"""Tests for deterministic typed-relation extraction (scripts/app/relations.py).

Pins the differentiator contract: from a project's chunks we emit document→document typed edges
using the SAME vocabulary as the curated demo relations file — supports / contradicts / decided /
uses-protocol — each carrying provenance, with no LLM. Also pins high precision (agreeing values do
NOT become a `contradicts` edge) and byte-for-byte determinism.
"""

import unittest

from evidence_graph.relations import extract_typed_relations, normalize_entity


def _chunk(aid, cid, text, ref="line 1"):
    return {"id": cid, "artifact_id": aid, "chunk_text": text, "page_or_row_reference": ref}


_ARTS = [
    {"id": "exp1", "title": "Buffer screen", "artifact_type": "note"},
    {"id": "exp2", "title": "pH repeat", "artifact_type": "note"},
    {"id": "decision", "title": "Decision log", "artifact_type": "note"},
    {"id": "sop_fluor", "title": "SOP FLUOR-002 fluorescence assay", "artifact_type": "sop"},
]


def _edges_of(edges, etype):
    return {(e["source"], e["target"]) for e in edges if e["type"] == etype}


class TypedRelationTests(unittest.TestCase):
    def test_contradicts_edge_on_disagreeing_measured_value(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "Histidine buffer at pH 5.5 gave the lowest aggregation."),
            _chunk("exp2", "exp2::c1", "In the repeat aggregation was lowest at pH 7.0."),
        ]
        edges = extract_typed_relations(_ARTS[:2], chunks)
        self.assertIn(("exp1", "exp2"), _edges_of(edges, "contradicts"))
        # no false supports edge between the two disagreeing artifacts
        self.assertNotIn(("exp1", "exp2"), _edges_of(edges, "supports"))

    def test_supports_edge_on_agreeing_measured_value(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "pH 6.0 was selected as optimal."),
            _chunk("exp2", "exp2::c1", "The repeat confirmed pH 6.0 as best."),
        ]
        edges = extract_typed_relations(_ARTS[:2], chunks)
        self.assertIn(("exp1", "exp2"), _edges_of(edges, "supports"))
        # agreeing values must NOT be flagged as a contradiction (precision)
        self.assertNotIn(("exp1", "exp2"), _edges_of(edges, "contradicts"))

    def test_decided_edge_from_decision_verb(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "Histidine buffer at pH 5.5 reduced aggregation."),
            _chunk("decision", "decision::c1", "We decided to select pH 5.5 for the formulation."),
        ]
        edges = extract_typed_relations([_ARTS[0], _ARTS[2]], chunks)
        self.assertIn(("decision", "exp1"), _edges_of(edges, "decided"))

    def test_uses_protocol_edge_from_sop_reference(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "Fluorescence was measured per SOP FLUOR-002."),
            _chunk("sop_fluor", "sop_fluor::c1", "SOP FLUOR-002: fluorescence assay procedure."),
        ]
        edges = extract_typed_relations([_ARTS[0], _ARTS[3]], chunks)
        self.assertIn(("exp1", "sop_fluor"), _edges_of(edges, "uses-protocol"))

    def test_vocabulary_matches_demo_relations_file(self):
        # Every emitted edge type is drawn from the curated demo relations vocabulary.
        allowed = {"supports", "contradicts", "decided", "uses-protocol"}
        chunks = [
            _chunk("exp1", "exp1::c1", "pH 5.5 per SOP FLUOR-002."),
            _chunk("exp2", "exp2::c1", "pH 7.0 in the repeat."),
            _chunk("decision", "decision::c1", "We selected pH 5.5."),
            _chunk("sop_fluor", "sop_fluor::c1", "SOP FLUOR-002 procedure."),
        ]
        edges = extract_typed_relations(_ARTS, chunks)
        self.assertTrue(edges)
        self.assertTrue({e["type"] for e in edges} <= allowed)

    def test_edges_carry_provenance(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "pH 5.5 best.", ref="line 3"),
            _chunk("exp2", "exp2::c1", "pH 7.0 best.", ref="line 4"),
        ]
        edges = extract_typed_relations(_ARTS[:2], chunks)
        c = next(e for e in edges if e["type"] == "contradicts")
        self.assertTrue(c["evidence"])
        ev = c["evidence"][0]
        self.assertIn(ev["artifact_id"], {"exp1", "exp2"})
        self.assertTrue(ev["chunk_id"])
        self.assertTrue(ev["quote"])
        self.assertIn(ev["location"], {"line 3", "line 4"})

    def test_no_edge_when_only_one_artifact_reports_a_quantity(self):
        chunks = [_chunk("exp1", "exp1::c1", "pH 5.5 was best.")]
        edges = extract_typed_relations(_ARTS[:1], chunks)
        self.assertEqual(_edges_of(edges, "supports"), set())
        self.assertEqual(_edges_of(edges, "contradicts"), set())

    def test_deterministic(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "pH 5.5 per SOP FLUOR-002."),
            _chunk("exp2", "exp2::c1", "pH 7.0 in the repeat."),
            _chunk("decision", "decision::c1", "We selected pH 5.5."),
            _chunk("sop_fluor", "sop_fluor::c1", "SOP FLUOR-002 procedure."),
        ]
        self.assertEqual(extract_typed_relations(_ARTS, chunks),
                         extract_typed_relations(_ARTS, chunks))

    def test_documented_contradiction_links_named_artifact(self):
        chunks = [
            _chunk("exp1", "exp1::c1", "The buffer screen showed histidine reduced aggregation."),
            _chunk("exp2", "exp2::c1", "We could not reproduce the buffer screen result."),
        ]
        edges = extract_typed_relations(_ARTS[:2], chunks)
        # exp2 explicitly contradicts the named "buffer screen" (exp1's title)
        self.assertIn(("exp2", "exp1"), _edges_of(edges, "contradicts"))

    def test_edges_carry_edge_class_typed(self):
        # Every typed edge is tagged edge_class="typed" so the payload separates it from the lexical
        # co-occurrence/mentions layer (the SPA styles + explains them differently).
        chunks = [
            _chunk("exp1", "exp1::c1", "pH 5.5 best."),
            _chunk("exp2", "exp2::c1", "pH 7.0 best."),
        ]
        edges = extract_typed_relations(_ARTS[:2], chunks)
        self.assertTrue(edges)
        self.assertTrue(all(e["edge_class"] == "typed" for e in edges))


class NormalizeEntityTests(unittest.TestCase):
    """Cross-document entity linking: surface forms of ONE named entity must fold to one key, while
    genuinely distinct terms must NOT merge (conservative to avoid false links across documents)."""

    def test_mab_x_aliases_all_fold_to_one_key(self):
        # mAb-X == mAbX == mAb X (the persona molecule) — the headline alias case.
        keys = {normalize_entity(s) for s in ("mAb-X", "mAbX", "mAb X", "  mAb-X.  ")}
        self.assertEqual(len(keys), 1)
        self.assertNotIn("", keys)

    def test_designator_variants_fold(self):
        self.assertEqual(normalize_entity("IgG1"), normalize_entity("IgG-1"))
        self.assertEqual(normalize_entity("IgG1"), normalize_entity("IgG 1"))
        self.assertEqual(normalize_entity("FLUOR-002"), normalize_entity("fluor 002"))

    def test_distinct_entities_do_not_merge(self):
        # mAb-X and mAb-Y are different molecules; must NOT collapse.
        self.assertNotEqual(normalize_entity("mAb-X"), normalize_entity("mAb-Y"))
        self.assertNotEqual(normalize_entity("IgG1"), normalize_entity("IgG4"))
        self.assertNotEqual(normalize_entity("CD20"), normalize_entity("CD19"))

    def test_prose_and_generic_words_are_not_entities(self):
        # Conservative: multi-word prose and generic single words are dropped (return '').
        for s in ("buffer screen", "the sample", "antibody", "results", "figure 1", "pH", "it", ""):
            self.assertEqual(normalize_entity(s), "", s)

    def test_cross_document_entity_link_across_surface_forms(self):
        # A contradiction chunk in a2 mentioning `mAbX` links to a1 which mentions `mAb-X` — the
        # SAME molecule despite the surface-form drift. Provenance keeps BOTH artifacts' chunks.
        arts = [{"id": "a1", "title": "Aggregation study", "artifact_type": "note"},
                {"id": "a2", "title": "Repeat run", "artifact_type": "note"}]
        chunks = [
            _chunk("a1", "a1::c1", "mAb-X aggregated heavily at high temperature.", ref="line 1"),
            _chunk("a2", "a2::c1", "We could not reproduce the mAbX aggregation.", ref="line 2"),
        ]
        edges = extract_typed_relations(arts, chunks)
        entity_edges = [e for e in edges if e["type"] == "contradicts" and e["quantity"] == "mabx"]
        self.assertTrue(entity_edges, "cross-document entity link not created")
        e = entity_edges[0]
        self.assertEqual({e["source"], e["target"]}, {"a1", "a2"})
        # provenance spans both artifacts (source chunk + the linked artifact's own mention)
        self.assertEqual({v["artifact_id"] for v in e["evidence"]}, {"a1", "a2"})

    def test_no_entity_link_when_forms_are_genuinely_different(self):
        # mAb-X vs mAb-Y must NOT produce a cross-document entity edge.
        arts = [{"id": "a1", "title": "X study", "artifact_type": "note"},
                {"id": "a2", "title": "Y study", "artifact_type": "note"}]
        chunks = [
            _chunk("a1", "a1::c1", "mAb-X aggregated at high temperature."),
            _chunk("a2", "a2::c1", "We could not reproduce the mAb-Y aggregation."),
        ]
        edges = extract_typed_relations(arts, chunks)
        self.assertFalse([e for e in edges if e["quantity"] in ("mabx", "maby")])


if __name__ == "__main__":
    unittest.main()
