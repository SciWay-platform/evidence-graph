"""Deterministic, stdlib-only typed-relation extraction over a project's chunks.

`graph.py` gives honest lexical co-occurrence. This module adds typed structure WITHOUT an LLM or a
new dependency: it reads regex-detectable signals out of the chunks and emits TYPED edges between
artifacts, using the vocabulary a hand-curated relations file would use:

  • `contradicts`   — two artifacts report DIFFERENT values for the same measured quantity
                      (unit-aware; e.g. pH 5.5 vs pH 7.0), or a chunk explicitly flags a
                      disagreement / failed reproduction.
  • `supports`      — two artifacts report the SAME (agreeing) value for a measured quantity, or a
                      chunk explicitly says it reproduces / confirms / is consistent with another.
  • `decided`       — an artifact records a decision (chose / selected / concluded / approved /
                      recommended) about a measured quantity → edge to the artifact(s) that report
                      the value it decided on.
  • `uses-protocol` — an artifact references an SOP / protocol → edge to the artifact whose title
                      or id names that protocol.

Every edge carries provenance (`evidence`: the artifact ids + a short quote + the trigger) so the
graph stays inspectable, exactly like a citation, and is tagged `edge_class="typed"` so the payload
cleanly separates typed edges from the lexical co-occurrence layer. Documented supports/contradicts
edges also use CROSS-DOCUMENT ENTITY LINKING (`normalize_entity`): a named entity whose surface form
drifts between artifacts (`mAb-X` ↔ `mAbX` ↔ `mAb X`) is folded to one canonical key so the same
molecule/assay links across documents. Deterministic (sorted, magnitude-rounded), fully unit-testable,
and never fabricates a value — every number/quote comes from a real chunk.
"""

from __future__ import annotations

import re
from collections import defaultdict

# Reuse the conflict layer's unit-aware measurement grammar so the graph and the conflict card agree
# on what counts as "the same quantity" (pH, temperature, concentration, …).
from evidence_graph.conflicts import (
    _CONTRADICTION_RE,
    _LABEL_NUM,
    _LABEL_STOP,
    _NUM_UNIT,
    _UNIT_QUANTITY,
)

# Explicit agreement cues — the mirror of _CONTRADICTION_RE (reproduce/confirm/consistent-with).
_AGREEMENT_RE = re.compile(
    r"\b(?:reproduce[sd]?|replicate[sd]?|confirm(?:s|ed)?|consistent\s+with|"
    r"in\s+agreement\s+with|agree[sd]?\s+with|matches?|corroborate[sd]?)\b",
    re.IGNORECASE,
)

# A recorded decision. Kept high-precision: a verb of choosing/approving/concluding.
_DECISION_RE = re.compile(
    r"\b(?:decide[sd]?|decision|chose|choose|selected?|opt(?:ed|s)?\s+for|"
    r"conclude[sd]?|recommend(?:s|ed)?|approve[sd]?|finali[sz]ed?|"
    r"went\s+with|settled\s+on)\b",
    re.IGNORECASE,
)

# A reference to a standard operating procedure / protocol. Captures a following identifier token so
# "per SOP FLUOR-002" / "using protocol fs_sop_fluor_002" can be matched to a protocol artifact.
_PROTOCOL_REF_RE = re.compile(
    r"\b(?:sop|protocol|s\.?o\.?p\.?)\b[\s:#-]*([A-Za-z0-9][A-Za-z0-9_\-]{2,40})?",
    re.IGNORECASE,
)
# Artifact types that ARE protocols/SOPs (targets for uses-protocol edges).
_PROTOCOL_TYPES = frozenset({"sop", "protocol"})
_PROTOCOL_TITLE_RE = re.compile(r"\b(?:sop|protocol)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------------------------------
# Cross-document entity linking (deterministic, stdlib/regex only)
# ---------------------------------------------------------------------------------------------------
# The typed relations above key `supports`/`contradicts` on shared MEASURED QUANTITIES (pH, temp, …)
# and on artifact-title tokens. That misses the case where two artifacts agree/disagree about the
# SAME NAMED ENTITY whose surface form drifts between documents — e.g. `mAb-X`, `mAbX`, `mAb X` are
# one molecule, but a naive string match treats them as three. `normalize_entity` folds those surface
# forms to one canonical key so a documented agreement/contradiction can be linked ACROSS artifacts.

# A candidate named entity: a distinctive lab identifier such as `mAb-X`, `IgG1`, `FLUOR-002`, `CD20`,
# `p53` — an alphabetic stem (2+ letters) then an optional hyphen/space then a short alphanumeric
# designator (a letter and/or 1–4 digits). Deliberately narrow (identifiers, not prose); regex-only.
_ENTITY_RE = re.compile(
    r"\b([A-Za-z]{2,}[A-Za-z0-9]*)[\s\-]?((?:[A-Za-z]?\d{1,4}[A-Za-z]?)|[A-Z])\b"
)
# Common words that would otherwise slip through as "stem + digit/letter"; never entities.
#
# The prepositions and the measurement labels are the ones that actually bit: on a real corpus
# "at 0.01%", "of 0.1 M" and "pH 6" all matched stem+designator, producing entities `at0`, `of0`
# and `ph6` that then linked unrelated documents. A measured quantity is handled by the measurement
# grammar above and is never a named entity.
_ENTITY_STOP = frozenset(
    "figure table section page chapter volume part step phase day week month year note "
    "sample group type class level stage round trial run test item line row column "
    # prepositions, articles and conjunctions
    "at of in on to by for with from into over under above below and or but the a an as is are "
    "was were be been than then when where which while after before during within without "
    # measurement labels: these are quantities, not entities
    "ph temperature temp time conc concentration dose volume mass weight rate ratio speed "
    "rpm rcf molar molarity percent percentage fraction range mean median average total "
    # generic scientific nouns that take a numeric designator
    "study experiment assay method model result data set batch lot version panel arm cohort "
    "condition control replicate point value number count size width height depth area".split()
)


def normalize_entity(surface: str) -> str:
    """Fold a named-entity surface form to a canonical key so mentions match ACROSS documents.

    Conservative + deterministic: lowercase; strip surrounding punctuation; collapse whitespace; then
    remove the separator (hyphen or space) BETWEEN an alphabetic stem and a short trailing designator
    so `mAb-X` == `mAbX` == `mAb X` == `mabx`. Only the stem↔designator seam is joined (the designator
    side is a <=4-char alphanumeric token), so genuinely distinct multi-word terms like `buffer screen`
    are NOT merged. Returns '' for anything that is not a plausible distinctive identifier.
    """
    s = re.sub(r"\s+", " ", (surface or "")).strip().strip(".,;:!?()[]{}\"'")
    if not s:
        return ""
    m = _ENTITY_RE.fullmatch(s)
    if m:
        stem, designator = m.group(1), m.group(2)
        if stem.lower() in _ENTITY_STOP:
            return ""
        return (stem + designator).lower()
    # Not designator-shaped. Accept only a compact single-token identifier (no internal space) that is
    # distinctive (has a digit OR mixed case, e.g. `mAbX`, `p53`); otherwise it's prose → drop it.
    if " " in s:
        return ""
    compact = s.replace("-", "")
    if len(compact) < 3 or compact.lower() in _ENTITY_STOP:
        return ""
    has_digit = any(c.isdigit() for c in compact)
    mixed_case = compact != compact.lower() and compact != compact.upper()
    if not (has_digit or mixed_case):
        return ""
    return compact.lower()


def _entity_mentions(chunks: list[dict]) -> dict[str, dict[str, tuple[dict, str]]]:
    """canonical_entity -> {artifact_id: (first_chunk_that_mentioned_it, its surface form)}.

    The surface form is kept alongside the chunk because the canonical key is normalised
    (`mAb-X` -> `mabx`) and so never appears in the text. Quoting the key back at a reader as
    provenance would be unreadable, and it would make the citation unverifiable against its own
    source. Deterministic: first mention per artifact wins, chunks scanned in order.
    """
    per: dict[str, dict[str, tuple[dict, str]]] = defaultdict(dict)
    for ch in chunks:
        aid = ch.get("artifact_id", "")
        if not aid:
            continue
        for m in _ENTITY_RE.finditer(_text(ch)):
            key = normalize_entity(m.group(0))
            if key:
                per[key].setdefault(aid, (ch, m.group(0)))
    return per


def _text(chunk: dict) -> str:
    return str(chunk.get("chunk_text") or chunk.get("source_quote") or "")


# What a trigger note claims can be found in the quote. Mirrors `adjudicate._checkable_terms`: a
# measurement note is `label=value` and an entity note is `... re: X` or `mentions X`.
_ANCHOR_MEASUREMENT = re.compile(r"^[\w .+/-]+=(?P<value>.+)$")
_ANCHOR_ENTITY = re.compile(r"(?:\bre:|^mentions)\s+(?P<entity>.+)$", re.IGNORECASE)


def _anchor(note: str) -> str:
    """The substring a cited quote must contain, given the trigger note. Empty when the note is
    descriptive ("decision on ph", "protocol reference") and claims nothing about the text."""
    note = (note or "").strip()
    match = _ANCHOR_MEASUREMENT.match(note) or _ANCHOR_ENTITY.search(note)
    return (match.group(1).strip() if match else "")


def _quote(text: str, limit: int = 160, anchor: str = "") -> str:
    """A short quote from `text`, WINDOWED ON `anchor` when one is given.

    Taking the first `limit` characters is wrong whenever the matched value sits further into the
    passage: the citation then does not contain the thing it is cited for, which makes it
    uncheckable by a reader and worse than no citation at all. Found on a real corpus, where the
    value a trigger named was routinely past the 160-character cut.

    The window is centred on the match and marked with an ellipsis when it does not start at the
    beginning, so a reader can see the quote is an excerpt.
    """
    s = re.sub(r"\s+", " ", text).strip()
    if len(s) <= limit:
        return s
    if anchor:
        position = s.lower().find(anchor.strip().lower())
        if position >= 0:
            end_of_match = position + len(anchor.strip())
            if end_of_match > limit:
                # Centre the window on the match, clamped to the ends of the passage.
                start = max(0, position - (limit - len(anchor.strip())) // 2)
                start = min(start, max(0, len(s) - limit))
                excerpt = s[start:start + limit]
                return ("…" + excerpt) if start else excerpt
    return s[:limit]


def _measurements(text: str) -> list[tuple[str, float, str]]:
    """(quantity_label, rounded_value, display_value) for every measured quantity in `text` — the
    ALL-quantities variant (no question filter), since the graph has no question. Deterministic."""
    out: list[tuple[str, float, str]] = []
    for raw_label, value in _LABEL_NUM.findall(text):
        label = raw_label.lower()
        if label in _LABEL_STOP:
            continue
        try:
            out.append((label, round(float(value), 4), value))
        except ValueError:
            continue
    for value, raw_unit in _NUM_UNIT.findall(text):
        quantity = _UNIT_QUANTITY.get(raw_unit.lower())
        if not quantity:
            continue
        try:
            out.append((quantity, round(float(value), 4), value))
        except ValueError:
            continue
    return out


def _artifact_measurements(chunks: list[dict]) -> dict[str, dict[str, dict[float, tuple[str, dict]]]]:
    """artifact_id -> quantity -> {rounded_value: (display, chunk)} — first chunk that reported each
    magnitude wins (stable). Only quantities are keys; each value keeps a provenance chunk."""
    per: dict[str, dict[str, dict[float, tuple[str, dict]]]] = defaultdict(lambda: defaultdict(dict))
    for ch in chunks:
        aid = ch.get("artifact_id", "")
        if not aid:
            continue
        for label, fval, display in _measurements(_text(ch)):
            per[aid][label].setdefault(fval, (display, ch))
    return per


def _edge(source: str, target: str, etype: str, quantity: str, evidence: list[dict]) -> dict:
    # `edge_class="typed"` marks this as a relation edge (vs the lexical "co-occurs"/"mentions" layer)
    # so a viewer can style and explain the two families distinctly.
    return {"source": source, "target": target, "type": etype, "edge_class": "typed",
            "quantity": quantity, "evidence": evidence, "method": "regex"}


def _ev(chunk: dict, note: str, anchor: str = "") -> dict:
    """One evidence item. `anchor` overrides the substring the quote is windowed on, for edges whose
    note is descriptive ("decision on ph") but which DID fire on a specific phrase in the text.
    Without it the quote is the opening of the passage and may not contain the cue the edge rests
    on, which is the same defect as an unanchored measurement quote."""
    return {"artifact_id": chunk.get("artifact_id", ""),
            "chunk_id": chunk.get("id", ""),
            "location": chunk.get("page_or_row_reference", ""),
            "source_uri": chunk.get("source_uri", ""),
            "quote": _quote(_text(chunk), anchor=anchor or _anchor(note)), "trigger": note}


def extract_typed_relations(artifacts: list[dict], chunks: list[dict]) -> list[dict]:
    """Return a deterministic list of TYPED edges between artifacts (supports / contradicts /
    decided / uses-protocol) with per-edge provenance. Pure function; stdlib + regex only."""
    by_id = {a["id"]: a for a in artifacts}
    per = _artifact_measurements(chunks)
    edges: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(source: str, target: str, etype: str, quantity: str, evidence: list[dict]) -> None:
        if source == target or source not in by_id or target not in by_id:
            return
        key = (source, target, etype, quantity)
        if key in seen:
            return
        seen.add(key)
        edges.append(_edge(source, target, etype, quantity, evidence))

    # 1) NUMERIC supports / contradicts: for each measured quantity, compare the value each artifact
    #    reports. Same magnitude across two artifacts -> supports; different -> contradicts. Directed
    #    edges are emitted lexicographically (a<b) so the pair is deduped and the graph is stable.
    quantities: dict[str, dict[str, dict[float, tuple[str, dict]]]] = defaultdict(dict)
    for aid, byq in per.items():
        for quantity, values in byq.items():
            quantities[quantity][aid] = values
    for quantity in sorted(quantities):
        reporters = sorted(quantities[quantity])           # artifact ids reporting this quantity
        for i in range(len(reporters)):
            for j in range(i + 1, len(reporters)):
                a, b = reporters[i], reporters[j]
                va, vb = quantities[quantity][a], quantities[quantity][b]
                shared = set(va) & set(vb)
                differing = (set(va) | set(vb)) - shared
                if shared:                                  # at least one agreeing value
                    fval = sorted(shared)[0]
                    add(a, b, "supports", quantity, [
                        _ev(va[fval][1], f"{quantity}={va[fval][0]}"),
                        _ev(vb[fval][1], f"{quantity}={vb[fval][0]}")])
                if differing and not shared:                # disagree with no common ground
                    fa = sorted(va)[0]
                    fb = sorted(vb)[0]
                    add(a, b, "contradicts", quantity, [
                        _ev(va[fa][1], f"{quantity}={va[fa][0]}"),
                        _ev(vb[fb][1], f"{quantity}={vb[fb][0]}")])

    # 2) DOCUMENTED supports / contradicts: a chunk that explicitly says it reproduces / contradicts
    #    another named artifact. We link it to any OTHER artifact that names the same result, matched
    #    two ways: (a) the artifact's TITLE token appears in the chunk ("could not reproduce the buffer
    #    screen"); (b) CROSS-DOCUMENT ENTITY LINK — the chunk and the other artifact share a NORMALIZED
    #    named entity (mAb-X ↔ mAbX ↔ mAb X), so agreement/contradiction about the same molecule links
    #    even when neither cites the other's title. The edge keeps provenance for BOTH endpoints.
    title_tokens = {
        aid: {t for t in re.findall(r"[a-z0-9]{4,}", (a.get("title") or "").lower())}
        for aid, a in by_id.items()
    }
    # canonical entity -> {artifact_id: mention chunk}; used for the cross-document link + its target
    # provenance. Only entities spanning 2+ artifacts can produce a cross-doc edge.
    entity_index = _entity_mentions(chunks)
    for ch in chunks:
        aid = ch.get("artifact_id", "")
        if aid not in by_id:
            continue
        text = _text(ch)
        low = text.lower()
        etype = None
        if _CONTRADICTION_RE.search(text):
            etype = "contradicts"
        elif _AGREEMENT_RE.search(text):
            etype = "supports"
        if not etype:
            continue
        cue_match = _CONTRADICTION_RE.search(text) or _AGREEMENT_RE.search(text)
        cue = cue_match.group(0) if cue_match else ""
        # (a) title-token match — link only carries the source chunk (the title named the result).
        for other, toks in title_tokens.items():
            if other == aid or not toks:
                continue
            if any(t in low for t in toks):
                add(aid, other, etype, "documented", [_ev(ch, etype, anchor=cue)])
        # (b) cross-document entity link — the chunk shares a normalized named entity with another
        #     artifact. Emit an edge to each such artifact, provenance = this chunk + the artifact's
        #     own mention chunk (so the UI can show both linked passages verbatim).
        chunk_entities: dict[str, str] = {}
        for m in _ENTITY_RE.finditer(text):
            key = normalize_entity(m.group(0))
            if key:
                chunk_entities.setdefault(key, m.group(0))
        for ent, here in sorted(chunk_entities.items()):
            for other, (mention, there) in sorted(entity_index.get(ent, {}).items()):
                if other == aid:
                    continue
                # Each side quotes the surface form as it appears in ITS OWN passage, so both
                # citations can be checked against the text a reader will actually see.
                add(aid, other, etype, ent,
                    [_ev(ch, f"{etype} re: {here}"), _ev(mention, f"mentions {there}")])

    # 3) DECIDED: a chunk in an artifact that records a decision about a measured quantity -> edge to
    #    every artifact that reports a value for that quantity (the evidence the decision rests on).
    for ch in chunks:
        aid = ch.get("artifact_id", "")
        if aid not in by_id:
            continue
        text = _text(ch)
        decision = _DECISION_RE.search(text)
        if not decision:
            continue
        # Window the quote on the decision verb itself: it is the reason the edge exists, and a
        # citation that does not contain it cannot be checked by a reader.
        for quantity, _fval, _display in _measurements(text):
            for other in sorted(quantities.get(quantity, {})):
                if other != aid:
                    add(aid, other, "decided", quantity,
                        [_ev(ch, f"decision on {quantity}", anchor=decision.group(0))])

    # 4) USES-PROTOCOL: a chunk that references an SOP/protocol -> edge to a protocol/SOP artifact,
    #    matched by the captured identifier token or (fallback) any protocol-typed artifact whose
    #    title tokens appear in the chunk.
    protocol_ids = {
        aid for aid, a in by_id.items()
        if (a.get("artifact_type") or "").lower() in _PROTOCOL_TYPES
        or _PROTOCOL_TITLE_RE.search(a.get("title") or "")
    }
    for ch in chunks:
        aid = ch.get("artifact_id", "")
        if aid not in by_id:
            continue
        text = _text(ch)
        low = text.lower()
        if not _PROTOCOL_REF_RE.search(text):
            continue
        for pid in sorted(protocol_ids):
            if pid == aid:
                continue
            toks = title_tokens.get(pid, set())
            # match by a distinctive protocol id/token appearing in the referencing chunk
            if pid.lower() in low or (toks and any(t in low for t in toks)):
                add(aid, pid, "uses-protocol", "", [_ev(ch, "protocol reference")])

    # Stable output order: (type, source, target, quantity).
    edges.sort(key=lambda e: (e["type"], e["source"], e["target"], e["quantity"]))
    return edges
