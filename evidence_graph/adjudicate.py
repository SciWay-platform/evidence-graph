"""Assert, abstain, or record a conflict — the verdict step between proposing an edge and keeping it.

A proposer (`relations.extract_typed_relations`, or a model) is optimistic by construction: it emits
every edge its patterns fire on. That is the right behaviour for a proposer and the wrong behaviour
for a graph, because a plausible edge and a supported edge are indistinguishable once they are in
the store. This module is the gate in between.

Every candidate gets one of three verdicts:

  ASSERT    the evidence carries the claim; the edge goes into the graph.
  ABSTAIN   the evidence does not reach; the edge is kept as an OPEN QUESTION with its evidence
            attached, so a curator can confirm or reject it. It is never silently dropped — an
            abstention is a finding, and discarding it throws away the only record that the system
            looked and was not convinced.
  CONFLICT  the candidate set disagrees with itself about this pair of documents (one edge says
            they agree on a quantity, another says they disagree). Both are kept, side by side,
            rather than resolved by picking the higher score.

**Scoring is pluggable and the default is keyless.** `EdgeScorer` is a Protocol: given an edge and
its evidence, return a score and a rationale. The default `GroundingScorer` runs no model. It asks a
narrower question than "is this edge true" — it asks **"is this edge actually grounded in the text
it cites?"**: does the evidence exist, does each quote really contain the trigger the proposer
claims fired, do both endpoints carry evidence, does the quote show the cue the edge type implies.

That distinction is the honest part. Verbatim grounding is necessary and not sufficient for
correctness: a perfectly grounded edge can still be a wrong reading of the sentence. An LLM scorer
supplied by the caller adds semantic judgement on top. The library defines the seam and implements
the half that needs no key, so the pipeline degrades to a weaker but still correct graph offline.

`assert_at` is the abstention dial. Raising it asserts fewer edges at higher precision; the
trade-off it makes is measurable with `evidence_graph.evaluation.risk_coverage`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# Default score floor for assertion, on the 0-10 scale every scorer reports. 6.0 sits above
# "evidence exists and is well formed" (which the grounding scorer alone can reach) and below
# "every check passed", so a structurally sound but weakly cued edge abstains rather than asserts.
DEFAULT_ASSERT_AT = 6.0

# Edge types that make incompatible claims about the same pair of documents and quantity. If a
# proposer emits both, the truth is not "whichever scored higher" — it is that the corpus disagrees.
INCOMPATIBLE_TYPES = frozenset({"supports", "contradicts"})

# Surface cues an edge type implies. Used by the grounding scorer to check that the quote shows
# what the edge claims, not merely that a quote exists.
_TYPE_CUES: dict[str, re.Pattern[str]] = {
    "decided": re.compile(
        r"\b(?:decide[sd]?|decision|chose|choose|selected?|opt(?:ed|s)?\s+for|conclude[sd]?|"
        r"recommend(?:s|ed)?|approve[sd]?|finali[sz]ed?|went\s+with|settled\s+on)\b",
        re.IGNORECASE,
    ),
    "uses-protocol": re.compile(r"\b(?:sop|protocol|s\.?o\.?p\.?)\b", re.IGNORECASE),
}


class Verdict(StrEnum):
    """What the adjudicator concluded about one candidate edge."""

    ASSERT = "assert"
    ABSTAIN = "abstain"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class EdgeScore:
    """A scorer's judgement of one candidate edge.

    score: 0-10. Scorers must use this scale so thresholds and risk-coverage curves are comparable
        between a deterministic scorer and a model.
    rationale: why, in one short human-readable line. This is shown to the curator reviewing an
        abstention, so "no evidence carries the trigger 'ph=5.5'" beats "score 2.0".
    checks: per-check detail, for a scorer that runs several. Free-form and advisory.
    """

    score: float
    rationale: str
    checks: dict[str, bool] = field(default_factory=dict)


class EdgeScorer(Protocol):
    """How the caller supplies judgement.

    An implementation receives the candidate edge (the shape `relations.extract_typed_relations`
    emits) and returns an `EdgeScore`. It must be deterministic for a given input if the caller
    wants reproducible graphs; a model-backed scorer usually is not, which is why the library
    default is not one.
    """

    def __call__(self, edge: dict[str, Any]) -> EdgeScore: ...


@dataclass(frozen=True)
class Adjudication:
    """One candidate edge plus the verdict on it, and everything needed to explain the verdict."""

    edge: dict[str, Any]
    verdict: Verdict
    score: float
    rationale: str
    checks: dict[str, bool] = field(default_factory=dict)
    # Populated for CONFLICT: the other adjudications this one disagrees with.
    conflicts_with: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Stable identity for this edge: source, type, target, quantity."""
        return edge_key(self.edge)

    @property
    def asserted(self) -> bool:
        return self.verdict is Verdict.ASSERT


@dataclass(frozen=True)
class AdjudicationResult:
    """The outcome of adjudicating a candidate set, partitioned by verdict.

    `abstained` is deliberately as prominent as `asserted`. It is the review queue, and a pipeline
    that ignores it is throwing away the system's own record of what it could not establish.
    """

    adjudications: tuple[Adjudication, ...]

    @property
    def asserted(self) -> tuple[Adjudication, ...]:
        return tuple(a for a in self.adjudications if a.verdict is Verdict.ASSERT)

    @property
    def abstained(self) -> tuple[Adjudication, ...]:
        return tuple(a for a in self.adjudications if a.verdict is Verdict.ABSTAIN)

    @property
    def conflicted(self) -> tuple[Adjudication, ...]:
        return tuple(a for a in self.adjudications if a.verdict is Verdict.CONFLICT)

    @property
    def coverage(self) -> float:
        """Share of candidates that were asserted. The x-axis of a risk-coverage curve."""
        if not self.adjudications:
            return 0.0
        return len(self.asserted) / len(self.adjudications)

    def summary(self) -> dict[str, Any]:
        return {
            "candidates": len(self.adjudications),
            "asserted": len(self.asserted),
            "abstained": len(self.abstained),
            "conflicted": len(self.conflicted),
            "coverage": round(self.coverage, 4),
        }


def edge_key(edge: dict[str, Any]) -> str:
    """Stable identity for an edge: `source|type|target|quantity`."""
    return "|".join(
        str(edge.get(k, "")) for k in ("source", "type", "target", "quantity")
    )


def _pair_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    """Identity of the *claim* an edge makes, ignoring its direction and its type.

    `supports` and `contradicts` between the same two documents about the same quantity are claims
    about the same thing, so they collide here on purpose — that collision is how a conflict is
    found. Direction is dropped because "A agrees with B" and "B agrees with A" are one claim.
    """
    a, b = str(edge.get("source", "")), str(edge.get("target", ""))
    lo, hi = (a, b) if a <= b else (b, a)
    return (lo, hi, str(edge.get("quantity", "")))


# --------------------------------------------------------------------------------------------
# The default scorer: grounding, not truth
# --------------------------------------------------------------------------------------------


def _trigger_terms(trigger: str) -> list[str]:
    """Split a proposer trigger like `ph=5.5` into the parts that must appear in the quote."""
    return [part for part in re.split(r"[=\s]+", trigger or "") if part]


class GroundingScorer:
    """Deterministic scorer: is this edge grounded in the text it cites?

    Runs no model and needs no key. It checks what can be checked by reading the evidence against
    the claim, and it is deliberately clear about what that does and does not establish — see the
    module docstring. Each check contributes to a 0-10 score:

      has_evidence        the edge cites at least one passage at all                    (hard gate)
      evidence_complete   every cited passage carries a quote and a locator
      trigger_verbatim    the value the proposer says fired is actually in the quote
      both_endpoints      a document-to-document edge cites evidence from both documents
      type_cue_present    the quote shows the cue the edge type implies (decided, uses-protocol)

    `trigger_verbatim` is the one that catches real defects: a proposer that reports `ph=5.5` from a
    passage that does not contain 5.5 has mis-parsed, and the resulting edge is unciteable.
    """

    #: Weight per check. `has_evidence` is a gate rather than a weight — without it the score is 0.
    WEIGHTS = {
        "evidence_complete": 2.0,
        "trigger_verbatim": 4.0,
        "both_endpoints": 2.5,
        "type_cue_present": 1.5,
    }

    def __call__(self, edge: dict[str, Any]) -> EdgeScore:
        evidence = edge.get("evidence") or []
        if not evidence:
            return EdgeScore(0.0, "no evidence cited", {"has_evidence": False})

        checks: dict[str, bool] = {"has_evidence": True}
        reasons: list[str] = []

        checks["evidence_complete"] = all(
            (ev.get("quote") or "").strip() and (ev.get("chunk_id") or ev.get("source_uri"))
            for ev in evidence
        )
        if not checks["evidence_complete"]:
            reasons.append("a cited passage is missing its quote or locator")

        triggered = [ev for ev in evidence if (ev.get("trigger") or "").strip()]
        if triggered:
            checks["trigger_verbatim"] = all(
                all(term.lower() in (ev.get("quote") or "").lower() for term in _trigger_terms(ev["trigger"]))
                for ev in triggered
            )
            if not checks["trigger_verbatim"]:
                bad = next(
                    ev for ev in triggered
                    if not all(t.lower() in (ev.get("quote") or "").lower() for t in _trigger_terms(ev["trigger"]))
                )
                reasons.append(f"quote does not contain the trigger {bad['trigger']!r}")
        else:
            # Nothing claimed to have fired, so nothing to verify. Neither credit nor penalty: the
            # check is recorded as untested by leaving it out of `checks`.
            pass

        endpoints = {str(edge.get("source", "")), str(edge.get("target", ""))}
        cited = {str(ev.get("artifact_id", "")) for ev in evidence}
        checks["both_endpoints"] = endpoints.issubset(cited)
        if not checks["both_endpoints"]:
            missing = sorted(endpoints - cited)
            reasons.append(f"no evidence cited from {', '.join(missing)}")

        cue = _TYPE_CUES.get(str(edge.get("type", "")))
        if cue is not None:
            checks["type_cue_present"] = any(cue.search(ev.get("quote") or "") for ev in evidence)
            if not checks["type_cue_present"]:
                reasons.append(f"no {edge['type']} cue in any cited quote")

        earned = sum(self.WEIGHTS[name] for name, ok in checks.items() if ok and name in self.WEIGHTS)
        available = sum(self.WEIGHTS[name] for name in checks if name in self.WEIGHTS)
        score = round(10.0 * earned / available, 2) if available else 0.0

        rationale = "; ".join(reasons) if reasons else "evidence carries the claim verbatim"
        return EdgeScore(score, rationale, checks)


#: The library default. Keyless, deterministic, and honest about scoring grounding rather than truth.
DEFAULT_SCORER: EdgeScorer = GroundingScorer()


# --------------------------------------------------------------------------------------------
# Adjudication
# --------------------------------------------------------------------------------------------


def adjudicate(
    edges: list[dict[str, Any]],
    *,
    scorer: EdgeScorer | None = None,
    assert_at: float = DEFAULT_ASSERT_AT,
) -> AdjudicationResult:
    """Score every candidate edge and return a verdict for each.

    Order of resolution matters. Conflicts are found FIRST, across the whole candidate set, because
    a conflict is a property of the set rather than of any single edge: two edges that make
    incompatible claims about the same document pair are both kept and marked, instead of one of
    them winning on score. Only the remaining edges face the `assert_at` threshold.

    Deterministic given a deterministic scorer: candidates keep their input order, and nothing here
    depends on iteration order of a set.
    """
    scorer = scorer or DEFAULT_SCORER
    scored = [(edge, scorer(edge)) for edge in edges]

    # Which claims have incompatible candidates? Group by the claim, not the edge.
    by_claim: dict[tuple[str, str, str], set[str]] = {}
    for edge, _ in scored:
        etype = str(edge.get("type", ""))
        if etype in INCOMPATIBLE_TYPES:
            by_claim.setdefault(_pair_key(edge), set()).add(etype)
    contested = {claim for claim, types in by_claim.items() if len(types & INCOMPATIBLE_TYPES) > 1}

    out: list[Adjudication] = []
    for edge, result in scored:
        claim = _pair_key(edge)
        if claim in contested and str(edge.get("type", "")) in INCOMPATIBLE_TYPES:
            others = tuple(
                sorted(
                    edge_key(other)
                    for other, _ in scored
                    if _pair_key(other) == claim
                    and str(other.get("type", "")) in INCOMPATIBLE_TYPES
                    and edge_key(other) != edge_key(edge)
                )
            )
            out.append(
                Adjudication(
                    edge=edge,
                    verdict=Verdict.CONFLICT,
                    score=result.score,
                    rationale=(
                        "the candidate set disagrees about this pair: "
                        + " and ".join(sorted(by_claim[claim]))
                    ),
                    checks=result.checks,
                    conflicts_with=others,
                )
            )
            continue

        verdict = Verdict.ASSERT if result.score >= assert_at else Verdict.ABSTAIN
        rationale = result.rationale
        if verdict is Verdict.ABSTAIN and not rationale.startswith("no evidence"):
            rationale = f"{rationale} (score {result.score} below {assert_at})"
        out.append(
            Adjudication(
                edge=edge,
                verdict=verdict,
                score=result.score,
                rationale=rationale,
                checks=result.checks,
            )
        )

    return AdjudicationResult(tuple(out))


def open_questions(result: AdjudicationResult) -> list[dict[str, Any]]:
    """Render the abstentions as a curator's review queue.

    Each entry carries the claim, why the adjudicator was not convinced, and the evidence it looked
    at, so a human can settle it without going back to the corpus themselves.
    """
    return [
        {
            "key": a.key,
            "source": a.edge.get("source"),
            "target": a.edge.get("target"),
            "type": a.edge.get("type"),
            "quantity": a.edge.get("quantity"),
            "score": a.score,
            "why_not_asserted": a.rationale,
            "evidence": a.edge.get("evidence") or [],
        }
        for a in result.abstained
    ]
