"""Tests for the W3C PROV-O answer lineage (scripts/app/provenance.py).

Covers building the derivation graph from an answer packet (answer → chunks → documents), the
SHA-256 tamper-evidence (a silent edit to a cited quote / source is caught), lineage tracing, and
the report — the audit artifact behind SciWay's "prove what the system did and why" story.
"""

import unittest

from evidence_graph import provenance as P


def _packet(**over):
    pkt = {
        "question": "Which pH reduced aggregation?",
        "answer_status": "supported",
        "draft_answer": "pH 5.5 minimized aggregation. [a1]",
        "answer_engine": "llm:mistral:mistral-small",
        "source_inspection": [
            {"artifact_id": "a1", "artifact_title": "Formulation study", "chunk_id": "a1::chunk_003",
             "chunk_quote": "Aggregation was lowest at pH 5.5.", "source_uri": "https://doi.org/10.x/abc",
             "location": "Results · p. 4", "retrieval_score": 3.2, "rcs_relevance": 9,
             "evidence_status": "expected_support", "source_family": "upload"},
            {"artifact_id": "a2", "artifact_title": "Second paper", "chunk_id": "a2::chunk_001",
             "chunk_quote": "At pH 7 aggregation rose.", "source_uri": "https://doi.org/10.x/def",
             "location": "p. 5", "retrieval_score": 2.1, "evidence_status": "supporting_context",
             "source_family": "upload"},
        ],
    }
    pkt.update(over)
    return pkt


class AnswerLineageTests(unittest.TestCase):
    def test_builds_answer_chunk_document_graph(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        by_type = {}
        for x in e:
            by_type.setdefault(x.entity_type, []).append(x)
        self.assertEqual(len(by_type["answer"]), 1)
        self.assertEqual(len(by_type["chunk"]), 2)
        self.assertEqual(len(by_type["document"]), 2)   # two distinct artifacts
        answer = by_type["answer"][0]
        self.assertEqual(answer.used_entities, ["a1::chunk_003", "a2::chunk_001"])
        self.assertEqual(answer.agent_id, "sciway:llm:mistral:mistral-small")
        chunk = by_type["chunk"][0]
        self.assertEqual(chunk.parent_entity_id, "document:a1")
        self.assertEqual(chunk.source_quote, "Aggregation was lowest at pH 5.5.")
        self.assertEqual(chunk.confidence, 0.9)          # rcs_relevance 9 → 0.9

    def test_deterministic_checksums(self):
        a = P.answer_lineage(_packet(), timestamp="T0")
        b = P.answer_lineage(_packet(), timestamp="T0")
        self.assertEqual([x.checksum for x in a], [x.checksum for x in b])
        self.assertTrue(all(x.checksum for x in a))

    def test_tolerates_retrievalhit_shape_and_missing_fields(self):
        pkt = {"question": "q", "answer_status": "supported",
               "citations": [{"artifact_id": "a1", "chunk_id": "a1::c1", "quote": "raw hit quote",
                              "title": "Paper", "source_uri": ""}]}
        e = P.answer_lineage(pkt, timestamp="T0")
        chunk = next(x for x in e if x.entity_type == "chunk")
        self.assertEqual(chunk.source_quote, "raw hit quote")
        self.assertEqual(chunk.confidence, 1.0)          # no rcs → default

    def test_refusal_packet_marks_refused(self):
        e = P.answer_lineage(_packet(answer_status="insufficient_evidence", source_inspection=[]),
                             timestamp="T0")
        answer = e[0]
        self.assertEqual(answer.entity_type, "answer")
        self.assertTrue(answer.metadata["refused"])
        self.assertEqual(answer.used_entities, [])


class IntegrityTests(unittest.TestCase):
    def test_intact_lineage_verifies(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        self.assertEqual(P.verify_lineage(e), [])
        self.assertTrue(all(P.verify_checksum(x) for x in e))

    def test_tampered_quote_is_detected(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        dicts = [x.to_dict() for x in e]
        dicts[1]["source_quote"] = "silently edited"     # tamper with a cited quote
        bad = P.verify_lineage(dicts)
        self.assertEqual(bad, ["a1::chunk_003"])

    def test_tampered_derivation_link_is_detected(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        dicts = [x.to_dict() for x in e]
        dicts[0]["used_entities"] = ["a1::chunk_003"]    # drop a source from the answer's derivation
        self.assertIn(dicts[0]["entity_id"], P.verify_lineage(dicts))

    def test_missing_checksum_does_not_verify(self):
        entry = P.ProvenanceEntry(entity_id="x", entity_type="answer")
        self.assertFalse(P.verify_checksum(entry))       # never finalized → no checksum


class TraceAndReportTests(unittest.TestCase):
    def test_trace_from_answer_reaches_chunks_and_documents(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        answer_id = e[0].entity_id
        chain = [x.entity_id for x in P.trace_lineage(e, answer_id)]
        self.assertEqual(chain[0], answer_id)
        self.assertIn("a1::chunk_003", chain)
        self.assertIn("document:a1", chain)
        self.assertIn("document:a2", chain)

    def test_trace_from_chunk_reaches_only_its_document(self):
        e = P.answer_lineage(_packet(), timestamp="T0")
        chain = [x.entity_id for x in P.trace_lineage(e, "a1::chunk_003")]
        self.assertEqual(chain, ["a1::chunk_003", "document:a1"])

    def test_report_shape(self):
        rep = P.lineage_report(P.answer_lineage(_packet(), timestamp="T0"))
        self.assertEqual(rep["provenance_model"], "W3C PROV-O")
        self.assertEqual(rep["entity_counts"], {"answer": 1, "chunk": 2, "document": 2})
        self.assertTrue(rep["tamper_check"]["intact"])
        self.assertEqual(sorted(rep["source_documents"]),
                         ["https://doi.org/10.x/abc", "https://doi.org/10.x/def"])


class JsonLdTests(unittest.TestCase):
    def test_prov_jsonld_shape_and_links(self):
        entries = P.answer_lineage(_packet(), timestamp="T0")
        doc = P.prov_jsonld([entries])
        self.assertEqual(doc["@context"], P.PROV_CONTEXT)
        by_id = {n["@id"]: n for n in doc["@graph"]}
        answer = next(n for n in doc["@graph"] if n.get("sciway:entityType") == "answer")
        # answer wasDerivedFrom its chunks; wasGeneratedBy an activity; wasAttributedTo an agent
        self.assertEqual({r["@id"] for r in answer["prov:wasDerivedFrom"]},
                         {"a1::chunk_003", "a2::chunk_001"})
        self.assertIn(answer["prov:wasGeneratedBy"]["@id"], by_id)          # activity node exists
        self.assertIn(answer["prov:wasAttributedTo"]["@id"], by_id)         # agent node exists
        # a chunk wasDerivedFrom its document; the checksum travels as sciway:checksum
        chunk = by_id["a1::chunk_003"]
        self.assertEqual(chunk["prov:wasDerivedFrom"], [{"@id": "document:a1"}])
        self.assertTrue(chunk["sciway:checksum"])

    def test_prov_jsonld_dedups_shared_nodes(self):
        e1 = P.answer_lineage(_packet(question="q1"), timestamp="T0")
        e2 = P.answer_lineage(_packet(question="q2"), timestamp="T0")
        doc = P.prov_jsonld([e1, e2])
        ids = [n["@id"] for n in doc["@graph"]]
        self.assertEqual(len(ids), len(set(ids)))                            # no duplicate @ids
        self.assertEqual(sum(1 for n in doc["@graph"] if n.get("@type") == "prov:Activity"), 1)


if __name__ == "__main__":
    unittest.main()
