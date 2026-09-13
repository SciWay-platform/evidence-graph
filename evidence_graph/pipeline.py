"""The whole spine in one call: propose, ground, adjudicate, reconcile, export.

Each stage is useful on its own and this module does not hide any of them. It exists so that the
common path — a corpus in, a checked graph and a review queue out — is one function rather than a
recipe every caller reimplements slightly differently.

    chunks ──▶ propose      relations.extract_typed_relations, regex and numeric grammar
           ──▶ ground       grounding.ground_all, surface strings to CURIEs, refusing most of them
           ──▶ adjudicate   assert / abstain / conflict per candidate edge
           ──▶ reconcile    fold into the previous run's history; nothing is overwritten
           ──▶ export       KGX, with only the asserted edges in it

The two outputs that matter are deliberately separate. `graph` holds what the system was willing to
assert. `open_questions` holds what it would not, with the evidence attached. A pipeline that
returns only the first is claiming a completeness it has not earned.

Nothing here calls a model or opens a socket. Supply `scorer` to add an LLM to the adjudication
step; everything else stays deterministic either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evidence_graph.adjudicate import (
    DEFAULT_ASSERT_AT,
    AdjudicationResult,
    EdgeScorer,
    adjudicate,
    open_questions,
)
from evidence_graph.grounding import GroundingReport, Vocabulary, ground_all, load_vocabulary
from evidence_graph.kgx import (
    KgxGraph,
    artifact_curie,
    from_adjudications,
    with_concept_mentions,
)
from evidence_graph.maintain import History, changelog, reconcile
from evidence_graph.relations import extract_typed_relations

#: Candidate term shapes pulled from a passage before grounding. Deliberately generous — grounding
#: refuses the overwhelming majority, and a term never offered can never be grounded.
#:
#: The bare-number alternative is not decorative. Ontology labels routinely end in one
#: ("polysorbate 80", "interleukin 6"), and a pattern that only matches letter-initial tokens can
#: never assemble them. A lone "80" is offered too and is harmlessly refused by the length floor.
_TERM = re.compile(r"[A-Za-zα-ωΑ-Ω][A-Za-z0-9α-ωΑ-Ω][A-Za-z0-9α-ωΑ-Ω\-]{0,40}|\d{1,4}")

#: How many words a candidate term may span. Ontology labels are routinely two or three words
#: ("polysorbate 80", "tumor necrosis factor"), and single tokens alone miss them.
MAX_TERM_WORDS = 3


@dataclass(frozen=True)
class PipelineResult:
    """Everything one run produced, including what it declined to assert."""

    candidates: tuple[dict[str, Any], ...]
    adjudication: AdjudicationResult
    grounding: GroundingReport
    graph: KgxGraph
    history: History
    #: Grounded mentions, one per (document, concept) pair, each carrying its passage.
    mentions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def open_questions(self) -> list[dict[str, Any]]:
        """Candidate edges the adjudicator would not assert, with their evidence, for review."""
        return open_questions(self.adjudication)

    def changelog(self, *, since: str | None = None) -> list[dict[str, Any]]:
        return changelog(self.history, since=since)

    def summary(self) -> dict[str, Any]:
        return {
            "edges": self.adjudication.summary(),
            "grounding": self.grounding.summary(),
            "graph": {"nodes": len(self.graph.nodes), "edges": len(self.graph.edges)},
            "open_questions": len(self.adjudication.abstained),
        }


def candidate_terms(text: str, *, max_words: int = MAX_TERM_WORDS) -> list[str]:
    """Word and short phrase candidates from one passage, in order of appearance.

    Every n-gram up to `max_words` is offered. That is wasteful and correct: grounding is exact and
    refuses nearly everything, so the cost of a bad candidate is one dictionary miss, while the cost
    of not offering "polysorbate 80" is never grounding it at all.
    """
    words = _TERM.findall(text or "")
    terms: list[str] = []
    seen: set[str] = set()
    for index in range(len(words)):
        for span in range(1, max_words + 1):
            if index + span > len(words):
                break
            phrase = " ".join(words[index:index + span])
            if phrase not in seen:
                seen.add(phrase)
                terms.append(phrase)
    return terms


def _mentions(chunks: list[dict[str, Any]], vocabulary: Vocabulary) -> tuple[list[dict], GroundingReport]:
    """Ground every candidate term and keep the first passage that evidenced each concept."""
    all_terms: list[str] = []
    seen_terms: set[str] = set()
    per_chunk: list[tuple[dict, list[str]]] = []
    for chunk in chunks:
        terms = candidate_terms(str(chunk.get("chunk_text") or chunk.get("source_quote") or ""))
        per_chunk.append((chunk, terms))
        for term in terms:
            if term not in seen_terms:
                seen_terms.add(term)
                all_terms.append(term)

    report = ground_all(all_terms, vocabulary=vocabulary)
    by_surface = {g.surface: g for g in report.grounded}

    mentions: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for chunk, terms in per_chunk:
        document = str(chunk.get("artifact_id", ""))
        if not document:
            continue
        for term in terms:
            grounding = by_surface.get(term)
            # An ambiguous surface form is not asserted as a mention of any one concept. The
            # ambiguity is preserved in the grounding report for a caller that wants to resolve it.
            if grounding is None or grounding.ambiguous:
                continue
            concept = grounding.best
            if (document, concept.curie) in seen_pairs:
                continue
            seen_pairs.add((document, concept.curie))
            mentions.append({
                "document": document,
                "curie": concept.curie,
                "label": concept.label,
                "category": concept.category,
                "surface": term,
                "quote": str(chunk.get("chunk_text") or "")[:300],
                "location": chunk.get("page_or_row_reference", ""),
                "chunk_id": chunk.get("id", ""),
            })
    return mentions, report


def build(
    artifacts: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    scorer: EdgeScorer | None = None,
    assert_at: float = DEFAULT_ASSERT_AT,
    history: History | None = None,
    at: str,
    vocabulary: Vocabulary | None = None,
    ground_concepts: bool = True,
    knowledge_source: str = "infores:evidence-graph",
) -> PipelineResult:
    """Run the full spine over a corpus.

    `at` is the timestamp recorded on this run's revisions, supplied by the caller so a build is
    reproducible. `history` is the previous run's, if there is one; edges that lose their support
    are weakened rather than dropped, and edges that vanish are withdrawn rather than deleted.
    """
    candidates = extract_typed_relations(artifacts, chunks)
    result = adjudicate(candidates, scorer=scorer, assert_at=assert_at)

    if ground_concepts:
        mentions, grounding = _mentions(chunks, vocabulary or load_vocabulary())
    else:
        mentions, grounding = [], GroundingReport((), ())

    graph = from_adjudications(result, artifacts, knowledge_source=knowledge_source)
    if mentions:
        # Mentions are keyed by artifact id; graph nodes are keyed by CURIE (a DOI where one
        # exists). Join them here rather than making the KGX layer know about artifact dicts.
        curie_by_artifact = {a["id"]: artifact_curie(a) for a in artifacts}
        resolved = [
            {**m, "document": curie_by_artifact.get(m["document"], m["document"])}
            for m in mentions
        ]
        graph = with_concept_mentions(graph, resolved, knowledge_source=knowledge_source)

    return PipelineResult(
        candidates=tuple(candidates),
        adjudication=result,
        grounding=grounding,
        graph=graph,
        history=reconcile(history, result, at=at),
        mentions=tuple(mentions),
    )
