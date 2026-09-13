"""Biolink constants and CURIE typing."""

from __future__ import annotations

import unittest

from evidence_graph.biolink import (
    AMBIGUOUS_PREFIXES,
    CATEGORIES,
    DEFAULT_CATEGORY,
    PREFIX_TO_CATEGORY,
    category_chain,
    category_for_curie,
    category_for_prefix,
)


class PrefixTypingTests(unittest.TestCase):
    def test_unambiguous_prefixes_type_their_curies(self):
        for curie, expected in [
            ("MONDO:0007254", "biolink:Disease"),
            ("CHEBI:15971", "biolink:SmallMolecule"),
            ("HGNC:3430", "biolink:Gene"),
            ("UniProtKB:P04626", "biolink:Protein"),
            ("UBERON:0002107", "biolink:AnatomicalEntity"),
            ("CL:0000236", "biolink:Cell"),
            ("PMID:12345", "biolink:Publication"),
        ]:
            with self.subTest(curie=curie):
                self.assertEqual(category_for_curie(curie), expected)

    def test_ambiguous_prefixes_are_refused_rather_than_guessed(self):
        # UMLS is declared by fifteen Biolink classes. Typing from it would produce a graph that
        # looks well-formed and is quietly wrong about what its nodes are.
        for prefix in sorted(AMBIGUOUS_PREFIXES):
            with self.subTest(prefix=prefix):
                self.assertIsNone(category_for_prefix(prefix))

    def test_go_is_typed_by_namespace_not_by_prefix(self):
        # The GO prefix is shared by five categories, so the term's own namespace decides.
        self.assertEqual(category_for_curie("GO:0006915", go_namespace="biological_process"),
                         "biolink:BiologicalProcess")
        self.assertEqual(category_for_curie("GO:0005739", go_namespace="cellular_component"),
                         "biolink:CellularComponent")
        self.assertEqual(category_for_curie("GO:0003674", go_namespace="molecular_function"),
                         "biolink:MolecularActivity")
        self.assertIsNone(category_for_curie("GO:0006915"))

    def test_unknown_and_malformed_input_returns_none(self):
        self.assertIsNone(category_for_curie("WIDGET:1"))
        self.assertIsNone(category_for_curie("not-a-curie"))
        self.assertIsNone(category_for_curie(""))


class ConsistencyTests(unittest.TestCase):
    def test_every_mapped_category_is_a_declared_category(self):
        for prefix, category in PREFIX_TO_CATEGORY.items():
            with self.subTest(prefix=prefix):
                self.assertIn(category, CATEGORIES)

    def test_no_prefix_is_both_mapped_and_declared_ambiguous(self):
        self.assertEqual(set(PREFIX_TO_CATEGORY) & AMBIGUOUS_PREFIXES, set())

    def test_category_chain_starts_with_the_category_and_ends_at_entity(self):
        chain = category_chain("biolink:Disease")
        self.assertEqual(chain[0], "biolink:Disease")
        self.assertEqual(chain[-1], "biolink:Entity")
        self.assertIn("biolink:DiseaseOrPhenotypicFeature", chain)

    def test_unknown_category_chains_to_itself(self):
        self.assertEqual(category_chain(DEFAULT_CATEGORY)[0], DEFAULT_CATEGORY)


if __name__ == "__main__":
    unittest.main()
