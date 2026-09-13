# Example output: 32 open-access antibody-formulation papers

Real output, regenerable from a clean checkout:

```bash
python scripts/fetch_pmc_corpus.py --query "protein aggregation monoclonal antibody formulation stability" \
    --name mab-aggregation --limit 45
python scripts/run_pipeline.py corpora/mab-aggregation --out build/mab-aggregation
```

The corpus is 32 CC BY articles from the PubMed Central Open Access subset, 5,333 passages. The run
takes about 13 seconds and needs no model, no key and no network.

## What came out

See [`summary.json`](summary.json). In short:

| | |
|---|---|
| candidate edges proposed | 12,185 |
| asserted | 10,684 |
| **abstained (open questions)** | **452** |
| recorded as conflicts | 1,049 |
| concepts grounded to CURIEs | 3,237 of 431,949 candidate strings |
| graph | 1,508 nodes, 16,357 edges, valid KGX |

The grounding number is the one that looks wrong and is not. Of 431,949 candidate word and phrase
spans pulled from the text, 0.75% ground to an ontology identifier. Nearly every string in a paper
is prose, and a grounder that matched more than this would be matching things that are not concepts.

## The files

- `kgx_nodes.sample.tsv`, `kgx_edges.sample.tsv` — the first rows of the KGX export.
- `open_questions.sample.json` — five of the 452 edges the adjudicator declined to assert, each
  with the passages it read and the reason it was not convinced. This is the half of the output a
  curator works through, and it is why the graph above can be trusted more than a graph of 12,185
  edges would be.

## What this does not show

There is no precision figure here, because this corpus has no gold standard. `evidence_graph.evaluation`
implements the risk-coverage measurement and is tested, but running it needs someone to read the
sources and label whether each edge is right. Publishing a precision number derived from the same
checks that produced the assertions would be circular, so there isn't one yet.
