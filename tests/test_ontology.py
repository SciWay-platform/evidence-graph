"""Tests for the seed scientific ontology (scripts/app/ontology.py) that types KG concept terms."""

import unittest

from evidence_graph import ontology as O


class ClassifyConceptTests(unittest.TestCase):
    def test_exact_alias_maps_to_canonical_and_type(self):
        self.assertEqual(O.classify_concept("histidine"), {"canonical": "histidine", "type": "excipient"})
        self.assertEqual(O.classify_concept("sec"),
                         {"canonical": "size-exclusion chromatography", "type": "technique"})
        self.assertEqual(O.classify_concept("tween"), {"canonical": "polysorbate", "type": "excipient"})
        self.assertEqual(O.classify_concept("mab"),
                         {"canonical": "monoclonal antibody", "type": "molecule"})

    def test_case_insensitive_and_plural(self):
        self.assertEqual(O.classify_concept("HISTIDINE")["type"], "excipient")
        # "aggregates" folds to the "aggregation" property (explicit plural alias)
        self.assertEqual(O.classify_concept("aggregates"), {"canonical": "aggregation", "type": "property"})
        # a light singular form also matches (fragments -> fragment alias)
        self.assertEqual(O.classify_concept("fragments")["type"], "property")

    def test_fuzzy_typo_matches_conservatively(self):
        # a one-char typo of a long term still classifies
        self.assertEqual(O.classify_concept("viscosityy"), {"canonical": "viscosity", "type": "property"})

    def test_distinct_terms_not_merged(self):
        # excipients that are close-ish strings must stay separate (no over-eager fuzzy merge)
        self.assertEqual(O.classify_concept("acetate")["canonical"], "acetate")
        self.assertEqual(O.classify_concept("citrate")["canonical"], "citrate")

    def test_unknown_term_is_none(self):
        self.assertIsNone(O.classify_concept("banana"))
        self.assertIsNone(O.classify_concept(""))
        self.assertIsNone(O.classify_concept("   "))

    def test_every_type_is_declared(self):
        types = {t for _c, (t, _a) in O.SEED_VOCABULARY.items()}
        self.assertTrue(types.issubset(set(O.ONTOLOGY_TYPES)))


if __name__ == "__main__":
    unittest.main()
