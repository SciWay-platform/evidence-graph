"""Grounding: turning a string found in text into a biomedical identifier, or declining to.

`ontology.py` types a term against a small hand-written vocabulary. This module does the real job:
it resolves a surface string to a CURIE from MONDO, ChEBI, GO, UBERON, CL, HGNC or UniProt, and
attaches the Biolink category that CURIE implies.

Three commitments shape the design.

**It refuses.** Most strings in a paper are not ontology terms, and a grounder that always returns
its closest match is a machine for producing confident nonsense. `ground` returns None when nothing
clears the bar, and `ground_all` reports what it could not place in `unmapped` rather than dropping
it. An unmapped term is information: it is usually either a genuinely novel entity or a sign the
vocabulary needs extending.

**Ambiguity is reported, not resolved by coin-flip.** A surface form that matches several CURIEs
comes back with all of them and `ambiguous=True`. "Aggregation" matching five GO terms is a fact
about the corpus, and picking the alphabetically first one would hide it.

**Short symbols are guarded by their surface form.** Gene symbols collide violently with English:
WAS, CAN, FOR and SET are all approved HGNC symbols. Because the index is lowercased, a short
alphabetic match against a gene or protein vocabulary is only accepted when the surface actually
looks like a symbol — capitals, a digit, or mixed case. Without this the grounder reports "for" as
the gene WWOX, which it did on the first corpus it was pointed at.

**Matching is exact after normalisation, and nothing else.** Case, whitespace, punctuation and
Greek-letter spellings are normalised; beyond that a string either matches a label or an EXACT
synonym, or it does not. There is no edit-distance fallback, because fuzzy matching at ontology
scale reliably grounds "IL-6" to "IL-16" and similar, and a wrong identifier is far more damaging
than a missing one — it is a citation that looks checkable and is not.

Data lives in `evidence_graph/data/*.tsv.gz`, built by `scripts/build_vocabulary.py` and read with
stdlib gzip. Around 6 MB compressed, loaded lazily on first use and cached. Sources and licences
are recorded in `evidence_graph/data/NOTICE`.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from evidence_graph.biolink import DEFAULT_CATEGORY, category_for_curie

DATA_DIR = Path(__file__).resolve().parent / "data"

#: Vocabulary files, in the order a tie is broken. Earlier sources win: a string that is both a gene
#: symbol and a chemical name is far more often the gene, and disease naming is more standardised
#: than chemical naming. The order is a documented convention, not a claim of truth — a tie still
#: comes back flagged `ambiguous`.
VOCABULARIES = ("hgnc", "mondo", "chebi", "go", "uberon", "cl", "uniprot")

#: Shortest string that may be grounded. Two-character tokens are overwhelmingly units, symbols or
#: abbreviations, and grounding them produces noise ("CL" is a real gene symbol and also a unit).
MIN_TERM_LENGTH = 3

#: Vocabularies whose entries are SYMBOLS rather than prose names. Gene and protein symbols collide
#: violently with ordinary English once case is normalised away: WAS, CAN, FOR, SET, ATM, PIGS and
#: hundreds of others are approved gene symbols. Matching them against lowercase prose produced
#: "for -> HGNC:12799 WWOX" on the first real corpus.
SYMBOL_SOURCES = frozenset({"hgnc", "uniprot"})

#: The guard applies to SINGLE-TOKEN matches only. A gene symbol is one token and is never written
#: in lowercase in a paper, so a lowercase single word matching a symbol vocabulary is prose. Names
#: with spaces ("tumor necrosis factor") are left alone, since those genuinely do appear lowercase.

#: Words that are never a term, whatever an ontology says, and whatever case they are written in.
#: Kept short and obviously safe: these are function words, not science. The list deliberately wins
#: over the symbol guard below, so an all-caps "WAS" in a heading does not become the
#: Wiskott-Aldrich gene. That costs a real match occasionally and prevents a wrong citation always,
#: which is the trade this library makes everywhere else too.
_PROSE = frozenset(
    "the a an and or but if then else of to in on at by for with from into over under as is are was "
    "were be been being do does did has have had not no nor so than too very can will would should "
    "could may might must this that these those it its they them we our you your all any both each "
    "few more most other some such only own same out up down off above below again further once here "
    "there when where why how also using used use one two three set can't cannot per via vs".split()
)


def _looks_like_symbol(surface: str) -> bool:
    """Does the surface form look like a gene/protein symbol rather than a word?

    Symbols are written in caps (ERBB2), or carry a digit (p53), or are mixed-case (mTOR). Ordinary
    prose is none of those, which is exactly the discrimination the lowercased index throws away.
    """
    text = (surface or "").strip()
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if any(c.isdigit() for c in text):
        return True
    if all(c.isupper() for c in letters):
        return True
    return not text.islower() and not text.istitle()

# Greek letters appear spelled out, as symbols, and as Latin lookalikes across sources.
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "µ": "mu", "σ": "sigma", "ω": "omega", "τ": "tau", "ζ": "zeta",
}
_PUNCT = re.compile(r"[\s\-_/(),.;:'\"\[\]{}]+")


def normalize_term(term: str) -> str:
    """Fold a surface string to its lookup key.

    Unicode is NFKD-normalised, Greek letters are spelled out, punctuation and whitespace collapse
    to single spaces, and the result is lowercased. `IL-6`, `IL 6` and `il6` all land on `il 6`;
    `TNF-α` and `TNF-alpha` both land on `tnf alpha`.
    """
    text = unicodedata.normalize("NFKD", term or "")
    text = "".join(_GREEK.get(ch, ch) for ch in text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _PUNCT.sub(" ", text).strip().lower()


def compact_term(term: str) -> str:
    """The space-free fallback key: `IL-6`, `IL 6` and `IL6` all reach `il6`.

    Papers and ontologies disagree about whether a symbol and its number are one token, so an exact
    key alone misses matches everyone would agree are the same term. This is a SECOND-CHANCE key,
    consulted only when the exact key misses, so collapsing a word boundary can never override a
    genuine exact match.
    """
    return normalize_term(term).replace(" ", "")


@dataclass(frozen=True)
class Concept:
    """One grounded identifier."""

    curie: str
    label: str
    category: str
    source: str
    #: True when the surface form matched a synonym rather than the primary label.
    via_synonym: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"curie": self.curie, "label": self.label, "category": self.category,
                "source": self.source, "via_synonym": self.via_synonym}


@dataclass(frozen=True)
class Grounding:
    """The result of grounding one surface string."""

    surface: str
    concepts: tuple[Concept, ...]

    @property
    def best(self) -> Concept:
        """The preferred concept. Only meaningful when `ambiguous` is False."""
        return self.concepts[0]

    @property
    def ambiguous(self) -> bool:
        return len(self.concepts) > 1

    def as_dict(self) -> dict[str, Any]:
        return {"surface": self.surface, "ambiguous": self.ambiguous,
                "concepts": [c.as_dict() for c in self.concepts]}


@dataclass
class Vocabulary:
    """The loaded term dictionaries. Built once and cached; see `load_vocabulary`."""

    #: normalised term -> [(curie, label, source, via_synonym, qualifier), ...]
    index: dict[str, list[tuple[str, str, str, bool, str]]] = field(default_factory=dict)
    #: space-free variant of the same, consulted only when `index` misses. See `compact_term`.
    compact: dict[str, list[tuple[str, str, str, bool, str]]] = field(default_factory=dict)
    #: obsolete or alternate CURIE -> current CURIE
    redirects: dict[str, str] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.index)


def _read(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [line.rstrip("\n").split("\t") for line in handle if line.strip()]


@lru_cache(maxsize=4)
def load_vocabulary(data_dir: str | None = None, sources: tuple[str, ...] = VOCABULARIES) -> Vocabulary:
    """Load the vendored dictionaries. Cached, so the cost is paid once per process.

    Missing files are skipped rather than raising: a checkout that has not run
    `scripts/build_vocabulary.py` should degrade to grounding nothing, not crash. `loaded_sources`
    on the result says what was actually available.
    """
    root = Path(data_dir) if data_dir else DATA_DIR
    index: dict[str, list[tuple[str, str, str, bool, str]]] = {}
    compact: dict[str, list[tuple[str, str, str, bool, str]]] = {}
    loaded: list[str] = []

    for source in sources:
        path = root / f"{source}.tsv.gz"
        if not path.exists():
            continue
        loaded.append(source)
        for row in _read(path):
            if len(row) < 2:
                continue
            curie, label = row[0], row[1]
            synonyms = row[2].split("|") if len(row) > 2 and row[2] else []
            # 4th column: a source-specific qualifier. For GO it is the term namespace, without
            # which a GO CURIE cannot be typed — the prefix is shared by five Biolink categories.
            qualifier = row[3] if len(row) > 3 else ""
            for surface, is_synonym in [(label, False), *((s, True) for s in synonyms)]:
                key = normalize_term(surface)
                if len(key) < MIN_TERM_LENGTH:
                    continue
                entry = (curie, label, source, is_synonym, qualifier)
                entries = index.setdefault(key, [])
                if not any(e[0] == curie for e in entries):
                    entries.append(entry)
                squeezed = key.replace(" ", "")
                if squeezed != key and len(squeezed) >= MIN_TERM_LENGTH:
                    fallback = compact.setdefault(squeezed, [])
                    if not any(e[0] == curie for e in fallback):
                        fallback.append(entry)

    redirects: dict[str, str] = {}
    redirect_path = root / "redirects.tsv.gz"
    if redirect_path.exists():
        redirects = {row[0]: row[1] for row in _read(redirect_path) if len(row) >= 2}

    return Vocabulary(index=index, compact=compact, redirects=redirects, sources=tuple(loaded))


def _rank(entry: tuple[str, str, str, bool, str]) -> tuple[int, int, str]:
    """Preference order: primary labels before synonyms, then vocabulary order, then CURIE."""
    curie, _label, source, via_synonym, _qualifier = entry
    position = VOCABULARIES.index(source) if source in VOCABULARIES else len(VOCABULARIES)
    return (int(via_synonym), position, curie)


def ground(term: str, *, vocabulary: Vocabulary | None = None) -> Grounding | None:
    """Ground one surface string, or return None when nothing matches.

    None is a normal, frequent, correct outcome. Callers that treat it as an error will end up
    forcing matches, which is the failure this module is built to avoid.
    """
    vocab = load_vocabulary() if vocabulary is None else vocabulary
    key = normalize_term(term)
    if len(key) < MIN_TERM_LENGTH:
        return None
    if key in _PROSE:
        return None
    entries = vocab.index.get(key)
    if not entries:
        # Second chance on the space-free key, so IL-6 finds a vocabulary that spells it IL6.
        entries = vocab.compact.get(compact_term(term))
    if not entries:
        return None

    # Drop symbol-vocabulary matches when the surface does not look like a symbol. Without this a
    # lowercased index grounds ordinary English to genes that happen to share the spelling:
    # "for" to WWOX, "particle" to PARTICL.
    if not _looks_like_symbol(term) and key.isalpha():
        entries = [e for e in entries if e[2] not in SYMBOL_SOURCES]
    if not entries:
        return None

    concepts: list[Concept] = []
    for curie, label, source, via_synonym, qualifier in sorted(entries, key=_rank):
        resolved = vocab.redirects.get(curie, curie)
        category = category_for_curie(resolved, go_namespace=qualifier) or DEFAULT_CATEGORY
        concepts.append(Concept(resolved, label, category, source, via_synonym))
    return Grounding(surface=term.strip(), concepts=tuple(concepts))


@dataclass(frozen=True)
class GroundingReport:
    """What grounding a set of terms produced, including what it could not place."""

    grounded: tuple[Grounding, ...]
    unmapped: tuple[str, ...]

    @property
    def ambiguous(self) -> tuple[Grounding, ...]:
        return tuple(g for g in self.grounded if g.ambiguous)

    @property
    def coverage(self) -> float:
        total = len(self.grounded) + len(self.unmapped)
        return len(self.grounded) / total if total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "terms": len(self.grounded) + len(self.unmapped),
            "grounded": len(self.grounded),
            "ambiguous": len(self.ambiguous),
            "unmapped": len(self.unmapped),
            "coverage": round(self.coverage, 4),
        }


def ground_all(terms: list[str], *, vocabulary: Vocabulary | None = None) -> GroundingReport:
    """Ground many terms at once, keeping the ones that failed.

    `unmapped` is the part worth looking at. On a real corpus it is where the novel entities and the
    vocabulary gaps both show up, and a pipeline that discards it learns nothing from either.
    """
    vocab = load_vocabulary() if vocabulary is None else vocabulary
    grounded: list[Grounding] = []
    unmapped: list[str] = []
    for term in terms:
        result = ground(term, vocabulary=vocab)
        if result is None:
            unmapped.append(term)
        else:
            grounded.append(result)
    return GroundingReport(tuple(grounded), tuple(unmapped))
