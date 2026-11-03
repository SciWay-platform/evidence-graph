# evidence-graph

Build knowledge graphs whose edges carry their evidence, or abstain from asserting them.

[![CI](https://github.com/SciWay-platform/evidence-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/SciWay-platform/evidence-graph/actions/workflows/ci.yml)
[![Security](https://github.com/SciWay-platform/evidence-graph/actions/workflows/security.yml/badge.svg)](https://github.com/SciWay-platform/evidence-graph/actions/workflows/security.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/SciWay-platform/evidence-graph/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://github.com/SciWay-platform/evidence-graph/blob/main/pyproject.toml)

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
pip install evidence-graph
```

The wheel is about 11 MB, nearly all of it the vendored vocabulary, and installs nothing else.

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

## A whole corpus

From a clone; the scripts ship with the repository, not the wheel.

```bash
# Build a corpus from the PubMed Central Open Access subset on AWS Open Data (no credentials).
python scripts/fetch_pmc_corpus.py --query "protein aggregation monoclonal antibody formulation" \
    --name mab-aggregation --limit 45

# Propose, ground, adjudicate, reconcile, export.
python scripts/run_pipeline.py corpora/mab-aggregation --out build/mab-aggregation
```

32 open-access papers, 5,333 passages, about 13 seconds, no model and no network:

| | |
|---|---|
| candidate edges proposed | 12,185 |
| asserted | 10,684 |
| **abstained — the review queue** | **452** |
| recorded as conflicts rather than resolved | 1,049 |
| concepts grounded to CURIEs | 3,237 |
| graph | 1,508 nodes, 16,357 edges, valid KGX |

Real output is in
[`examples/mab-aggregation/`](https://github.com/SciWay-platform/evidence-graph/tree/main/examples/mab-aggregation/),
including five of the open questions with the passages the adjudicator read and why it was not
convinced.

## What is in it

| Module | What it does |
|---|---|
| `evidence` | `RetrievalHit`: a passage plus the provenance needed to cite it. The input type. |
| `relations` | Proposes typed document→document edges (`supports`, `contradicts`, `decided`, `uses-protocol`) by regex and unit-aware numeric grammar. Each edge carries its evidence. |
| `grounding` | Resolves a surface string to a MONDO, ChEBI, GO, UBERON, CL, HGNC or UniProt CURIE, or refuses. 369,627 terms vendored as 10.8 MB of gzipped TSV. |
| `biolink` | Biolink Model 4.4.4 categories, predicates, and CURIE typing that declines to guess. |
| `adjudicate` | The gate: assert, abstain, or record a conflict, over a pluggable scorer. |
| `conflicts` | Detects numeric disagreement (unit-aware) and documented contradiction between cited passages. |
| `maintain` | Re-runs without overwriting: per-edge revision history, withdrawal instead of deletion. |
| `evaluation` | Edge-level precision as a function of abstention, with an SVG renderer. |
| `kgx` | KGX interchange, TSV and JSON Lines, Biolink-compatible. |
| `pipeline` | All of the above in one call. |
| `graph` | Assembles documents, concepts and edges into a `{nodes, edges, stats}` payload. |
| `ontology` | Types concept terms against a small built-in vocabulary. Superseded by `grounding` for biomedical work. |
| `provenance` | W3C PROV-O derivation records with SHA-256 tamper-evidence (`verify_lineage`). |
| `fair` | DataCite completeness report, Dublin Core XML, RO-Crate 1.1 JSON-LD. |
| `ro_crate_validation` | Structural validation of a crate against the RO-Crate 1.1 spec, offline. |

## Three things worth knowing before you use it

**Abstention is an output, not a silence.** `adjudicate` returns assert, abstain or conflict for
every candidate. Abstained edges keep their evidence and come back through
`pipeline.open_questions`; they are never dropped. Conflicts are settled by recording both sides,
not by keeping the higher-scoring one.

**Grounding refuses most strings, and that is the correct behaviour.** Of 431,949 candidate spans in
the corpus above, 0.75% ground to an identifier. Matching is exact after normalisation with no
edit-distance fallback, because fuzzy matching at ontology scale grounds `IL-6` to `IL-16`, and a
wrong identifier is a citation that looks checkable and is not. Ambiguous surfaces come back with
every candidate rather than resolved by a coin-flip.

**Biolink cannot express these relations, and the export says so.** Biolink is entity-centric: an
edge *is* a statement, and there is no `supports` or `contradicts` predicate anywhere in the 247
descendants of `related to`. `kgx` puts the relation in its own namespace and attaches
`broad_predicate: biolink:related_to` so a Biolink-only consumer can still traverse the graph.
Grounded concept mentions, by contrast, map cleanly and use `biolink:mentions`.

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

## Vendored data

`evidence_graph/data/` holds the vocabulary as gzipped TSV, built by `scripts/build_vocabulary.py`
from MONDO, GO, ChEBI, UBERON, CL, HGNC and UniProt. That data is **not** covered by this project's
Apache-2.0 licence: each source keeps its own (CC0, CC BY 4.0, CC BY 3.0). Sources, attributions and
a statement of the modifications made are in [`evidence_graph/data/NOTICE`](https://github.com/SciWay-platform/evidence-graph/blob/main/evidence_graph/data/NOTICE).

**RetrievalHit**: see [`evidence.py`](https://github.com/SciWay-platform/evidence-graph/blob/main/evidence_graph/evidence.py).

## What it deliberately does not do

It does not retrieve, embed, call a model, or store anything. Those belong to the system embedding
it. When a step genuinely needs a model, the shape is a `Protocol` defined here and implemented by
the caller, so the library stays keyless and runs in an air-gapped lab.

It also knows nothing about users, tenants, projects, or requests.

## What is missing

**A gold standard.** `evaluation` implements and tests the risk-coverage measurement, but no
labelled edge set exists for the corpus above, so this repository publishes no precision figure.
Deriving one from the same checks that produced the assertions would be circular. Labelling a
sample is the next piece of work, and it needs someone to read the sources.

**A semantic scorer.** The default `GroundingScorer` verifies that an edge is grounded in the text
it cites, which is necessary and not sufficient for correctness: a perfectly grounded edge can still
be a wrong reading of the sentence. `EdgeScorer` is a Protocol precisely so a model can be dropped
in; the library ships the half that needs no key.

**Better proposers.** Relation extraction is regex and numeric grammar. It is auditable and fast and
it misses everything stated in prose without a number in it.

## Status

**0.1.1.** The API will change and there is no support SLA. It is extracted from
[SciWay](https://sciway.software), a scientific memory workspace, where this code has been running
in production; the extraction is what makes it independently reviewable and citable.

## Contributing

See [CONTRIBUTING.md](https://github.com/SciWay-platform/evidence-graph/blob/main/CONTRIBUTING.md) for scope (the boundary is strict and most
review effort goes there), [SECURITY.md](https://github.com/SciWay-platform/evidence-graph/blob/main/SECURITY.md) for vulnerability reporting, and
[CODE_OF_CONDUCT.md](https://github.com/SciWay-platform/evidence-graph/blob/main/CODE_OF_CONDUCT.md).

## Licence

[Apache-2.0](https://github.com/SciWay-platform/evidence-graph/blob/main/LICENSE). Code adapted from other projects is attributed in [NOTICE](https://github.com/SciWay-platform/evidence-graph/blob/main/NOTICE).
