"""KGX interchange — writing a graph other people's biomedical tooling can load.

KGX is the exchange format of the Biolink/Translator ecosystem. Emitting it is what turns a graph
built here from a private data structure into something a collaborator can import.

**The awkward part, stated plainly.** Biolink is entity-centric: an edge *is* a statement about two
entities, and there is no native way to say "document D contradicts document E about pH". Verified
against Biolink 4.4.4 by enumerating all 247 descendants of `related to`: none of `supports`,
`contradicts`, `decided`, or `uses_protocol` exists, and an Association cannot be the subject of
another Association (`subject` has `range: named thing`; `association` is not a `named thing`).

So this module does two things rather than pretend one is enough:

  * The relation goes in `predicate` under our own namespace (`eg:contradicts` and friends). The
    KGX specification explicitly permits non-Biolink properties, so the file stays parseable.
  * Every edge also carries `broad_predicate: biolink:related_to`, a real Biolink predicate, so a
    consumer that only understands Biolink can still traverse the graph rather than drop the edge.

Evidence rides on the edge as Biolink association slots — `publications`, `supporting_text`,
`has_evidence_count` — which is how Biolink models "what supports this statement". That is the one
place where Biolink's design and this library's agree exactly.

`knowledge_level` and `agent_type` are **required** on every edge in Biolink 4.x and are the most
commonly missed fields in KGX files. They are set honestly here rather than filled with whatever
passes a validator: see `KNOWLEDGE_LEVEL_BY_EDGE_CLASS`.

Everything is pure: functions return strings, callers decide where bytes go.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from evidence_graph.adjudicate import Adjudication, AdjudicationResult, Verdict
from evidence_graph.biolink import DEFAULT_CATEGORY, category_chain

#: The Biolink Model release this module was written against and verified with.
BIOLINK_VERSION = "4.4.4"

#: Our own namespace, for the assertion-level relations Biolink does not model.
EG_PREFIX = "eg"
EG_VOCAB = "https://sciway-platform.github.io/evidence-graph/vocab/"

#: A real Biolink predicate attached to every edge so Biolink-only consumers can still traverse it.
BROAD_PREDICATE = "biolink:related_to"

#: `KnowledgeLevelEnum`, verbatim from Biolink 4.4.4.
KNOWLEDGE_LEVELS = frozenset({
    "knowledge_assertion", "logical_entailment", "prediction",
    "statistical_association", "text_co_occurrence", "observation", "not_provided",
})

#: `AgentTypeEnum`, verbatim from Biolink 4.4.4.
AGENT_TYPES = frozenset({
    "manual_agent", "automated_agent", "data_analysis_pipeline", "computational_model",
    "text_mining_agent", "image_processing_agent", "manual_validation_of_automated_agent",
    "not_provided",
})

#: How each edge class maps onto `knowledge_level`.
#:
#: The lexical layer genuinely is text co-occurrence, so it says so. The typed layer is deliberately
#: `not_provided`: none of the seven enum values honestly describes deterministic extraction of an
#: explicit numeric claim from a sentence. `prediction` implies a model we did not run,
#: `knowledge_assertion` implies a curator who did not read it, and `text_co_occurrence` understates
#: a parsed measurement. `not_provided` is a legal value, and claiming less than we can defend is
#: the whole posture of this library. Callers with a defensible mapping should pass their own.
KNOWLEDGE_LEVEL_BY_EDGE_CLASS = {
    "lexical": "text_co_occurrence",
    "typed": "not_provided",
}

#: We are, precisely, a text-mining agent. This one is unambiguous.
DEFAULT_AGENT_TYPE = "text_mining_agent"

# Column order for the TSV writer, from kgx/sink/tsv_sink.py. Core columns first in this order (only
# those actually present), then everything else alphabetically.
NODE_CORE_COLUMNS = ["id", "category", "name", "description", "xref", "provided_by",
                     "synonym", "exact_synonym", "broad_synonym", "narrow_synonym",
                     "related_synonym"]
EDGE_CORE_COLUMNS = ["id", "subject", "predicate", "object", "category", "relation", "provided_by"]

#: TSV multi-value separator (`DEFAULT_LIST_DELIMITER` in KGX).
LIST_DELIMITER = "|"


@dataclass(frozen=True)
class KgxGraph:
    """A KGX node and edge set, ready to serialise."""

    nodes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    edges: tuple[dict[str, Any], ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------------


def artifact_curie(artifact: dict[str, Any]) -> str:
    """A CURIE for a source document, preferring identifiers the wider world can resolve.

    A DOI beats a PMC id beats a local id, because the point of interchange is that someone else's
    tooling recognises the node. A local id is namespaced under `eg:` so it can never be mistaken
    for a resolvable identifier.
    """
    meta = artifact.get("structured_metadata") or {}
    doi = str(meta.get("doi") or "").strip()
    if doi:
        return f"doi:{doi.removeprefix('https://doi.org/').removeprefix('doi:')}"
    pmid = str(meta.get("pmid") or "").strip()
    identifier = str(artifact.get("id", "")).strip()
    if identifier.upper().startswith("PMC"):
        return f"PMC:{identifier}"
    if pmid:
        return f"PMID:{pmid}"
    return f"{EG_PREFIX}:{identifier}"


def edge_id(subject: str, predicate: str, obj: str, qualifier: str = "") -> str:
    """A stable, content-derived edge id.

    KGX treats edge `id` as optional in practice but Biolink inherits a required `id` from `entity`,
    and the cost of emitting one is nothing. Deriving it from content rather than a random UUID
    keeps a re-export byte-identical, which matters when the output is committed next to the code.
    """
    digest = hashlib.sha256("|".join((subject, predicate, obj, qualifier)).encode("utf-8")).hexdigest()
    return f"urn:uuid:{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


# --------------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------------


def _publication_node(artifact: dict[str, Any]) -> dict[str, Any]:
    meta = artifact.get("structured_metadata") or {}
    node: dict[str, Any] = {
        "id": artifact_curie(artifact),
        "category": ["biolink:Publication"],
        "name": artifact.get("title") or artifact.get("id"),
        "provided_by": [f"infores:{artifact.get('source_system', 'unknown')}"],
    }
    xrefs = [x for x in (
        f"PMC:{artifact['id']}" if str(artifact.get("id", "")).upper().startswith("PMC") else "",
        f"PMID:{meta['pmid']}" if meta.get("pmid") else "",
    ) if x and x != node["id"]]
    if xrefs:
        node["xref"] = xrefs
    if meta.get("publication_year"):
        node["publication_year"] = meta["publication_year"]
    if meta.get("license"):
        node["license"] = meta["license"]
    return node


def from_adjudications(
    result: AdjudicationResult,
    artifacts: list[dict[str, Any]],
    *,
    knowledge_source: str = "infores:evidence-graph",
    agent_type: str = DEFAULT_AGENT_TYPE,
    include_conflicted: bool = True,
) -> KgxGraph:
    """Build a KGX graph from adjudicated edges.

    **Only asserted edges become graph edges.** Abstentions are not exported: an abstained edge is
    an open question, and writing it into an interchange file would hand a collaborator exactly the
    unverified assertion this library exists to withhold. Use `adjudicate.open_questions` for the
    review queue instead.

    Conflicted edges ARE exported, carrying `eg:conflicted` and the keys they disagree with, because
    a recorded disagreement is a finding a collaborator should see. Set `include_conflicted=False`
    to emit only settled assertions.
    """
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"agent_type {agent_type!r} is not a Biolink AgentTypeEnum value")

    by_id = {a["id"]: a for a in artifacts}
    kept: list[Adjudication] = [a for a in result.adjudications if a.verdict is Verdict.ASSERT]
    if include_conflicted:
        kept += [a for a in result.adjudications if a.verdict is Verdict.CONFLICT]

    used_artifacts: set[str] = set()
    edges: list[dict[str, Any]] = []

    for adjudication in kept:
        edge = adjudication.edge
        source_art, target_art = str(edge.get("source", "")), str(edge.get("target", ""))
        if source_art not in by_id or target_art not in by_id:
            continue
        used_artifacts.update((source_art, target_art))

        subject, obj = artifact_curie(by_id[source_art]), artifact_curie(by_id[target_art])
        predicate = f"{EG_PREFIX}:{str(edge.get('type', 'related_to')).replace('-', '_')}"
        quantity = str(edge.get("quantity", ""))
        evidence = edge.get("evidence") or []

        record: dict[str, Any] = {
            "id": edge_id(subject, predicate, obj, quantity),
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "broad_predicate": BROAD_PREDICATE,
            "knowledge_level": KNOWLEDGE_LEVEL_BY_EDGE_CLASS.get(
                str(edge.get("edge_class", "")), "not_provided"
            ),
            "agent_type": agent_type,
            "primary_knowledge_source": knowledge_source,
            "publications": sorted({artifact_curie(by_id[a]) for a in (source_art, target_art)}),
            "has_evidence_count": len(evidence),
            # The verbatim passages. `supporting_text` is a real Biolink slot and is the reason a
            # reader can check this edge without returning to the corpus.
            "supporting_text": [ev.get("quote", "") for ev in evidence if ev.get("quote")],
            f"{EG_PREFIX}:adjudication": str(adjudication.verdict),
            f"{EG_PREFIX}:score": adjudication.score,
            f"{EG_PREFIX}:rationale": adjudication.rationale,
        }
        if quantity:
            record[f"{EG_PREFIX}:quantity"] = quantity
        locations = [ev.get("location", "") for ev in evidence if ev.get("location")]
        if locations:
            record[f"{EG_PREFIX}:evidence_location"] = locations
        if adjudication.verdict is Verdict.CONFLICT:
            record[f"{EG_PREFIX}:conflicts_with"] = list(adjudication.conflicts_with)
        edges.append(record)

    nodes = [_publication_node(by_id[a]) for a in sorted(used_artifacts)]
    edges.sort(key=lambda e: (e["subject"], e["predicate"], e["object"], e["id"]))
    return KgxGraph(tuple(nodes), tuple(edges))


def with_concept_mentions(
    graph: KgxGraph,
    mentions: list[dict[str, Any]],
    *,
    knowledge_source: str = "infores:evidence-graph",
    agent_type: str = DEFAULT_AGENT_TYPE,
) -> KgxGraph:
    """Add grounded concept nodes and document-to-concept edges.

    Unlike the assertion-level relations, this part maps cleanly onto Biolink: `biolink:mentions` is
    a real predicate, and a grounded concept carries a real category from its CURIE prefix. So these
    edges are fully Biolink-native and a Translator-ecosystem consumer can use them directly.

    `knowledge_level` is `text_co_occurrence` here and means it: the claim is that the document
    mentions the concept, which is exactly what was observed.

    Each mention is `{document, curie, label, category, quote, location, chunk_id}`.
    """
    nodes = {n["id"]: dict(n) for n in graph.nodes}
    edges = list(graph.edges)
    seen: set[tuple[str, str]] = {(e["subject"], e["object"]) for e in edges}

    for mention in sorted(mentions, key=lambda m: (m["document"], m["curie"])):
        document, curie = mention["document"], mention["curie"]
        if document not in nodes or (document, curie) in seen:
            continue
        seen.add((document, curie))
        nodes.setdefault(curie, {
            "id": curie,
            "category": list(category_chain(mention.get("category") or DEFAULT_CATEGORY)),
            "name": mention.get("label") or curie,
            "provided_by": [knowledge_source],
        })
        record = {
            "id": edge_id(document, "biolink:mentions", curie),
            "subject": document,
            "predicate": "biolink:mentions",
            "object": curie,
            "broad_predicate": BROAD_PREDICATE,
            "knowledge_level": "text_co_occurrence",
            "agent_type": agent_type,
            "primary_knowledge_source": knowledge_source,
            "supporting_text": [mention["quote"]] if mention.get("quote") else [],
        }
        if mention.get("location"):
            record[f"{EG_PREFIX}:evidence_location"] = [mention["location"]]
        edges.append(record)

    ordered_nodes = tuple(nodes[k] for k in sorted(nodes))
    edges.sort(key=lambda e: (e["subject"], e["predicate"], e["object"], e["id"]))
    return KgxGraph(ordered_nodes, tuple(edges))


# --------------------------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------------------------


def _order_columns(records: list[dict[str, Any]], core: list[str]) -> list[str]:
    present = {key for record in records for key in record}
    ordered = [c for c in core if c in present]
    rest = sorted(k for k in present if k not in ordered and not k.startswith("_"))
    internal = sorted(k for k in present if k.startswith("_"))
    return ordered + rest + internal


def _tsv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return LIST_DELIMITER.join(_tsv_cell(v) for v in value)
    # Tabs and newlines inside a quote would break the row; TSV has no quoting convention.
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def to_tsv(graph: KgxGraph) -> tuple[str, str]:
    """Serialise as the KGX TSV pair, returned as `(nodes_tsv, edges_tsv)`.

    Multi-valued fields use the pipe delimiter KGX defaults to. TSV has no quoting convention, so
    tabs and newlines inside a quoted passage are collapsed to spaces — the JSON Lines form below
    preserves them exactly and is the better choice when the text matters.
    """
    def render(records: list[dict[str, Any]], core: list[str]) -> str:
        if not records:
            return ""
        columns = _order_columns(records, core)
        lines = ["\t".join(columns)]
        lines += ["\t".join(_tsv_cell(record.get(c)) for c in columns) for record in records]
        return "\n".join(lines) + "\n"

    return render(list(graph.nodes), NODE_CORE_COLUMNS), render(list(graph.edges), EDGE_CORE_COLUMNS)


def to_jsonl(graph: KgxGraph) -> tuple[str, str]:
    """Serialise as the KGX JSON Lines pair. Multi-valued fields stay real JSON arrays."""

    def render(records: tuple[dict[str, Any], ...]) -> str:
        return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)

    return render(graph.nodes), render(graph.edges)


# --------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------


def validate_kgx(graph: KgxGraph) -> list[str]:
    """Check the structural rules a KGX consumer relies on. Empty list means valid.

    Deliberately checks the two fields most often missing from real KGX files — `knowledge_level`
    and `agent_type`, required on edges since Biolink 4.x — and that every edge endpoint resolves
    to a node, which is the dangling-reference failure a consumer cannot recover from.
    """
    issues: list[str] = []
    ids: set[str] = set()

    for node in graph.nodes:
        if not node.get("id"):
            issues.append("node without an id")
            continue
        if node["id"] in ids:
            issues.append(f"duplicate node id {node['id']!r}")
        ids.add(node["id"])
        categories = node.get("category")
        if not categories or not isinstance(categories, (list, tuple)):
            issues.append(f"node {node['id']!r} has no category list")
        elif not all(str(c).startswith("biolink:") for c in categories):
            issues.append(f"node {node['id']!r} has a non-Biolink category: {categories}")

    for edge in graph.edges:
        label = edge.get("id") or f"{edge.get('subject')}->{edge.get('object')}"
        for required in ("subject", "predicate", "object"):
            if not edge.get(required):
                issues.append(f"edge {label!r} is missing {required}")
        level = edge.get("knowledge_level")
        if level not in KNOWLEDGE_LEVELS:
            issues.append(f"edge {label!r} has knowledge_level {level!r}, not a Biolink enum value")
        agent = edge.get("agent_type")
        if agent not in AGENT_TYPES:
            issues.append(f"edge {label!r} has agent_type {agent!r}, not a Biolink enum value")
        for endpoint in ("subject", "object"):
            value = edge.get(endpoint)
            if value and value not in ids:
                issues.append(f"edge {label!r} references missing node {value!r} (via {endpoint})")

    return issues
