"""RO-Crate 1.1 structural validation.

A validator is only worth having if it rejects things, so most of these are negative cases. Each
one breaks a valid crate in exactly one way and asserts the issue is named.
"""

from __future__ import annotations

import copy
import unittest
from datetime import date

from evidence_graph.fair import ro_crate
from evidence_graph.ro_crate_validation import validate_ro_crate

PINNED = date(2026, 1, 15)


def _valid_crate() -> dict:
    artifact = {
        "id": "art-1",
        "title": "A study",
        "artifact_type": "paper",
        "source_system": "upload",
        "structured_metadata": {"doi": "10.5281/zenodo.0000000", "creators": [{"name": "A. Researcher"}]},
    }
    return ro_crate([artifact], "A collection", today=PINNED)


def _entity(crate: dict, entity_id: str) -> dict:
    return next(e for e in crate["@graph"] if e.get("@id") == entity_id)


class ValidCrateTests(unittest.TestCase):
    def test_a_generated_crate_is_valid(self):
        self.assertEqual(validate_ro_crate(_valid_crate()), [])


class InvalidCrateTests(unittest.TestCase):
    def assert_rejects(self, crate: dict, fragment: str):
        issues = validate_ro_crate(crate)
        self.assertTrue(issues, "expected the validator to report an issue")
        self.assertTrue(
            any(fragment in issue for issue in issues),
            f"no issue mentioned {fragment!r}; got {issues}",
        )

    def test_missing_context(self):
        crate = _valid_crate()
        del crate["@context"]
        self.assert_rejects(crate, "@context")

    def test_missing_metadata_descriptor(self):
        crate = _valid_crate()
        crate["@graph"] = [e for e in crate["@graph"] if e.get("@id") != "ro-crate-metadata.json"]
        self.assert_rejects(crate, "ro-crate-metadata.json")

    def test_root_without_a_name(self):
        crate = _valid_crate()
        del _entity(crate, "./")["name"]
        self.assert_rejects(crate, "no name")

    def test_dangling_reference_is_caught(self):
        # The failure that matters in practice: a crate that lists a part which is not in the graph,
        # so a consumer resolving hasPart finds nothing.
        crate = _valid_crate()
        _entity(crate, "./")["hasPart"].append({"@id": "does-not-exist"})
        self.assert_rejects(crate, "does-not-exist")

    def test_duplicate_ids_are_caught(self):
        crate = _valid_crate()
        crate["@graph"].append(copy.deepcopy(_entity(crate, "./")))
        self.assert_rejects(crate, "duplicate")

    def test_data_entity_without_provenance(self):
        crate = _valid_crate()
        part_id = _entity(crate, "./")["hasPart"][0]["@id"]
        del _entity(crate, part_id)["sciway:sourceSystem"]
        self.assert_rejects(crate, "provenance")

    def test_external_http_references_are_allowed(self):
        # Licences, PIDs and the spec itself legitimately live outside the graph.
        crate = _valid_crate()
        self.assertNotIn("https://creativecommons.org", str(validate_ro_crate(crate)))


if __name__ == "__main__":
    unittest.main()
