# evidence-graph

Build knowledge graphs whose edges carry their evidence, or abstain from asserting them.

[![CI](https://github.com/SciWay-platform/evidence-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/SciWay-platform/evidence-graph/actions/workflows/ci.yml)
[![Security](https://github.com/SciWay-platform/evidence-graph/actions/workflows/security.yml/badge.svg)](https://github.com/SciWay-platform/evidence-graph/actions/workflows/security.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Most pipelines that turn documents into a knowledge graph optimise recall: a model reads text and
emits triples. Over scientific literature that produces graphs a researcher cannot act on, because
a plausible edge and a supported edge look identical once they are in the store.

This library optimises a different quantity: **the share of asserted edges someone can verify from
the cited sentence.** Every edge it proposes carries the passage it came from, its location in the
source document, and the trigger that fired. Where sources disagree it records the disagreement
instead of picking a winner. Where the evidence does not reach, the intended answer is nothing.

It is deterministic, standard library only, and has no runtime dependencies. It never opens a
socket and never reads a credential.

## Install

```bash
pip install evidence-graph        # not yet published; see Status
```

Or clone it. There is no build step, and the tests run with no install:

```bash
git clone https://github.com/SciWay-platform/evidence-graph.git
cd evidence-graph && python -m unittest discover -s tests
```

## Example

```python
from evidence_graph import RetrievalHit, detect_conflict, extract_typed_relations

artifacts = [
    {"id": "exp-078", "title": "Buffer screen", "artifact_type": "experiment", "source_system": "eln"},
    {"id": "exp-085", "title": "pH optimisation", "artifact_type": "experiment", "source_system": "eln"},
]
chunks = [
    {"id": "c1", "artifact_id": "exp-078", "chunk_text": "Lowest aggregation was observed at pH 5.5.",
     "source_uri": "eln://exp-078", "page_or_row_reference": "p.2"},
    {"id": "c2", "artifact_id": "exp-085", "chunk_text": "Lowest aggregation was observed at pH 7.0.",
     "source_uri": "eln://exp-085", "page_or_row_reference": "p.1"},
]

for edge in extract_typed_relations(artifacts, chunks):
    print(f"{edge['type']}: {edge['source']} -> {edge['target']}  (quantity: {edge['quantity']})")
    for ev in edge["evidence"]:
        print(f"    {ev['trigger']:<10} {ev['location']:<5} {ev['quote']}")

hits = [RetrievalHit(artifact_id=c["artifact_id"], chunk_id=c["id"], source_system="eln",
                     title=a["title"], score=1.0, quote=c["chunk_text"],
                     source_uri=c["source_uri"], location=c["page_or_row_reference"])
        for a, c in zip(artifacts, chunks)]

conflict = detect_conflict("Which pH minimised aggregation?", hits)
print(f"\nconflict ({conflict['kind']}): {conflict['summary']}")
for v in conflict["values"]:
    print(f"    {v['value']:<5} {v['artifact_title']} {v['location']}: {v['quote']}")
```

```
contradicts: exp-078 -> exp-085  (quantity: ph)
    ph=5.5     p.2   Lowest aggregation was observed at pH 5.5.
    ph=7.0     p.1   Lowest aggregation was observed at pH 7.0.

conflict (numeric): the cited sources report different ph values (5.5 vs 7.0)
    5.5   Buffer screen p.2: Lowest aggregation was observed at pH 5.5.
    7.0   pH optimisation p.1: Lowest aggregation was observed at pH 7.0.
```

No model ran. The contradiction was found by unit-aware numeric comparison, and both halves of it
are quoted back with their page references so a reader can check the claim against the source.

## What is in it

| Module | What it does |
|---|---|
| `evidence` | `RetrievalHit`: a passage plus the provenance needed to cite it. The input type. |
| `relations` | Proposes typed document→document edges (`supports`, `contradicts`, `decided`, `uses-protocol`) by regex and unit-aware numeric grammar. Each edge carries its evidence. |
| `conflicts` | Detects numeric disagreement (unit-aware) and documented contradiction between cited passages. High precision by design. |
| `graph` | Assembles documents, concepts and edges into a `{nodes, edges, stats}` payload. |
| `ontology` | Types concept terms against a controlled vocabulary. Currently a narrow seed; see Roadmap. |
| `provenance` | W3C PROV-O derivation records with SHA-256 tamper-evidence (`verify_lineage`). |
| `fair` | DataCite completeness report, Dublin Core XML, RO-Crate 1.1 JSON-LD. |
| `ro_crate_validation` | Structural validation of a crate against the RO-Crate 1.1 spec, offline. |

## Data shapes

Three dict shapes recur. They are inherited from the system this code was extracted from, and the
key names are not yet consistent between them (a chunk's id is `id`, a hit's is `chunk_id`).
Normalising that is a 1.0 change, so it is documented here rather than fixed quietly.

**Artifact** (a source document):
`id`, `title`, `artifact_type`, `source_system`, optional `author_or_owner`, `created_at`, and
`structured_metadata` carrying `doi`, `creators`, `publisher`, `publication_year`, `license`,
`keywords`.

**Chunk** (a passage of one artifact):
`id`, `artifact_id`, `chunk_text` (or `source_quote`), `source_uri`, `page_or_row_reference`.

**RetrievalHit**: see [`evidence.py`](evidence_graph/evidence.py).

## What it deliberately does not do

It does not retrieve, embed, call a model, or store anything. Those belong to the system embedding
it. When a step genuinely needs a model, the shape is a `Protocol` defined here and implemented by
the caller, so the library stays keyless and runs in an air-gapped lab.

It also knows nothing about users, tenants, projects, or requests.

## Roadmap

The near-term work, in order:

1. **Biomedical grounding.** `ontology` is a small hand-written vocabulary biased toward protein
   formulation. Replacing it with Biolink Model categories and OBO CURIEs (MONDO, ChEBI, GO,
   UBERON, CL, HGNC) is the next substantial piece.
2. **An adjudication layer.** Turning each proposed edge into an explicit assert / abstain /
   conflict verdict over a scorer `Protocol`, with a deterministic default.
3. **KGX export**, so a graph built here loads into other tooling.
4. **Edge-level evaluation**: asserted-edge precision as a function of abstention rate, on a public
   corpus. This is the number we most want to publish, and the one nobody currently reports.

## Status

**0.1.0.dev0.** The API will change and there is no support SLA. It is extracted from
[SciWay](https://sciway.software), a scientific memory workspace, where this code has been running
in production; the extraction is what makes it independently reviewable and citable.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for scope (the boundary is strict and most review effort goes
there), [SECURITY.md](SECURITY.md) for vulnerability reporting, and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Licence

[Apache-2.0](LICENSE). Code adapted from other projects is attributed in [NOTICE](NOTICE).
