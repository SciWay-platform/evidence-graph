"""Tests for deterministic conflict detection (scripts/app/conflicts.py).

Pins the high-precision contract: a conflict is raised only when the question asks about a
measured label and the top hits report two distinct numeric values for it from distinct chunks.
"""

import unittest

from evidence_graph.conflicts import (
    detect_conflict,
    detect_documented_conflict,
    detect_numeric_conflict,
    detect_numeric_conflict_any,
)
from evidence_graph.evidence import RetrievalHit


def _hit(aid, cid, quote, title=None):
    return RetrievalHit(artifact_id=aid, chunk_id=cid, source_system="upload",
                        title=title or aid, score=1.0, quote=quote, source_uri=aid,
                        artifact_type="note", location="line 1")


class ConflictTests(unittest.TestCase):
    def test_detects_disagreeing_ph_across_documents(self):
        hits = [_hit("d1", "d1::c1", "Histidine buffer at pH 5.5 gave the lowest aggregation."),
                _hit("d2", "d2::c1", "In the repeat the antibody aggregation was lowest at pH 7.0.")]
        c = detect_numeric_conflict("What pH minimised aggregation?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")
        self.assertEqual({v["value"] for v in c["values"]}, {"5.5", "7.0"})
        # provenance preserved on each side of the conflict
        self.assertEqual({v["artifact_id"] for v in c["values"]}, {"d1", "d2"})
        self.assertTrue(all(v["quote"] for v in c["values"]))

    def test_no_conflict_when_values_agree(self):
        hits = [_hit("d1", "d1::c1", "pH 6.0 was chosen."), _hit("d2", "d2::c2", "pH 6.0 confirmed.")]
        self.assertIsNone(detect_numeric_conflict("which pH?", hits))

    def test_no_conflict_when_label_not_in_question(self):
        hits = [_hit("d1", "d1::c1", "pH 5.5 best."), _hit("d2", "d2::c1", "pH 7.0 best.")]
        self.assertIsNone(detect_numeric_conflict("which buffer was selected?", hits))

    def test_no_conflict_for_two_values_in_one_chunk(self):
        hits = [_hit("d1", "d1::c1", "Both pH 5.5 and pH 7.0 were screened in one run.")]
        self.assertIsNone(detect_numeric_conflict("what pH was screened?", hits))

    def test_empty_or_no_hits(self):
        self.assertIsNone(detect_numeric_conflict("anything?", []))
        self.assertIsNone(detect_numeric_conflict("", [_hit("d", "d::c", "pH 5.5 and pH 7.0")]))

    def test_unit_led_temperature_conflict(self):
        # no explicit "temperature" label before the number — the unit (C) maps to the quantity
        hits = [_hit("d1", "d1::c1", "Samples were incubated at 40 C for four weeks."),
                _hit("d2", "d2::c1", "The accelerated arm ran at 25 C instead.")]
        c = detect_numeric_conflict("What temperature was the incubation?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "temperature")
        self.assertEqual({v["value"] for v in c["values"]}, {"40", "25"})

    def test_same_magnitude_different_precision_is_not_a_conflict(self):
        # 5.5 and 5.50 are the same value — must not be flagged as disagreement
        hits = [_hit("d1", "d1::c1", "Buffer set to pH 5.5."), _hit("d2", "d2::c1", "Confirmed at pH 5.50.")]
        self.assertIsNone(detect_numeric_conflict("which pH?", hits))

    def test_unit_conversion_recognizes_equivalent_temperature(self):
        # 40 C == 104 F exactly — must NOT be flagged as a conflict once converted to a common unit
        hits = [_hit("d1", "d1::c1", "Samples were incubated at 40 C for four weeks."),
                _hit("d2", "d2::c1", "The parallel arm was incubated at 104 F instead.")]
        self.assertIsNone(detect_numeric_conflict("What temperature was the incubation?", hits))

    def test_negative_celsius_freezer_temperatures_convert_correctly(self):
        # -20 C (freezer storage, very common lab phrasing) == 253.15 K exactly; the sign must
        # survive extraction (a leading "-" after whitespace is not a \b transition) for the
        # conversion to be correct, and a genuinely different negative temperature must still
        # register as a real conflict.
        hits = [_hit("d1", "d1::c1", "Aliquots were archived at -20 C for long-term storage."),
                _hit("d2", "d2::c1", "The archive freezer was held at 253.15 K.")]
        self.assertIsNone(detect_numeric_conflict("what storage temperature?", hits))

        hits2 = [_hit("d1", "d1::c1", "Aliquots were archived at -20 C for long-term storage."),
                 _hit("d2", "d2::c1", "The archive freezer was held at -80 C.")]
        c = detect_numeric_conflict("what storage temperature?", hits2)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "temperature")
        self.assertEqual({v["value"] for v in c["values"]}, {"-20", "-80"})

    def test_genuine_cross_unit_temperature_conflict_still_flagged(self):
        # 40 C vs 20 C is a real disagreement, not a unit artifact
        hits = [_hit("d1", "d1::c1", "Samples were incubated at 40 C for four weeks."),
                _hit("d2", "d2::c1", "The parallel arm was incubated at 20 C instead.")]
        c = detect_numeric_conflict("What temperature was the incubation?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "temperature")
        self.assertEqual({v["value"] for v in c["values"]}, {"40", "20"})

    def test_mass_and_molar_concentration_are_never_compared(self):
        # mg/mL and mM are not interconvertible without the molecule's molecular weight — must
        # neither be flagged as conflicting NOR silently treated as agreeing.
        hits = [_hit("d1", "d1::c1", "The protein concentration was 150 mg/mL."),
                _hit("d2", "d2::c1", "The protein concentration measured 150 mM.")]
        self.assertIsNone(detect_numeric_conflict("what concentration?", hits))

    def test_bare_concentration_label_cannot_bypass_the_mass_molar_split(self):
        # Regression: the label word "concentration" itself matches the BARE _LABEL_NUM pattern
        # (no unit) in prose that also carries an explicit unit ("Protein concentration was 50
        # mg/mL" matches both the bare label AND the unit-derived "50 mg/mL"), which used to bucket
        # both readings under one ambiguous "concentration" key and defeat the mass/molar split from
        # the previous test whenever the two magnitudes actually differed (the previous test used
        # equal magnitudes — 150 vs 150 — which happened to collapse to one value either way and so
        # didn't catch this). Checked in BOTH the phrasing-independent and question-anchored paths,
        # since the bare-label bypass affected both.
        hits = [_hit("d1", "d1::c1", "Protein concentration was 50 mg/mL."),
                _hit("d2", "d2::c1", "Concentration measured at 0.33 mM.")]
        self.assertIsNone(detect_numeric_conflict_any(hits))
        self.assertIsNone(detect_numeric_conflict("what concentration was used?", hits))

    def test_same_family_concentration_conflict_still_detected_question_anchored(self):
        # Sanity check for the fix above: two DIFFERENT mg/mL readings (same, comparable unit
        # family) must still be flagged when the question anchors on "concentration".
        hits = [_hit("d1", "d1::c1", "Concentration was 50 mg/mL."),
                _hit("d2", "d2::c1", "Concentration was 80 mg/mL.")]
        c = detect_numeric_conflict("what concentration was used?", hits)
        self.assertIsNotNone(c)
        self.assertEqual({v["value"] for v in c["values"]}, {"50", "80"})

    def test_verb_mediated_ph_phrasing_is_detected(self):
        # natural ELN phrasing ("was measured at" / "was recorded as") previously fell outside the
        # single-connective label-number regex and silently produced zero measurements.
        hits = [_hit("d1", "d1::c1", "The pH was measured at 5.5 in the first run."),
                _hit("d2", "d2::c1", "The pH was recorded as 7.0 in the repeat.")]
        c = detect_numeric_conflict("what pH?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")
        self.assertEqual({v["value"] for v in c["values"]}, {"5.5", "7.0"})

    def test_verb_mediated_phrasing_does_not_bridge_across_an_unrelated_number(self):
        # a plausible adversarial sentence: an unrelated number ("3pm"/"4pm") sits between a label
        # and a real measurement, separated by a non-connective word ("and") — the wider connective
        # window must not bridge across it and mis-associate the distractor with "pH".
        hits = [_hit("d1", "d1::c1", "The sample was measured at 3pm and pH 5.5 was recorded."),
                _hit("d2", "d2::c1", "The sample was measured at 4pm and pH 5.5 was confirmed.")]
        # same pH in both -> no conflict; if "3pm"/"4pm" were mis-parsed as a competing pH value
        # (or any bare-allowed quantity) this would incorrectly fire.
        self.assertIsNone(detect_numeric_conflict("what pH?", hits))
        self.assertIsNone(detect_numeric_conflict_any(hits))

        # now make the pH genuinely differ — the real conflict must still be found, and must report
        # exactly the pH values, not the distractor clock times.
        hits2 = [_hit("d1", "d1::c1", "The sample was measured at 3pm and pH 5.5 was recorded."),
                 _hit("d2", "d2::c1", "The sample was measured at 4pm and pH 7.0 was confirmed.")]
        c = detect_numeric_conflict("what pH?", hits2)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")
        self.assertEqual({v["value"] for v in c["values"]}, {"5.5", "7.0"})

    def test_original_label_adjacent_phrasing_unaffected(self):
        hits = [_hit("d1", "d1::c1", "Histidine buffer at pH 5.5 gave the lowest aggregation."),
                _hit("d2", "d2::c1", "In the repeat the antibody aggregation was lowest at pH 7.0.")]
        c = detect_numeric_conflict("What pH minimised aggregation?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")
        self.assertEqual({v["value"] for v in c["values"]}, {"5.5", "7.0"})


class DocumentedConflictTests(unittest.TestCase):
    def test_explicit_contradiction_is_surfaced(self):
        hits = [_hit("d2", "d2::c1",
                     "The round-2 aggregation result disagrees with round 1 for mAb-X.")]
        c = detect_documented_conflict("What did round 2 find about aggregation?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["kind"], "documented")
        self.assertTrue(c["values"][0]["quote"])

    def test_contradiction_cue_off_topic_does_not_trigger(self):
        # the cue is present but unrelated to the question -> no false conflict
        hits = [_hit("d", "d::c", "The shipping vendor disagrees with the invoice total.")]
        self.assertIsNone(detect_documented_conflict("What pH reduced aggregation?", hits))

    def test_plain_negation_is_not_a_documented_conflict(self):
        hits = [_hit("d", "d::c", "Viscosity was not measured in this round.")]
        self.assertIsNone(detect_documented_conflict("what viscosity?", hits))


class PhrasingIndependentConflictTests(unittest.TestCase):
    """The quantity disagrees across cited chunks even when the question does NOT name it."""

    def test_disagreeing_values_flagged_regardless_of_question_wording(self):
        hits = [_hit("d1", "d1::c1", "Histidine buffer at pH 5.5 gave the lowest aggregation."),
                _hit("d2", "d2::c1", "In the repeat aggregation was lowest at pH 7.0.")]
        # question never mentions "pH" — the anchored detector would miss it
        self.assertIsNone(detect_numeric_conflict("summarise the results", hits))
        c = detect_numeric_conflict_any(hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")
        self.assertEqual({v["value"] for v in c["values"]}, {"5.5", "7.0"})
        # and the unified entry point surfaces it too
        self.assertEqual(detect_conflict("summarise the results", hits)["kind"], "numeric")

    def test_agreeing_values_never_flagged(self):
        hits = [_hit("d1", "d1::c1", "pH 6.0 was chosen."), _hit("d2", "d2::c1", "pH 6.0 confirmed.")]
        self.assertIsNone(detect_numeric_conflict_any(hits))
        self.assertIsNone(detect_conflict("anything at all", hits))

    def test_single_chunk_two_values_is_not_a_conflict(self):
        # a comparison inside one chunk is not a disagreement between sources
        hits = [_hit("d1", "d1::c1", "We compared pH 5.5 and pH 7.0 in a single run.")]
        self.assertIsNone(detect_numeric_conflict_any(hits))

    def test_same_magnitude_different_precision_not_flagged(self):
        hits = [_hit("d1", "d1::c1", "Set to pH 5.5."), _hit("d2", "d2::c1", "Confirmed pH 5.50.")]
        self.assertIsNone(detect_numeric_conflict_any(hits))


class DisambiguationGuardTests(unittest.TestCase):
    """Regression for the Q08 false positive (2026-07-02): a chunk that itself reports two values
    for a label AND explicitly says they're for separate purposes must not manufacture a conflict
    against another chunk that reports one of those same values — even in question-anchored mode,
    where the plain same-chunk-sweep guard (see PhrasingIndependentConflictTests above) does not
    apply. A genuine debate between two values in one chunk (no disambiguation cue) must still be
    flagged."""

    def test_disambiguated_sweep_does_not_create_a_question_anchored_conflict(self):
        hits = [
            _hit("dec", "dec::c1",
                 "The team adopted histidine buffer pH 6.0 as the lead formulation buffer."),
            _hit("dec", "dec::c2",
                 "The histidine pH 6.0 formulation decision is separate from the aggregation "
                 "assay baseline. pH 7.0 fluorescence remains the onboarding assay baseline; it "
                 "is not used as the formulation condition."),
        ]
        self.assertIsNone(detect_numeric_conflict("Why was pH 6.0 adopted as the buffer?", hits))
        self.assertIsNone(detect_conflict("Why was pH 6.0 adopted as the buffer?", hits))

    def test_disambiguated_sweep_does_not_create_a_phrasing_independent_conflict_either(self):
        hits = [
            _hit("dec", "dec::c1", "The team adopted histidine buffer pH 6.0."),
            _hit("dec", "dec::c2",
                 "This pH 6.0 decision is separate from the pH 7.0 assay baseline."),
        ]
        self.assertIsNone(detect_numeric_conflict_any(hits))

    def test_undisambiguated_debate_within_one_chunk_is_still_a_conflict(self):
        # same shape (one chunk sweeps 2 values, a second chunk repeats one of them) but no
        # disambiguation cue — a genuine unresolved debate, must still be flagged.
        hits = [
            _hit("email", "email::c1", "Repeat pH 6.5 or move to pH 7.0 as the new baseline?"),
            _hit("lit", "lit::c1", "The literature recommends pH 8.0 for this protein family."),
        ]
        c = detect_numeric_conflict("Should pH 6.5 be the baseline?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")

    def test_disambiguation_cue_alone_without_a_sweep_does_not_suppress_a_real_conflict(self):
        # the disambiguation phrase appears, but this chunk only asserts ONE value itself — the
        # guard only fires for a chunk that sweeps >=2 values AND disambiguates, so a genuine
        # cross-chunk disagreement elsewhere must still be caught.
        hits = [
            _hit("d1", "d1::c1", "Buffer choice is separate from assay conditions; pH was 5.5."),
            _hit("d2", "d2::c1", "In the repeat, pH was 7.0."),
        ]
        c = detect_numeric_conflict("What pH was used?", hits)
        self.assertIsNotNone(c)
        self.assertEqual(c["label"], "ph")


class UnifiedConflictTests(unittest.TestCase):
    def test_prefers_numeric_then_documented(self):
        numeric = [_hit("d1", "d1::c1", "lowest at pH 5.5"), _hit("d2", "d2::c1", "lowest at pH 6.5")]
        self.assertEqual(detect_conflict("which pH?", numeric)["kind"], "numeric")
        documented = [_hit("d2", "d2::c1", "round 2 contradicts round 1 on aggregation")]
        self.assertEqual(detect_conflict("round 2 aggregation?", documented)["kind"], "documented")
        self.assertIsNone(detect_conflict("which buffer?", [_hit("d", "d::c", "histidine was used")]))


if __name__ == "__main__":
    unittest.main()
