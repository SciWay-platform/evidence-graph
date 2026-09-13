"""Per-project knowledge graph — a deterministic, stdlib-only concept map of a project's corpus.

Where a corpus comes with a hand-curated relations file, use it. Most do not, so this builds an
honest heuristic graph straight from the ingested chunks and says plainly what each layer is:

  • document nodes  — one per artifact (a file, a note, a record)
  • concept nodes   — salient keyword/measurement terms extracted from chunk text
  • "mentions" edges — document → concept (which file talks about which concept)
  • "co-occurs" edges — concept ↔ concept (terms that appear together in a chunk)

The co-occurrence layer is explicitly lexical, NOT LLM relation extraction — same "deterministic
before clever" contract as the rest of the library. ON TOP of it we overlay deterministic,
regex-only TYPED edges (`relations.extract_typed_relations`): document→document `supports` /
`contradicts` / `decided` / `uses-protocol` edges, each carrying its own provenance. So a corpus
gets a real typed graph rather than a term cloud, without any model. Computed on demand from the
chunks (no index, no re-ingest); cheap up to roughly 5000 chunks per corpus, above which you want
a real graph store.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from evidence_graph.ontology import classify_concept
from evidence_graph.relations import extract_typed_relations

# Function words + retrieval-noise terms that should never become graph concepts. Kept inline
# (stdlib-only) and deliberately small; the goal is a readable map, not exhaustive NLP.
_STOP = frozenset(
    "the a an and or but if then else of to in on at by for with from into over under as is are "
    "was were be been being do does did has have had not no nor so than too very can will would "
    "should could may might must this that these those it its it's he she they them his her their "
    "we our you your i me my us who whom which what when where why how all any both each few more "
    "most other some such only own same out up down off above below again further once here there "
    "about between through during before after because while also using used use one two three "
    "fig figure table data result results method methods study using show shows showed found per "
    "et al via vs within without across among against".split()
)

# A "concept" token: a word of 3+ chars (letters/digits/_/-) OR a measurement like "ph", "40c",
# "5.5". Pure integers are dropped later (noise); decimals/units are kept (they are informative
# in lab text, e.g. pH 5.5, 40C, 150mm).
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}|\d+(?:\.\d+)?[a-z%]+|\d+\.\d+")
_PURE_INT = re.compile(r"^\d+$")


def _terms(text: str) -> list[str]:
    out = []
    for raw in _TOKEN.findall(text or ""):
        t = raw.lower().strip("-")
        if len(t) < 3 or t in _STOP or _PURE_INT.match(t):
            continue
        out.append(t)
    return out


def _node_category(artifact_type: str | None) -> str:
    return {
        "paper": "papers", "paper_summary": "papers",
        "table": "results", "note": "decisions", "document": "sops",
    }.get(artifact_type or "", "other")


def build_project_graph(
    artifacts: list[dict],
    chunks: list[dict],
    *,
    max_concepts: int = 40,
    min_doc_freq: int = 1,
    max_cooccur_edges: int = 120,
) -> dict:
    """Return {"nodes": [...], "edges": [...], "stats": {...}} for a project's corpus.

    Deterministic: terms are ranked by (document frequency, total frequency, term) and ties broken
    lexicographically, so the same corpus always yields the same graph (testable, no randomness).
    """
    artifacts_by_id = {a["id"]: a for a in artifacts}
    # chunks grouped per artifact, and per-chunk term sets (for co-occurrence)
    chunk_term_sets: list[tuple[str, set[str]]] = []        # (artifact_id, {terms})
    doc_terms: dict[str, set[str]] = defaultdict(set)        # artifact_id -> terms it mentions
    term_doc_freq: Counter = Counter()                       # term -> #artifacts mentioning it
    term_total_freq: Counter = Counter()                     # term -> total occurrences

    for ch in chunks:
        aid = ch.get("artifact_id", "")
        terms = _terms(ch.get("chunk_text", ""))
        if not terms:
            continue
        tset = set(terms)
        chunk_term_sets.append((aid, tset))
        for t in terms:
            term_total_freq[t] += 1
        for t in tset:
            if t not in doc_terms[aid]:        # count document frequency once per artifact
                term_doc_freq[t] += 1
            doc_terms[aid].add(t)

    # Rank and select the salient concepts.
    ranked = sorted(
        term_doc_freq.items(),
        key=lambda kv: (-kv[1], -term_total_freq[kv[0]], kv[0]),
    )
    selected = [t for t, df in ranked if df >= min_doc_freq][:max_concepts]
    selected_set = set(selected)

    nodes: list[dict] = []
    edges: list[dict] = []

    # Document nodes (always include every artifact, even if it yielded no concepts).
    for a in artifacts:
        nodes.append({
            "id": a["id"],
            "label": (a.get("title") or a["id"])[:60],
            "title": a.get("title") or a["id"],
            "category": _node_category(a.get("artifact_type")),
            "kind": "document",
            "artifact_type": a.get("artifact_type", ""),
            "source_system": a.get("source_system", "upload"),
            "source_uri": a.get("source_uri", ""),   # so the typed-edge inspect panel can link to source
        })

    # Concept nodes. Each is typed against the seed scientific ontology (technique / excipient /
    # property / molecule / condition) so a graph view can group and colour concepts by kind — the typed-KG
    # upgrade over a bare term cloud. Unknown terms stay type "term" (graceful on any corpus).
    concept_type_counts: Counter = Counter()
    for t in selected:
        node = {
            "id": f"concept::{t}",
            "label": t,
            "title": t,
            "category": "concept",
            "kind": "concept",
            "entity_type": "term",
            "doc_freq": term_doc_freq[t],
            "freq": term_total_freq[t],
        }
        onto = classify_concept(t)
        if onto:
            node["entity_type"] = onto["type"]
            node["canonical"] = onto["canonical"]
            node["title"] = f"{t} · {onto['type']} ({onto['canonical']})"
        concept_type_counts[node["entity_type"]] += 1
        nodes.append(node)

    # "mentions" edges: document -> concept. Tagged edge_class="lexical" so a viewer can de-emphasize
    # / toggle the lexical layer independently of the typed relation structure.
    for aid, terms in doc_terms.items():
        if aid not in artifacts_by_id:
            continue
        for t in sorted(terms & selected_set):
            edges.append({"source": aid, "target": f"concept::{t}", "type": "mentions",
                          "edge_class": "lexical", "weight": 1})

    # "co-occurs" edges: concept <-> concept within the same chunk (undirected, deduped). Also lexical.
    pair_weight: Counter = Counter()
    for _aid, tset in chunk_term_sets:
        present = sorted(tset & selected_set)
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                pair_weight[(present[i], present[j])] += 1
    top_pairs = sorted(pair_weight.items(), key=lambda kv: (-kv[1], kv[0]))[:max_cooccur_edges]
    for (a_term, b_term), w in top_pairs:
        edges.append({
            "source": f"concept::{a_term}", "target": f"concept::{b_term}",
            "type": "co-occurs", "edge_class": "lexical", "weight": w,
        })

    # Typed document→document relations (regex/stdlib, same vocabulary as the demo relations file).
    # These sit ON TOP of the lexical map: supports / contradicts / decided / uses-protocol, each
    # with provenance. They upgrade the bring-your-own graph from a term cloud to a memory graph.
    typed_edges = extract_typed_relations(artifacts, chunks)
    edges.extend(typed_edges)

    typed_counts: Counter = Counter(e["type"] for e in typed_edges)
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "documents": len(artifacts),
            "concepts": len(selected),
            "mentions_edges": sum(1 for e in edges if e["type"] == "mentions"),
            "cooccurrence_edges": sum(1 for e in edges if e["type"] == "co-occurs"),
            "typed_edges": len(typed_edges),
            "typed_edge_counts": dict(sorted(typed_counts.items())),
            # Ontology typing of concept nodes: how many concepts were grounded to a scientific type
            # (vs left as a generic "term"), and the per-type breakdown — powers the graph legend.
            "typed_concepts": sum(v for k, v in concept_type_counts.items() if k != "term"),
            "concept_type_counts": dict(sorted(concept_type_counts.items())),
            "method": ("lexical co-occurrence + deterministic regex typed-relation extraction "
                       "with cross-document entity linking "
                       "(supports/contradicts/decided/uses-protocol; not LLM relation extraction)"),
        },
    }
