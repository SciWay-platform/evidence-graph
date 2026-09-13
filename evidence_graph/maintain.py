"""Keeping a graph current without overwriting what it used to say.

A graph built once is a snapshot. A graph that is maintained has to answer a harder question: when
new literature arrives and the evidence for an existing edge changes, what happens to the edge?

The common answer is to rebuild from scratch, which quietly destroys information. If an edge was
asserted last month and today's corpus contradicts it, "rebuild" shows you a graph without that
edge and no record that it was ever there, or why it left. For a scientific record that is the
wrong behaviour: the fact that a finding was once supported and is now contested **is** the finding.

So reconciliation here is additive. Every edge carries a revision list, and a re-run appends to it:

  NEW            first seen
  UNCHANGED      same verdict, same evidence
  RESTATED       same verdict, but the evidence behind it changed
  STRENGTHENED   was abstained on, now asserted
  WEAKENED       was asserted, now abstained — the support no longer reaches
  CONTRADICTED   was asserted, now in conflict with another edge in the same corpus
  WITHDRAWN      the candidate is gone entirely (its source document was removed or re-parsed)
  REINSTATED     withdrawn or weakened before, asserted again

Nothing is deleted. `live_edges` gives the current graph; the history behind it stays queryable, so
"when did we stop believing this, and what changed?" has an answer.

Timestamps are supplied by the caller rather than read from the clock, so a reconciliation is
reproducible and testable — the same convention as `provenance`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from evidence_graph.adjudicate import Adjudication, AdjudicationResult, Verdict


class EdgeStatus(StrEnum):
    """What happened to an edge at one revision."""

    NEW = "new"
    UNCHANGED = "unchanged"
    RESTATED = "restated"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    CONTRADICTED = "contradicted"
    WITHDRAWN = "withdrawn"
    REINSTATED = "reinstated"


#: Statuses at which the edge is part of the current graph.
LIVE_STATUSES = frozenset({
    EdgeStatus.NEW, EdgeStatus.UNCHANGED, EdgeStatus.RESTATED,
    EdgeStatus.STRENGTHENED, EdgeStatus.REINSTATED,
})


def evidence_fingerprint(edge: dict[str, Any]) -> str:
    """SHA-256 over the edge's cited evidence.

    Lets a re-run tell "the same claim, still supported by the same passages" apart from "the same
    claim, now resting on different passages". The second is a change worth recording even when the
    verdict is identical, because the reason to believe the edge has moved.
    """
    material = [
        [ev.get("artifact_id", ""), ev.get("chunk_id", ""), ev.get("quote", ""), ev.get("trigger", "")]
        for ev in (edge.get("evidence") or [])
    ]
    material.sort()
    payload = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Revision:
    """One entry in an edge's history."""

    revision: int
    at: str
    status: EdgeStatus
    verdict: Verdict | None
    score: float | None
    reason: str
    evidence_fingerprint: str
    #: For CONTRADICTED: the edges this one is now in conflict with.
    conflicts_with: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "at": self.at,
            "status": str(self.status),
            "verdict": str(self.verdict) if self.verdict else None,
            "score": self.score,
            "reason": self.reason,
            "evidence_fingerprint": self.evidence_fingerprint,
            "conflicts_with": list(self.conflicts_with),
        }


@dataclass(frozen=True)
class EdgeHistory:
    """An edge and everything that has happened to it."""

    key: str
    #: The most recent shape of the edge, including the evidence behind the latest revision.
    edge: dict[str, Any]
    revisions: tuple[Revision, ...] = field(default_factory=tuple)

    @property
    def current(self) -> Revision:
        return self.revisions[-1]

    @property
    def status(self) -> EdgeStatus:
        return self.current.status

    @property
    def live(self) -> bool:
        """Is this edge part of the graph as it stands?"""
        return self.status in LIVE_STATUSES

    @property
    def contested(self) -> bool:
        return self.status is EdgeStatus.CONTRADICTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "edge": self.edge,
            "status": str(self.status),
            "live": self.live,
            "revisions": [r.as_dict() for r in self.revisions],
        }


#: A graph's full history, keyed by `adjudicate.edge_key`.
History = dict[str, EdgeHistory]


def _classify(
    previous: EdgeHistory | None,
    adjudication: Adjudication,
    fingerprint: str,
) -> tuple[EdgeStatus, str]:
    """Decide what this re-run did to one edge, and say why in a line a human can read."""
    verdict = adjudication.verdict

    if previous is None:
        if verdict is Verdict.CONFLICT:
            return EdgeStatus.CONTRADICTED, "first seen, already contested by another edge"
        return EdgeStatus.NEW, f"first seen ({verdict})"

    was = previous.current
    was_live = previous.live

    if verdict is Verdict.CONFLICT:
        if was.verdict is Verdict.ASSERT:
            return EdgeStatus.CONTRADICTED, "was asserted; the corpus now disagrees with itself here"
        return EdgeStatus.CONTRADICTED, "contested by another edge in the corpus"

    if verdict is Verdict.ASSERT:
        if not was_live:
            return EdgeStatus.REINSTATED, f"asserted again after being {was.status}"
        if was.verdict is Verdict.ABSTAIN:
            return EdgeStatus.STRENGTHENED, "evidence now reaches the assertion threshold"
        if fingerprint != was.evidence_fingerprint:
            return EdgeStatus.RESTATED, "same conclusion, different supporting passages"
        return EdgeStatus.UNCHANGED, "unchanged"

    # ABSTAIN
    if was.verdict is Verdict.ASSERT:
        return EdgeStatus.WEAKENED, f"support no longer reaches: {adjudication.rationale}"
    if fingerprint != was.evidence_fingerprint:
        return EdgeStatus.RESTATED, "still not asserted, on different passages"
    return EdgeStatus.UNCHANGED, "unchanged"


def reconcile(
    history: History | None,
    result: AdjudicationResult,
    *,
    at: str,
) -> History:
    """Fold a fresh adjudication into an existing history, returning the updated history.

    Pure: the input history is not mutated, so a caller can diff before and after, or discard the
    result if a re-run looks wrong.

    Edges present in the history but absent from this run are WITHDRAWN rather than deleted. That
    happens when a source document is removed or re-parsed into different chunks, and it is exactly
    the case where a silent rebuild would leave no trace.
    """
    previous: History = dict(history or {})
    updated: History = {}
    seen: set[str] = set()

    for adjudication in result.adjudications:
        key = adjudication.key
        seen.add(key)
        before = previous.get(key)
        fingerprint = evidence_fingerprint(adjudication.edge)
        status, reason = _classify(before, adjudication, fingerprint)
        revision = Revision(
            revision=(before.current.revision + 1) if before else 1,
            at=at,
            status=status,
            verdict=adjudication.verdict,
            score=adjudication.score,
            reason=reason,
            evidence_fingerprint=fingerprint,
            conflicts_with=adjudication.conflicts_with,
        )
        updated[key] = EdgeHistory(
            key=key,
            edge=adjudication.edge,
            revisions=((before.revisions if before else ()) + (revision,)),
        )

    for key, before in previous.items():
        if key in seen:
            continue
        if before.status is EdgeStatus.WITHDRAWN:
            updated[key] = before  # already gone; do not add a second withdrawal
            continue
        revision = Revision(
            revision=before.current.revision + 1,
            at=at,
            status=EdgeStatus.WITHDRAWN,
            verdict=None,
            score=None,
            reason="no longer proposed by the corpus",
            evidence_fingerprint=before.current.evidence_fingerprint,
        )
        updated[key] = EdgeHistory(key=key, edge=before.edge, revisions=before.revisions + (revision,))

    return updated


def live_edges(history: History) -> list[dict[str, Any]]:
    """The graph as it currently stands: the edges whose latest revision keeps them in it."""
    return [h.edge for _, h in sorted(history.items()) if h.live]


def contested_edges(history: History) -> list[EdgeHistory]:
    """Edges the corpus currently disagrees about. These are findings, not failures."""
    return [h for _, h in sorted(history.items()) if h.contested]


def changelog(history: History, *, since: str | None = None) -> list[dict[str, Any]]:
    """What changed, most recent first — the answer to "what did the re-run do?".

    Ordered by the revision timestamp rather than the revision number, because revision numbers are
    per-edge: an edge seen for the first time in the tenth run is at revision 1, and sorting by that
    would bury it under long-lived edges. `since` takes a timestamp (the `at` of an earlier run) to
    report just what happened after it.

    `UNCHANGED` revisions are omitted. A changelog listing everything that stayed the same is not a
    changelog.
    """
    entries: list[dict[str, Any]] = []
    for key, h in sorted(history.items()):
        for rev in h.revisions:
            if rev.status is EdgeStatus.UNCHANGED:
                continue
            if since is not None and rev.at <= since:
                continue
            entries.append({
                "key": key,
                "type": h.edge.get("type"),
                "source": h.edge.get("source"),
                "target": h.edge.get("target"),
                "quantity": h.edge.get("quantity"),
                **rev.as_dict(),
            })
    entries.sort(key=lambda e: (e["at"], e["key"]), reverse=True)
    return entries
