"""FAIR export: DataCite completeness, Dublin Core XML, and RO-Crate JSON-LD.

The crate tests deliberately assert against `validate_ro_crate` rather than against a hand-written
expected document. A crate that merely looks right in a diff but fails the 1.1 structural rules is
the failure mode worth catching, and it is the one a reader of the output cannot spot.
"""

from __future__ import annotations

import unittest
import xml.dom.minidom as minidom
from datetime import date

from evidence_graph.fair import (
    DATACITE_MANDATORY,
    Producer,
    dublin_core_xml,
    fair_record,
    fair_report,
    ro_crate,
)
from evidence_graph.ro_crate_validation import validate_ro_crate

PINNED = date(2026, 1, 15)


def _complete_artifact() -> dict:
    return {
        "id": "art-1",
        "title": "Buffer screen for mAb-X aggregation",
        "artifact_type": "paper",
        "source_system": "upload",
        "structured_metadata": {
            "doi": "10.5281/zenodo.0000000",
            "creators": [{"name": "A. Researcher", "orcid": "0000-0002-1825-0097"}],
            "publisher": "Example Institute",
            "publication_year": 2026,
            "license": "CC-BY-4.0",
            "keywords": ["aggregation", "formulation"],
        },
    }


def _bare_artifact() -> dict:
    return {"id": "art-2", "title": "Untitled note", "artifact_type": "note", "source_system": "paste"}


class FairRecordTests(unittest.TestCase):
    def test_complete_artifact_satisfies_every_mandatory_field(self):
        record = fair_record(_complete_artifact())
        self.assertEqual(record["missing_mandatory_fields"], [])
        self.assertTrue(record["fair_complete"])
        self.assertEqual(record["datacite_mandatory_present"], len(DATACITE_MANDATORY))

    def test_missing_metadata_is_named_rather_than_silently_passed(self):
        record = fair_record(_bare_artifact())
        self.assertFalse(record["fair_complete"])
        # The point of the report is telling the depositor what to fix.
        self.assertIn("identifier", record["missing_mandatory_fields"])
        self.assertIn("publisher", record["missing_mandatory_fields"])

    def test_author_or_owner_is_a_fallback_creator(self):
        artifact = _bare_artifact() | {"author_or_owner": "A. Researcher"}
        self.assertEqual(fair_record(artifact)["creators"], [{"name": "A. Researcher"}])

    def test_report_aggregates_and_does_not_claim_certification(self):
        report = fair_report([_complete_artifact(), _bare_artifact()])
        self.assertEqual(report["records_total"], 2)
        self.assertEqual(report["records_fully_compliant"], 1)
        self.assertLess(report["metadata_completeness_pct"], 100.0)
        self.assertIn("not a certificate", report["note"])


class DublinCoreTests(unittest.TestCase):
    def test_output_is_well_formed_xml(self):
        minidom.parseString(dublin_core_xml([_complete_artifact()]))  # raises if malformed

    def test_special_characters_are_escaped(self):
        artifact = _complete_artifact() | {"title": 'Aggregation <5% & "stable"'}
        xml = dublin_core_xml([artifact])
        self.assertNotIn('<5%', xml)
        self.assertIn("&lt;5% &amp; &quot;stable&quot;", xml)
        minidom.parseString(xml)

    def test_single_artifact_selection(self):
        xml = dublin_core_xml([_complete_artifact(), _bare_artifact()], artifact_id="art-1")
        self.assertIn("Buffer screen", xml)
        self.assertNotIn("Untitled note", xml)


class RoCrateTests(unittest.TestCase):
    def test_generated_crate_passes_structural_validation(self):
        crate = ro_crate([_complete_artifact()], "Formulation study", today=PINNED)
        self.assertEqual(validate_ro_crate(crate), [])

    def test_crate_is_reproducible_when_the_date_is_pinned(self):
        first = ro_crate([_complete_artifact()], "Formulation study", today=PINNED)
        second = ro_crate([_complete_artifact()], "Formulation study", today=PINNED)
        self.assertEqual(first, second)

    def test_producer_defaults_to_this_library_not_a_third_party(self):
        # Regression guard: the crate must never attribute a user's export to an organisation that
        # had nothing to do with it. Before extraction this was hardcoded to one company's name.
        crate = ro_crate([_complete_artifact()], "Formulation study", today=PINNED)
        orgs = [e for e in crate["@graph"] if e.get("@type") == "Organization"]
        self.assertEqual([o["name"] for o in orgs], ["evidence-graph"])

    def test_producer_is_caller_supplied(self):
        producer = Producer(name="Example Lab", url="https://example.org", description="A lab.")
        crate = ro_crate([_complete_artifact()], "Study", today=PINNED, producer=producer)
        root = next(e for e in crate["@graph"] if e["@id"] == "./")
        org = next(e for e in crate["@graph"] if e.get("@type") == "Organization")
        self.assertEqual(org["name"], "Example Lab")
        self.assertEqual(root["publisher"], {"@id": org["@id"]})
        self.assertEqual(validate_ro_crate(crate), [])

    def test_orcid_travels_into_the_crate(self):
        crate = ro_crate([_complete_artifact()], "Study", today=PINNED)
        person = next(e for e in crate["@graph"] if e.get("@type") == "Person")
        self.assertEqual(person["identifier"], "https://orcid.org/0000-0002-1825-0097")

    def test_non_depositable_artifacts_are_excluded(self):
        junk = {"id": "x", "title": "t", "artifact_type": "email", "source_system": "upload"}
        crate = ro_crate([_complete_artifact(), junk], "Study", today=PINNED)
        ids = {e.get("@id") for e in crate["@graph"]}
        self.assertNotIn("x", ids)
        self.assertEqual(validate_ro_crate(crate), [])


if __name__ == "__main__":
    unittest.main()
