"""Seed scientific ontology — a light controlled vocabulary that TYPES the KG's concept terms.

Turns the lexical co-occurrence graph's concept nodes from bare strings into typed scientific
entities (technique / excipient / property / molecule / condition), so the graph view can group and
colour them by kind — the difference between a term cloud and a typed scientific KG.

It borrows the "ontology-grounded entities" pattern from cognee, implemented as light standard
library code (a curated alias → (canonical, type) map plus conservative fuzzy match) rather than an
LLM-extraction pipeline or a graph database.

**This is a seed, and a narrow one.** The vocabulary is small, hand-written, and biased toward
protein formulation plus general lab methods. It is not a biomedical ontology: grounding to Biolink
categories and OBO CURIEs (MONDO, ChEBI, GO, UBERON, CL, HGNC) is the intended replacement and is
tracked as the next substantial piece of work. Extend `SEED_VOCABULARY` for your own domain in the
meantime. Unknown terms are left untyped, so the graph degrades gracefully on any corpus.
"""

from __future__ import annotations

import difflib
from functools import lru_cache

ONTOLOGY_TYPES = ("technique", "excipient", "property", "molecule", "condition")

# canonical concept -> (type, [surface aliases]). Small + curated; aliases include common plurals /
# hyphenations the graph tokenizer (`graph._terms`, single lowercased tokens) actually produces.
SEED_VOCABULARY: dict[str, tuple[str, list[str]]] = {
    # --- analytical techniques / assays ---
    "size-exclusion chromatography": ("technique", ["sec", "sec-hplc", "sec-uplc"]),
    "dynamic light scattering": ("technique", ["dls"]),
    "high-performance liquid chromatography": ("technique", ["hplc", "uplc", "rp-hplc"]),
    "differential scanning calorimetry": ("technique", ["dsc"]),
    "differential scanning fluorimetry": ("technique", ["dsf", "nanodsf"]),
    "capillary electrophoresis": ("technique", ["ce", "icief", "ce-sds"]),
    "mass spectrometry": ("technique", ["ms", "lc-ms", "maldi", "lc-ms/ms"]),
    "sds-page": ("technique", ["page", "sds"]),
    "elisa": ("technique", ["elisa"]),
    "chromatography": ("technique", ["chromatography"]),
    "spectroscopy": ("technique", ["spectroscopy", "ftir", "cd", "nmr"]),
    "qpcr": ("technique", ["qpcr", "pcr", "rt-pcr", "rt-qpcr"]),
    "microscopy": ("technique", ["microscopy", "sem", "tem"]),
    # --- excipients / buffers / stabilizers / surfactants ---
    "histidine": ("excipient", ["histidine", "his"]),
    "citrate": ("excipient", ["citrate", "citric"]),
    "phosphate": ("excipient", ["phosphate", "pbs"]),
    "acetate": ("excipient", ["acetate", "acetic"]),
    "tris": ("excipient", ["tris"]),
    "arginine": ("excipient", ["arginine", "arg"]),
    "trehalose": ("excipient", ["trehalose"]),
    "sucrose": ("excipient", ["sucrose"]),
    "sorbitol": ("excipient", ["sorbitol"]),
    "mannitol": ("excipient", ["mannitol"]),
    "polysorbate": ("excipient", ["polysorbate", "tween", "ps20", "ps80"]),
    "poloxamer": ("excipient", ["poloxamer"]),
    "sodium chloride": ("excipient", ["nacl"]),
    "methionine": ("excipient", ["methionine"]),
    "edta": ("excipient", ["edta"]),
    # --- measured / stability properties ---
    "aggregation": ("property", ["aggregation", "aggregates", "aggregate", "aggregated"]),
    "viscosity": ("property", ["viscosity", "viscous"]),
    "stability": ("property", ["stability", "stable"]),
    "solubility": ("property", ["solubility", "soluble"]),
    "turbidity": ("property", ["turbidity", "turbid", "opalescence"]),
    "fragmentation": ("property", ["fragmentation", "fragments", "fragment"]),
    "oxidation": ("property", ["oxidation", "oxidized"]),
    "deamidation": ("property", ["deamidation"]),
    "particles": ("property", ["particles", "subvisible", "particulates"]),
    "monomer": ("property", ["monomer", "monomeric"]),
    "purity": ("property", ["purity", "pure"]),
    "potency": ("property", ["potency"]),
    "degradation": ("property", ["degradation", "degraded"]),
    # --- molecules / modalities ---
    "monoclonal antibody": ("molecule", ["mab", "mabs", "antibody", "antibodies", "monoclonal"]),
    "immunoglobulin g": ("molecule", ["igg", "igg1", "igg4"]),
    "protein": ("molecule", ["protein", "proteins"]),
    "peptide": ("molecule", ["peptide", "peptides"]),
    "fusion protein": ("molecule", ["adc", "fab", "fc", "bsa"]),
    # --- conditions / stress factors ---
    "ph": ("condition", ["ph"]),
    "temperature": ("condition", ["temperature", "thermal"]),
    "concentration": ("condition", ["concentration", "concentrated"]),
    "freeze-thaw": ("condition", ["freeze-thaw", "freeze", "thaw", "cryo"]),
    "agitation": ("condition", ["agitation", "shaking", "shear", "stress"]),
    "osmolality": ("condition", ["osmolality", "tonicity"]),
}

# alias (lowercased) -> (canonical, type)
_ALIAS_INDEX: dict[str, tuple[str, str]] = {}
for _canonical, (_etype, _aliases) in SEED_VOCABULARY.items():
    for _a in [_canonical, *_aliases]:
        _ALIAS_INDEX.setdefault(_a.lower(), (_canonical, _etype))
_ALIAS_KEYS = sorted(_ALIAS_INDEX)


def _singular(term: str) -> str:
    """A light singular form for plural matching (aggregates→aggregate); never for -ss words."""
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


@lru_cache(maxsize=4096)
def classify_concept(term: str) -> dict[str, str] | None:
    """Return {"canonical", "type"} for a concept term, or None if it isn't in the seed ontology.

    Deterministic: exact alias → light singular form → a conservative close fuzzy match (guards minor
    variants/typos without merging genuinely distinct terms, e.g. acetate vs citrate stay separate)."""
    if not term:
        return None
    key = term.strip().lower()
    if not key:
        return None
    hit = _ALIAS_INDEX.get(key) or _ALIAS_INDEX.get(_singular(key))
    if hit is None:
        close = difflib.get_close_matches(key, _ALIAS_KEYS, n=1, cutoff=0.92)
        if close:
            hit = _ALIAS_INDEX[close[0]]
    if hit is None:
        return None
    canonical, etype = hit
    return {"canonical": canonical, "type": etype}
