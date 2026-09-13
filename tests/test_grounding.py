"""Grounding surface strings to biomedical CURIEs.

Two groups of tests. The first uses a small synthetic vocabulary written to a temp directory, so
normalisation, ranking and refusal are tested deterministically without depending on which ontology
release happens to be vendored. The second runs against the real vendored data and is skipped when
it has not been built, because a fresh checkout without `scripts/build_vocabulary.py` should still
have a green suite.
"""

from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

from evidence_graph.grounding import (
    DATA_DIR,
    MIN_TERM_LENGTH,
    Vocabulary,
    compact_term,
    ground,
    ground_all,
    load_vocabulary,
    normalize_term,
)

FIXTURE = {
    "hgnc": [
        ("HGNC:3430", "ERBB2", ["HER2", "NEU", "TNF-alpha"], ""),
        ("HGNC:11892", "TNF", ["TNF-alpha", "TNFA"], ""),
    ],
    "mondo": [("MONDO:0007254", "breast cancer", ["malignant neoplasm of breast"], "")],
    "chebi": [
        ("CHEBI:15971", "L-histidine", ["histidine"], ""),
        ("CHEBI:53426", "polysorbate 80", ["Tween 80"], ""),
    ],
    "go": [
        ("GO:0006915", "apoptotic process", [], "biological_process"),
        ("GO:0005739", "mitochondrion", [], "cellular_component"),
    ],
}


def _write_fixture(root: Path) -> None:
    for source, rows in FIXTURE.items():
        with gzip.open(root / f"{source}.tsv.gz", "wt", encoding="utf-8") as out:
            for curie, label, synonyms, qualifier in rows:
                out.write(f"{curie}\t{label}\t{'|'.join(synonyms)}\t{qualifier}\n")
    with gzip.open(root / "redirects.tsv.gz", "wt", encoding="utf-8") as out:
        out.write("MONDO:0000999\tMONDO:0007254\n")


class NormalizationTests(unittest.TestCase):
    def test_case_punctuation_and_spacing_collapse(self):
        self.assertEqual(normalize_term("IL-6"), normalize_term("IL 6"))

    def test_compact_key_unifies_symbol_and_number(self):
        # The literature writes IL-6, IL 6 and IL6 interchangeably.
        self.assertEqual(compact_term("IL-6"), compact_term("il6"))
        self.assertEqual(compact_term("IL 6"), compact_term("IL-6"))

    def test_greek_letters_fold_to_their_names(self):
        # A paper writes TNF-α; an ontology writes TNF-alpha. Both must reach the same key.
        self.assertEqual(normalize_term("TNF-α"), normalize_term("TNF-alpha"))
        self.assertEqual(normalize_term("β-sheet"), normalize_term("beta sheet"))

    def test_accents_are_stripped(self):
        self.assertEqual(normalize_term("Ångström"), normalize_term("Angstrom"))


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        _write_fixture(root)
        load_vocabulary.cache_clear()
        self.vocab = load_vocabulary(str(root))

    def tearDown(self):
        self._tmp.cleanup()
        load_vocabulary.cache_clear()

    def test_primary_label_grounds(self):
        result = ground("breast cancer", vocabulary=self.vocab)
        self.assertEqual(result.best.curie, "MONDO:0007254")
        self.assertEqual(result.best.category, "biolink:Disease")
        self.assertFalse(result.best.via_synonym)

    def test_synonym_grounds_and_is_marked_as_such(self):
        result = ground("Tween 80", vocabulary=self.vocab)
        self.assertEqual(result.best.curie, "CHEBI:53426")
        self.assertTrue(result.best.via_synonym)

    def test_go_terms_are_typed_from_their_namespace(self):
        self.assertEqual(ground("apoptotic process", vocabulary=self.vocab).best.category,
                         "biolink:BiologicalProcess")
        self.assertEqual(ground("mitochondrion", vocabulary=self.vocab).best.category,
                         "biolink:CellularComponent")

    def test_unknown_terms_are_refused_rather_than_approximated(self):
        # No edit-distance fallback: "histidin" must not become histidine, because a wrong
        # identifier is a citation that looks checkable and is not.
        for term in ("histidin", "mAb-X", "the buffer", "aggregation"):
            with self.subTest(term=term):
                self.assertIsNone(ground(term, vocabulary=self.vocab))

    def test_very_short_terms_are_refused(self):
        self.assertIsNone(ground("pH", vocabulary=self.vocab))
        self.assertLess(len("pH"), MIN_TERM_LENGTH)

    def test_ambiguity_is_reported_with_every_candidate(self):
        # "TNF-alpha" is a synonym of both entries in this fixture, so both must come back.
        result = ground("TNF-alpha", vocabulary=self.vocab)
        self.assertTrue(result.ambiguous)
        self.assertEqual({c.curie for c in result.concepts}, {"HGNC:11892", "HGNC:3430"})

    def test_primary_labels_outrank_synonyms(self):
        result = ground("TNF", vocabulary=self.vocab)
        self.assertEqual(result.best.curie, "HGNC:11892")
        self.assertFalse(result.best.via_synonym)

    def test_obsolete_curies_are_redirected(self):
        vocab = Vocabulary(index={"old thing": [("MONDO:0000999", "old", "mondo", False, "")]},
                           redirects=self.vocab.redirects, sources=("mondo",))
        self.assertEqual(ground("old thing", vocabulary=vocab).best.curie, "MONDO:0007254")

    def test_missing_data_directory_degrades_to_grounding_nothing(self):
        load_vocabulary.cache_clear()
        with tempfile.TemporaryDirectory() as empty:
            vocab = load_vocabulary(empty)
            self.assertEqual(vocab.sources, ())
            self.assertIsNone(ground("breast cancer", vocabulary=vocab))

    def test_ground_all_keeps_what_it_could_not_place(self):
        report = ground_all(["breast cancer", "mAb-X", "L-histidine", "frobnicase"],
                            vocabulary=self.vocab)
        self.assertEqual(report.summary()["grounded"], 2)
        self.assertEqual(set(report.unmapped), {"mAb-X", "frobnicase"})
        self.assertAlmostEqual(report.coverage, 0.5)

    def test_report_serialises(self):
        report = ground_all(["breast cancer"], vocabulary=self.vocab)
        self.assertEqual(report.grounded[0].as_dict()["concepts"][0]["curie"], "MONDO:0007254")


class SymbolGuardTests(unittest.TestCase):
    """Gene symbols against ordinary English.

    WAS, CAN, FOR and SET are all approved HGNC symbols. With a lowercased index they match prose,
    and the first real corpus duly produced "for -> HGNC:12799 WWOX".
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        with gzip.open(root / "hgnc.tsv.gz", "wt", encoding="utf-8") as out:
            out.write("HGNC:12799\tWWOX\tFOR|WOX1\t\n")
            out.write("HGNC:12731\tWAS\tIMD2\t\n")
            out.write("HGNC:3430\tERBB2\tHER2|NEU\t\n")
            out.write("HGNC:11998\tTP53\tp53\t\n")
        with gzip.open(root / "chebi.tsv.gz", "wt", encoding="utf-8") as out:
            out.write("CHEBI:15377\twater\t\t\n")
        load_vocabulary.cache_clear()
        self.vocab = load_vocabulary(str(root))

    def tearDown(self):
        self._tmp.cleanup()
        load_vocabulary.cache_clear()

    def test_lowercase_prose_does_not_ground_to_a_gene(self):
        for word in ("for", "was", "can", "the"):
            with self.subTest(word=word):
                self.assertIsNone(ground(word, vocabulary=self.vocab))

    def test_symbol_shaped_surfaces_still_ground(self):
        self.assertEqual(ground("ERBB2", vocabulary=self.vocab).best.curie, "HGNC:3430")
        self.assertEqual(ground("p53", vocabulary=self.vocab).best.curie, "HGNC:11998")

    def test_the_prose_list_wins_even_in_capitals(self):
        # A deliberate, documented cost: an all-caps WAS in a heading is not the gene.
        self.assertIsNone(ground("WAS", vocabulary=self.vocab))

    def test_longer_lowercase_words_are_guarded_too(self):
        # "particle" is eight characters and an HGNC alias (PARTICL); a length-based guard let it
        # through. Gene symbols are simply never written in lowercase.
        with gzip.open(Path(self._tmp.name) / "hgnc.tsv.gz", "at", encoding="utf-8") as out:
            out.write("HGNC:50886\tPARTICL\tparticle\t\n")
        load_vocabulary.cache_clear()
        vocab = load_vocabulary(self._tmp.name)
        self.assertIsNone(ground("particle", vocabulary=vocab))
        self.assertIsNotNone(ground("PARTICL", vocabulary=vocab))

    def test_multi_word_protein_names_are_not_guarded(self):
        # Names with spaces do appear in lowercase prose and must still ground.
        with gzip.open(Path(self._tmp.name) / "uniprot.tsv.gz", "wt", encoding="utf-8") as out:
            out.write("UniProtKB:P01375\tTumor necrosis factor\tTNF\t\n")
        load_vocabulary.cache_clear()
        vocab = load_vocabulary(self._tmp.name)
        self.assertEqual(ground("tumor necrosis factor", vocabulary=vocab).best.curie,
                         "UniProtKB:P01375")

    def test_the_guard_does_not_touch_non_symbol_vocabularies(self):
        # "water" is lowercase prose-shaped and short, but ChEBI is not a symbol vocabulary.
        self.assertEqual(ground("water", vocabulary=self.vocab).best.curie, "CHEBI:15377")


@unittest.skipUnless((DATA_DIR / "chebi.tsv.gz").exists(),
                     "vendored vocabulary not built; run scripts/build_vocabulary.py")
class VendoredVocabularyTests(unittest.TestCase):
    """Against the real data. Guards the choices made when building it."""

    @classmethod
    def setUpClass(cls):
        load_vocabulary.cache_clear()
        cls.vocab = load_vocabulary()

    def test_every_expected_source_loaded(self):
        self.assertEqual(set(self.vocab.sources),
                         {"hgnc", "mondo", "chebi", "go", "uberon", "cl", "uniprot"})

    def test_biologic_drug_names_are_present(self):
        # These are two-star ChEBI entries. Building at three stars drops them, which would make the
        # vocabulary useless for exactly the formulation literature this was built for.
        for drug in ("trastuzumab", "bevacizumab"):
            with self.subTest(drug=drug):
                self.assertIsNotNone(ground(drug, vocabulary=self.vocab))

    def test_formulation_excipients_ground_to_chebi(self):
        for excipient in ("polysorbate 80", "sucrose", "L-histidine", "trehalose"):
            with self.subTest(excipient=excipient):
                result = ground(excipient, vocabulary=self.vocab)
                self.assertIsNotNone(result)
                self.assertTrue(result.best.curie.startswith("CHEBI:"))

    def test_go_namespaces_survived_the_build(self):
        self.assertEqual(ground("protein folding", vocabulary=self.vocab).best.category,
                         "biolink:BiologicalProcess")
        self.assertEqual(ground("mitochondrion", vocabulary=self.vocab).best.category,
                         "biolink:CellularComponent")

    def test_greek_spelling_reaches_the_same_concept_as_the_word(self):
        self.assertEqual(ground("TNF-α", vocabulary=self.vocab).best.curie,
                         ground("TNF-alpha", vocabulary=self.vocab).best.curie)

    def test_prose_is_still_refused_against_a_million_keys(self):
        # The real risk of a large vocabulary is that everything matches something.
        for term in ("the buffer", "was observed", "in this study", "mAb-X", "our results",
                     "for", "was", "can", "using", "from"):
            with self.subTest(term=term):
                self.assertIsNone(ground(term, vocabulary=self.vocab))


if __name__ == "__main__":
    unittest.main()
