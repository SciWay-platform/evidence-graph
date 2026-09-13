"""Conservative, deterministic conflict detection over a project's retrieved evidence.

Surfacing disagreement matters as much as answering. Where a curated relations file exists, the
contradictions are already recorded; over an arbitrary corpus there is no such file, so this module
detects the two least-ambiguous kinds of disagreement directly from the cited chunks:

  1. NUMERIC — the top evidence reports DIFFERENT numeric values for the SAME measured quantity
     (a label like "pH" / "temperature" / a unit like "mg/mL"). Two modes:
       • question-anchored — the quantity also appears in the question (highest precision), and
       • phrasing-independent — the quantity disagrees across cited chunks REGARDLESS of how the
         question is worded (so "summarise the results" still surfaces a pH 5.5 vs 7.0 clash).
     Phrasing-independent detection raises recall without hurting precision: agreeing values are
     never flagged, and a quantity named by only one chunk is never a conflict.
  2. DOCUMENTED — a top, on-topic chunk explicitly says it disagrees with / contradicts / could
     not reproduce another result (the lab memory flags its own conflict).

Both are deliberately high-precision — better to miss a fuzzy conflict than to cry wolf, since a
detector that flags noise gets switched off and then catches nothing. Pure lexical/numeric, no model, fully unit-testable,
and we never fabricate a value (every reported number/quote comes from a cited chunk).
"""

from __future__ import annotations

import re
from typing import Any

_NUM = r"-?\d+(?:\.\d+)?"
# Label immediately preceding a number, via zero-to-three connective/verb words — "pH 5.5", "pH of
# 7.0", "temperature = 40", "pH was measured at 5.5", "pH was recorded as 7.0". Label is an
# alphabetic token (keeps unit chars like %). The connective vocabulary is deliberately small and
# curated (not e.g. "\w+") so this can't drift across an unrelated clause onto an unrelated number.
_CONNECTIVE = r"(?:of|at|as|=|:|~|was|is|were|are|measured|recorded|observed|found|determined|reported|around|about|to|be)"
_LABEL_NUM = re.compile(
    r"\b([A-Za-z][A-Za-z%]{1,20})\s*(?:" + _CONNECTIVE + r"\s+){0,3}(" + _NUM + r")\b",
    re.IGNORECASE,
)
# Number followed by a unit — "40 C", "40°C", "150 mg/mL", "25 mM", "-20 C". The unit maps to a
# quantity name so a question about "temperature" / "concentration" matches the unit in the
# evidence. Uses a negative lookbehind (not \b) immediately before the optional sign: a leading \b
# there would require a word-char/non-word-char transition right at the "-", which a preceding space
# never satisfies (space and "-" are both non-word) — silently stripping the sign off every negative
# reading (common for Celsius freezer temps, "-20 C"/"-80 C"). The lookbehind also correctly refuses
# to read the tail of a hyphenated identifier as a negative number ("lot-5" must not parse as "-5").
_NUM_UNIT = re.compile(
    r"(?<![A-Za-z0-9.])(" + _NUM + r")\s*(°?[A-Za-zµ%]{1,6}(?:/[A-Za-z]{1,4})?)", re.IGNORECASE
)
# Mass-concentration (mg/mL, µg/mL, ng/mL) and molar-concentration (mM, µM, nM) are NOT
# interconvertible without the molecule's molecular weight, which this library does not have — so they
# are kept as two distinct quantity buckets rather than one. Values in different buckets are simply
# never compared (neither flagged as conflicting nor treated as agreeing); values WITHIN a bucket
# get real unit conversion (see _UNIT_CONVERSION), since metric-prefix scaling is parameter-free.
_UNIT_QUANTITY = {
    "c": "temperature", "°c": "temperature", "celsius": "temperature", "k": "temperature",
    "°f": "temperature", "f": "temperature",
    "mg/ml": "concentration_mass", "ng/ml": "concentration_mass",
    "µg/ml": "concentration_mass", "ug/ml": "concentration_mass",
    "mm": "concentration_molar", "µm": "concentration_molar", "um": "concentration_molar",
    "nm": "concentration_molar",
    "%": "percentage", "mpa": "pressure", "cp": "viscosity", "mosm": "osmolality", "kda": "mass",
}
# Per-quantity, per-unit conversion to the bucket's canonical unit (temperature -> Celsius,
# concentration_mass -> mg/mL, concentration_molar -> mM). Only closed-form, parameter-free
# conversions (metric-prefix scaling, the linear C/F/K relations) are included — anything that would
# need external domain data (e.g. molecular weight) is intentionally left unconverted (see above).
_UNIT_CONVERSION: dict[str, dict[str, Any]] = {
    "temperature": {
        "c": lambda v: v, "°c": lambda v: v, "celsius": lambda v: v,
        "k": lambda v: v - 273.15,
        "f": lambda v: (v - 32) * 5 / 9, "°f": lambda v: (v - 32) * 5 / 9,
    },
    "concentration_mass": {
        "mg/ml": lambda v: v,
        "µg/ml": lambda v: v / 1000, "ug/ml": lambda v: v / 1000,
        "ng/ml": lambda v: v / 1_000_000,
    },
    "concentration_molar": {
        "mm": lambda v: v,
        "µm": lambda v: v / 1000, "um": lambda v: v / 1000,
        "nm": lambda v: v / 1_000_000,
    },
}


def _canonical_value(quantity: str, unit: str, value: float) -> float:
    """Convert `value` (expressed in `unit`) to `quantity`'s canonical unit for comparison purposes.
    `unit` == "" means the measurement came from a bare label match with no explicit unit token
    (e.g. "pH 5.5") — there is nothing to convert from, so the value is returned unchanged."""
    if not unit:
        return value
    fn = _UNIT_CONVERSION.get(quantity, {}).get(unit)
    return fn(value) if fn else value


def _question_matches_quantity(quantity: str, unit: str, q_tokens: set[str]) -> bool:
    """True if a unit-derived (quantity, unit) pair is anchored by the question's tokens. Handles
    the concentration_mass/concentration_molar split transparently: a question that says the
    generic word "concentration" (not knowing/caring about mass-vs-molar, a unit-driven distinction
    the question wording won't make) still anchors either subtype."""
    if quantity in q_tokens or unit in q_tokens:
        return True
    if quantity.startswith("concentration") and "concentration" in q_tokens:
        return True
    return False


_WORD = re.compile(r"[a-z0-9]+")
# Tokens that are almost always connective noise rather than a measured quantity.
#
# Widened after a real corpus produced "quantities" called `pmc`, `igg`, `after`, `than` and `mabs`:
# any word followed by a number matches the bare-label grammar, so identifiers (PMC12345), isotype
# names (IgG1) and ordinary prose all parsed as measurements and then linked unrelated documents.
_LABEL_STOP = frozenset(
    "the a an and or of to in at on for was were is are be been by from with as it this that "
    "about around over under up down day days week weeks year years no not n round table figure fig "
    # prose connectives that precede a number
    "than then when while after before during since until between among within without across "
    "more less most least above below up to approximately approx circa about nearly almost only "
    "all both each either neither such same other another following above-mentioned respectively "
    # bibliographic and identifier prefixes
    "pmc pmid doi isbn issn vol volume no issue page pages pp fig figs eq eqs ref refs chapter "
    "et al ibid supplementary supplement appendix "
    # antibody/biologic naming that is an identity, not a measurement
    "igg iga igm ige igd fab fc mab mabs adc scfv cho hek".split()
)
_QSTOP = frozenset(
    "the a an and or of to in at on for was were is are be what which who how why when where did do "
    "does with this that as by from".split()
)
# Bare labels (no unit) that are safe to treat as a measured quantity WITHOUT a question to anchor
# them. Question-anchored detection can trust any label the user named; phrasing-independent
# detection must not, or alphanumeric noise (lot codes "PB65", ids "EXP082") parses as a quantity.
# Kept deliberately tiny + domain-obvious; unit-derived quantities (from _UNIT_QUANTITY) are always
# allowed since a real unit already vouches for them.
_BARE_QUANTITY_ALLOW = frozenset({"ph", "temperature", "concentration", "viscosity", "osmolality"})

# Labels whose bare (no-unit) mention is inherently ambiguous once split into unit-incompatible
# sub-buckets — right now just "concentration" (mass-concentration and molar-concentration are NOT
# interconvertible without the molecule's molecular weight, see _UNIT_CONVERSION below). A bare
# "concentration was 50" tells us nothing about which family it belongs to, so it must never create
# a measurement at all — not even when question-anchored ("what concentration was used?" still can't
# tell a 50 mg/mL mention from a 0.33 mM mention apart without a unit). Without this guard, a bare
# mention silently defeats the split by bucketing mass- and molar-concentration readings together
# under one ambiguous "concentration" key whenever the label word itself precedes the number in
# prose that ALSO carries an explicit unit — which is the common case ("Protein concentration was
# 50 mg/mL" matches both the bare label AND the unit). Reproduced and fixed 2026-07-02.
_AMBIGUOUS_BARE_LABELS = frozenset({"concentration"})

# The lab memory explicitly states a disagreement. High-precision cues only (not generic negation).
_CONTRADICTION_RE = re.compile(
    r"\b(?:disagree(?:s|d)?\s+with|contradict(?:s|ed|ory)?|in\s+contrast\s+to|"
    r"conflict(?:s|ed)?\s+with|could\s+not\s+(?:be\s+)?(?:reproduce|replicate)d?|"
    r"failed\s+to\s+(?:reproduce|replicate)|inconsistent\s+with|"
    r"(?:does|did)\s+not\s+(?:match|agree|reproduce)|at\s+odds\s+with)\b",
    re.IGNORECASE,
)

# The mirror-image cue: the lab memory explicitly states that two values it itself mentions are
# NOT competing (different purpose/condition), e.g. a decision log clarifying a formulation buffer
# pH is unrelated to an assay-baseline pH. High-precision, curated vocabulary only — this must
# never fire on a genuine debate between two options (e.g. "repeat pH 6.5 or move to pH 7.0?"),
# only on an explicit disambiguation.
_DISAMBIGUATION_RE = re.compile(
    r"\b(?:(?:is|are)\s+separate\s+from|separate\s+from|(?:is|are)\s+different\s+from|"
    r"not\s+the\s+same\s+as|(?:is|are)\s+not\s+used\s+as|not\s+used\s+as|"
    r"for\s+a\s+different\s+purpose|unrelated\s+to|distinct\s+from)\b",
    re.IGNORECASE,
)


def _question_tokens(question: str) -> set[str]:
    return set(_WORD.findall((question or "").lower()))


def _content_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if t not in _QSTOP and len(t) > 2}


def _hit_text(hit: Any) -> str:
    for attr in ("quote", "source_quote", "chunk_text"):
        v = getattr(hit, attr, None) if not isinstance(hit, dict) else hit.get(attr)
        if v:
            return str(v)
    return ""


def _hit_field(hit: Any, name: str, default: str = "") -> str:
    v = getattr(hit, name, None) if not isinstance(hit, dict) else hit.get(name)
    return str(v) if v not in (None, "") else default


def _citation(hit: Any, value: str = "") -> dict:
    return {
        "value": value,
        "artifact_id": _hit_field(hit, "artifact_id"),
        "artifact_title": _hit_field(hit, "title"),
        "chunk_id": _hit_field(hit, "chunk_id"),
        "location": _hit_field(hit, "location"),
        "quote": _hit_text(hit),
    }


def _measurements(text: str, q_tokens: set[str] | None) -> list[tuple[str, str, float, str]]:
    """(label, display_value, float_value, unit) measurements in `text`. `unit` is "" for a bare
    label match (no explicit unit token in the text — e.g. "pH 5.5" — so there is nothing to
    convert) and the matched unit string (lowercased) for a _NUM_UNIT match (e.g. "c", "mg/ml").
    When `q_tokens` is a set, only quantities whose label/unit appears in the question are kept
    (question-anchored). When `q_tokens` is None, ALL measured quantities are returned
    (phrasing-independent)."""
    out: list[tuple[str, str, float, str]] = []
    for raw_label, value in _LABEL_NUM.findall(text):
        label = raw_label.lower()
        if label in _LABEL_STOP:
            continue
        if label in _AMBIGUOUS_BARE_LABELS:
            # No unit -> can't tell which incompatible sub-bucket this belongs to; skip in BOTH the
            # question-anchored and phrasing-independent paths (see _AMBIGUOUS_BARE_LABELS above).
            continue
        if q_tokens is None:
            # phrasing-independent: only trust a curated set of bare labels (no question to anchor)
            if label not in _BARE_QUANTITY_ALLOW:
                continue
        elif label not in q_tokens:
            continue
        try:
            out.append((label, value, float(value), ""))
        except ValueError:
            continue
    for value, raw_unit in _NUM_UNIT.findall(text):
        unit = raw_unit.lower()
        quantity = _UNIT_QUANTITY.get(unit)
        if not quantity:
            continue
        if q_tokens is not None and not _question_matches_quantity(quantity, unit, q_tokens):
            continue
        try:
            out.append((quantity, value, float(value), unit))
        except ValueError:
            continue
    return out


def _numeric_conflict(hits: list[Any], q_tokens: set[str] | None, top_n: int) -> dict | None:
    """Core numeric-conflict scan. `q_tokens=None` -> phrasing-independent (scan every quantity);
    a set -> only quantities named in the question. Requires >=2 distinct values for a quantity from
    >=2 distinct chunks (magnitude-deduped, 5.5==5.50). Returns the widest disagreement or None."""
    if not hits:
        return None
    # label -> { rounded_float -> (display_value, hit) } (first chunk that reported each magnitude)
    by_label: dict[str, dict[float, tuple[str, Any]]] = {}
    # label -> set of labels that a SINGLE chunk reported with >=2 distinct values (a sweep/range,
    # e.g. "pH 5.0 to 7.5"). Such a quantity is being varied, not asserted, so — for the phrasing-
    # independent path — it must not be read as a competing claim against another chunk's value.
    swept: set[str] = set()
    for hit in hits[:top_n]:
        text = _hit_text(hit)
        # A chunk that BOTH sweeps a label (reports >=2 distinct values itself) AND explicitly
        # disambiguates them ("is separate from", "not used as", ...) is asserting they're for
        # different purposes/conditions, not competing claims — e.g. a decision log clarifying a
        # formulation-buffer pH is unrelated to an assay-baseline pH mentioned in the same
        # paragraph. Guard this regardless of question-anchored vs phrasing-independent mode (unlike
        # the bare `swept` guard below, which only applies phrasing-independent — see its comment).
        disambiguated = bool(_DISAMBIGUATION_RE.search(text))
        per_chunk: dict[str, set[float]] = {}
        pending: dict[str, dict[float, tuple[str, Any]]] = {}
        for label, display, fval, unit in _measurements(text, q_tokens):
            # Compare/dedupe on the CANONICAL (unit-converted) magnitude — e.g. 40 C and 104 F both
            # canonicalize to 40.0 and are recognized as the same value — while `display` keeps the
            # original as-cited string ("104 F") for the summary/citation shown to the user.
            r = round(_canonical_value(label, unit, fval), 4)
            per_chunk.setdefault(label, set()).add(r)
            pending.setdefault(label, {}).setdefault(r, (display, hit))
        for label, vals in per_chunk.items():
            if len(vals) >= 2:
                swept.add(label)
                if disambiguated:
                    continue   # don't let this chunk contribute ANY value for `label`
            for r, entry in pending[label].items():
                by_label.setdefault(label, {}).setdefault(r, entry)

    best: tuple[str, dict[float, tuple[str, Any]]] | None = None
    for label in sorted(by_label):
        # A quantity swept within a single chunk is a range/comparison, not a conflicting claim.
        # Only guard the phrasing-independent path (q_tokens is None); a question-anchored ask keeps
        # its original, higher-recall behavior.
        if q_tokens is None and label in swept:
            continue
        values = by_label[label]
        if len(values) < 2:
            continue
        chunk_ids = {_hit_field(h, "chunk_id") for _d, h in values.values()}
        if len(chunk_ids) < 2:            # one chunk naming two values = a comparison, not a conflict
            continue
        # Prefer the widest disagreement; tie-break lexicographically for determinism.
        if best is None or (len(values), label) > (len(best[1]), best[0]):
            best = (label, values)

    if best is None:
        return None
    label, values = best
    ordered = sorted(values.items(), key=lambda kv: kv[0])
    out_values = [_citation(hit, display) for _f, (display, hit) in ordered]
    shown = " vs ".join(v["value"] for v in out_values)
    return {
        "kind": "numeric",
        "label": label,
        "summary": f"the cited sources report different {label} values ({shown})",
        "values": out_values,
    }


def detect_numeric_conflict(question: str, hits: list[Any], top_n: int = 5) -> dict | None:
    """Conflict descriptor if the top hits disagree on a QUESTION-RELEVANT measured value, else None
    (highest precision — the quantity must be named in the question)."""
    q_tokens = _question_tokens(question)
    if not q_tokens:
        return None
    return _numeric_conflict(hits, q_tokens, top_n)


def detect_numeric_conflict_any(hits: list[Any], top_n: int = 2) -> dict | None:
    """PHRASING-INDEPENDENT: conflict descriptor if the TOP cited chunks report different values for
    ANY single measured quantity, regardless of how (or whether) the question names it. Same
    high-precision core (>=2 distinct magnitudes from >=2 distinct chunks); agreeing values never
    fire. This raises conflict recall on questions like 'summarise the results'.

    Precision guard: only the top `top_n` (=2) hits are scanned — the disagreement must be between
    the two MOST relevant chunks, so a stray number deep in the result list can't hijack an
    otherwise-supported answer (a lower-ranked tangential quantity is not the subject)."""
    return _numeric_conflict(hits, None, top_n)


def detect_documented_conflict(question: str, hits: list[Any], top_n: int = 3) -> dict | None:
    """Conflict descriptor when a top, on-topic chunk explicitly flags a disagreement (the lab
    memory contradicts itself), else None. Requires shared content with the question so an unrelated
    'contradicts' sentence can't trigger it."""
    if not hits:
        return None
    q_content = _content_tokens(question)
    if not q_content:
        return None
    for hit in hits[:top_n]:
        text = _hit_text(hit)
        if _CONTRADICTION_RE.search(text) and (q_content & _content_tokens(text)):
            return {
                "kind": "documented",
                "label": "disagreement flagged in the evidence",
                "summary": "the cited evidence explicitly flags a disagreement between results",
                "values": [_citation(hit)],
            }
    return None


def detect_conflict(question: str, hits: list[Any]) -> dict | None:
    """Unified entry point. Precedence (precision → recall):
      1. question-anchored numeric disagreement (the quantity is named in the question),
      2. a documented disagreement (a chunk explicitly flags it),
      3. PHRASING-INDEPENDENT numeric disagreement (the cited chunks disagree on a quantity even
         when the question doesn't name it) — surfaces conflicts the question wording would miss.
    All three are high-precision (agreeing values never fire)."""
    return (
        detect_numeric_conflict(question, hits)
        or detect_documented_conflict(question, hits)
        or detect_numeric_conflict_any(hits)
    )
