"""W3C PROV-O answer lineage — a tamper-evident derivation record for a grounded answer.

Every citation already carries provenance (source_system, title, chunk id, quote, source_uri,
page/line). This module turns an answer packet into an explicit, checksummed **derivation
graph** — the audit artifact behind "prove what the system did and why" (EU AI Act Art. 50 transparency
+ GDPR accountability). The shape follows W3C PROV-O:

    answer  --prov:wasGeneratedBy-->  activity "grounded_qa"
            --prov:wasAttributedTo-->  agent  "sciway:<engine>"   (deterministic | llm:provider:model)
            --prov:used / wasDerivedFrom-->  chunk entities
    chunk   --prov:wasDerivedFrom-->  document entity  (source_uri / DOI)  + page/line + verbatim quote

Every entry carries a SHA-256 checksum over its content, so any later edit to a quote, source, or
derivation link is detectable (`verify_lineage`). Pure + stdlib + deterministic (the caller passes the
timestamp), so it is unit-testable and adds no dependency.

The `ProvenanceEntry` shape and the checksum idea are adapted from Semantica's `provenance/` module
(https://github.com/semantica-agi/semantica, MIT) — reimplemented rather than vendored: we bind the
verbatim quote and the derivation links into the checksum, and build the graph straight from an
answer packet. See NOTICE for the upstream licence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

PROV_SCHEMA_VERSION = "1.0"
DEFAULT_AGENT = "sciway:deterministic"

# JSON-LD namespaces for the PROV-O rendering (prov_jsonld). `sciway:` carries the fields with no
# clean PROV/DC term (checksum, answer status, retrieval scores); `prov:` is the W3C PROV ontology.
PROV_CONTEXT = {"prov": "http://www.w3.org/ns/prov#", "sciway": "https://sciway.eu/ns#"}


@dataclass
class ProvenanceEntry:
    """One node in an answer's PROV-O derivation graph.

    entity_id/entity_type: the thing (prov:Entity) — "answer" | "chunk" | "document".
    activity_id: the process that produced it (prov:Activity), e.g. "grounded_qa".
    agent_id: who is responsible (prov:Agent), e.g. "sciway:llm:mistral:...".
    parent_entity_id / used_entities: prov:wasDerivedFrom / prov:used derivation links.
    source_document/source_location/source_quote: where the evidence came from (audit trail).
    checksum: SHA-256 over the entry's content (tamper-evidence); set by `finalize`."""

    entity_id: str
    entity_type: str
    activity_id: str = "grounded_qa"
    agent_id: str = DEFAULT_AGENT
    source_document: str = ""
    source_location: str = ""
    source_quote: str = ""
    timestamp: str = ""
    confidence: float = 1.0
    parent_entity_id: str | None = None
    used_entities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = PROV_SCHEMA_VERSION
    checksum: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_checksum(entry: ProvenanceEntry) -> str:
    """Deterministic SHA-256 over the entry's *content* fields (never the checksum itself).

    Binds the identity, activity/agent, the verbatim source (document + location + quote), the
    confidence, and the derivation links — so tampering with any of them (e.g. quietly editing a
    cited quote or re-pointing a derivation edge) changes the checksum and is caught by
    `verify_checksum`. Timestamp is included so the record is bound to when it was produced."""
    payload = "␟".join([
        entry.entity_id, entry.entity_type, entry.activity_id, entry.agent_id,
        entry.source_document, entry.source_location, entry.source_quote,
        entry.timestamp, f"{entry.confidence}", entry.parent_entity_id or "",
        ",".join(entry.used_entities), entry.version,
    ])
    return _sha(payload)


def finalize(entry: ProvenanceEntry) -> ProvenanceEntry:
    """Stamp the entry's checksum from its current content and return it."""
    entry.checksum = compute_checksum(entry)
    return entry


def verify_checksum(entry: ProvenanceEntry | dict[str, Any]) -> bool:
    """True iff the entry's stored checksum matches a fresh recomputation (untampered)."""
    if isinstance(entry, dict):
        stored = entry.get("checksum")
        entry = ProvenanceEntry(**{k: v for k, v in entry.items() if k != "checksum"})
        entry.checksum = stored
    if not entry.checksum:
        return False
    return compute_checksum(entry) == entry.checksum


def _cit_field(cit: dict[str, Any], *names: str) -> str:
    """First non-empty value among `names` — tolerant of both the source_inspection shape
    (artifact_title/chunk_quote) and the raw RetrievalHit shape (title/quote)."""
    for name in names:
        val = cit.get(name)
        if val:
            return str(val)
    return ""


def answer_lineage(packet: dict[str, Any], *, agent_id: str | None = None,
                   activity_id: str = "grounded_qa", timestamp: str = "") -> list[ProvenanceEntry]:
    """Build the checksummed PROV-O derivation graph for one answer packet.

    Returns entries ordered [answer, chunk*, document*]. `packet` needs `question` and a citation
    list under `source_inspection` (preferred) or `citations`; each citation needs an artifact id,
    a chunk id, a quote, and ideally source_uri/location. Deterministic: entity ids and checksums
    are derived from content + the passed `timestamp` (no wall-clock)."""
    question = str(packet.get("question") or "")
    status = str(packet.get("answer_status") or "")
    engine = str(packet.get("answer_engine") or "deterministic")
    agent = agent_id or f"sciway:{engine}"
    citations = packet.get("source_inspection") or packet.get("citations") or []

    chunk_entries: list[ProvenanceEntry] = []
    document_entries: dict[str, ProvenanceEntry] = {}
    used: list[str] = []
    for cit in citations:
        artifact_id = _cit_field(cit, "artifact_id")
        chunk_id = _cit_field(cit, "chunk_id") or artifact_id
        if not chunk_id:
            continue
        source_uri = _cit_field(cit, "source_uri")
        doc_entity_id = f"document:{artifact_id}" if artifact_id else f"document:{chunk_id}"
        rcs = cit.get("rcs_relevance")
        confidence = round(rcs / 10.0, 3) if isinstance(rcs, (int, float)) else 1.0
        meta = {"retrieval_score": cit.get("retrieval_score") or cit.get("score"),
                "evidence_status": cit.get("evidence_status")}
        if rcs is not None:
            meta["rcs_relevance"] = rcs
        chunk_entries.append(finalize(ProvenanceEntry(
            entity_id=chunk_id, entity_type="chunk", activity_id=activity_id, agent_id=agent,
            source_document=source_uri or doc_entity_id,
            source_location=_cit_field(cit, "location", "page_or_row_reference"),
            source_quote=_cit_field(cit, "chunk_quote", "quote"), timestamp=timestamp,
            confidence=confidence, parent_entity_id=doc_entity_id,
            metadata={k: v for k, v in meta.items() if v is not None})))
        used.append(chunk_id)
        if doc_entity_id not in document_entries:
            document_entries[doc_entity_id] = finalize(ProvenanceEntry(
                entity_id=doc_entity_id, entity_type="document", activity_id="ingestion",
                agent_id="sciway:ingest", source_document=source_uri or artifact_id,
                timestamp=timestamp,
                metadata={"artifact_id": artifact_id, "title": _cit_field(cit, "artifact_title", "title"),
                          "source_system": _cit_field(cit, "source_family", "source_system")}))

    answer_id = f"answer:{_sha(question + '␟' + timestamp)[:16]}"
    answer_entry = finalize(ProvenanceEntry(
        entity_id=answer_id, entity_type="answer", activity_id=activity_id, agent_id=agent,
        timestamp=timestamp, used_entities=used,
        metadata={"question": question, "answer_status": status,
                  "draft_answer": str(packet.get("draft_answer") or ""),
                  "refused": status == "insufficient_evidence"}))
    return [answer_entry, *chunk_entries, *document_entries.values()]


def trace_lineage(entries: list[ProvenanceEntry], entity_id: str) -> list[ProvenanceEntry]:
    """Walk the derivation graph from `entity_id` back to its roots (documents), following
    `used_entities` and `parent_entity_id`. Returns the reachable entries in discovery order
    (the start entity first), each visited once."""
    by_id = {e.entity_id: e for e in entries}
    order: list[ProvenanceEntry] = []
    seen: set[str] = set()
    queue = [entity_id]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        entry = by_id.get(cur)
        if entry is None:
            continue
        order.append(entry)
        queue.extend(entry.used_entities)
        if entry.parent_entity_id:
            queue.append(entry.parent_entity_id)
    return order


def verify_lineage(entries: list[ProvenanceEntry | dict[str, Any]]) -> list[str]:
    """Return the entity_ids whose checksum fails to verify (tampered/corrupted). Empty == intact."""
    bad: list[str] = []
    for e in entries:
        eid = e.get("entity_id") if isinstance(e, dict) else e.entity_id
        if not verify_checksum(e):
            bad.append(str(eid))
    return bad


def _curie(kind: str, value: str) -> str:
    """A stable `sciway:<kind>/<slug>` CURIE for a shared PROV activity/agent node."""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "x"
    return f"sciway:{kind}/{slug}"


def prov_jsonld_nodes(entries: list[ProvenanceEntry]) -> list[dict[str, Any]]:
    """Render a lineage's entries as W3C PROV-O JSON-LD `@graph` nodes (the caller supplies
    `@context`; use `PROV_CONTEXT`). Entities become `prov:Entity` with `prov:wasDerivedFrom`
    (chunks for an answer, the document for a chunk); the answer also carries `prov:wasGeneratedBy`
    (a shared `prov:Activity`) and `prov:wasAttributedTo` (a shared `prov:SoftwareAgent`). The
    per-entry SHA-256 travels as `sciway:checksum` so the linked-data form stays tamper-evident.
    Activity/agent nodes are emitted once here; a multi-lineage merge de-dups them by `@id`."""
    nodes: list[dict[str, Any]] = []
    activities: dict[str, dict] = {}
    agents: dict[str, dict] = {}
    for e in entries:
        derived = list(e.used_entities) + ([e.parent_entity_id] if e.parent_entity_id else [])
        node: dict[str, Any] = {
            "@id": e.entity_id, "@type": "prov:Entity",
            "sciway:entityType": e.entity_type, "sciway:checksum": e.checksum,
        }
        if e.timestamp:
            node["prov:generatedAtTime"] = e.timestamp
        if derived:
            node["prov:wasDerivedFrom"] = [{"@id": d} for d in derived]
        if e.source_document:
            node["sciway:sourceDocument"] = e.source_document
        if e.source_location:
            node["sciway:sourceLocation"] = e.source_location
        if e.entity_type == "chunk" and e.source_quote:
            node["prov:value"] = e.source_quote
        if e.entity_type == "answer":
            act, agent = _curie("activity", e.activity_id), _curie("agent", e.agent_id)
            node["prov:wasGeneratedBy"] = {"@id": act}
            node["prov:wasAttributedTo"] = {"@id": agent}
            node["prov:value"] = e.metadata.get("draft_answer", "")
            node["sciway:question"] = e.metadata.get("question", "")
            node["sciway:answerStatus"] = e.metadata.get("answer_status", "")
            activities.setdefault(act, {"@id": act, "@type": "prov:Activity",
                                        "sciway:name": e.activity_id})
            agents.setdefault(agent, {"@id": agent, "@type": ["prov:Agent", "prov:SoftwareAgent"],
                                      "sciway:name": e.agent_id})
        nodes.append({k: v for k, v in node.items() if v not in (None, "")})
    return nodes + list(activities.values()) + list(agents.values())


def prov_jsonld(lineages: list[list[ProvenanceEntry]]) -> dict[str, Any]:
    """A standalone W3C PROV-O JSON-LD document over one or more answer lineages (activity/agent/
    document nodes de-duplicated by `@id`)."""
    merged: dict[str, dict] = {}
    for entries in lineages:
        for node in prov_jsonld_nodes(entries):
            merged[node["@id"]] = {**merged.get(node["@id"], {}), **node}
    return {"@context": PROV_CONTEXT, "@graph": list(merged.values())}


def lineage_report(entries: list[ProvenanceEntry]) -> dict[str, Any]:
    """A compact, JSON-friendly summary of a lineage graph for an evidence export / audit view."""
    answer = next((e for e in entries if e.entity_type == "answer"), None)
    documents = sorted({e.source_document for e in entries
                        if e.entity_type == "document" and e.source_document})
    return {
        "provenance_model": "W3C PROV-O",
        "schema_version": PROV_SCHEMA_VERSION,
        "answer_id": answer.entity_id if answer else None,
        "agent": answer.agent_id if answer else None,
        "activity": answer.activity_id if answer else None,
        "answer_status": (answer.metadata.get("answer_status") if answer else None),
        "source_documents": documents,
        "entity_counts": {
            "answer": sum(1 for e in entries if e.entity_type == "answer"),
            "chunk": sum(1 for e in entries if e.entity_type == "chunk"),
            "document": sum(1 for e in entries if e.entity_type == "document"),
        },
        "tamper_check": {"intact": not verify_lineage(entries),
                         "tampered_entities": verify_lineage(entries)},
        "entries": [e.to_dict() for e in entries],
    }
