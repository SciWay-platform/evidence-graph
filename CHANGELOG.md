# Changelog

## 0.1.1 — 2026-11-03

The first release on PyPI. Packaging only: the library code is the same as in 0.1.0, which was
tagged but never uploaded.

### Changed

- `pip install evidence-graph` works. Releases are uploaded by `.github/workflows/release.yml`
  through PyPI Trusted Publishing, so no upload token exists in the repository or its secrets.
- Added the `py.typed` marker. The `Typing :: Typed` classifier already claimed the package was
  typed, but without the marker type checkers ignored its annotations.
- Dropped the `License ::` classifier. `license = "Apache-2.0"` is the PEP 639 declaration, and
  the classifier is deprecated next to it.
- README links are absolute, so they also resolve on the PyPI project page.

## 0.1.0 — 2026-09-14

First release. Extracted from [SciWay](https://sciway.software), then extended with the pieces a
biomedical knowledge graph actually needs.

### Added

- **`grounding`** — surface strings to MONDO, ChEBI, GO, UBERON, CL, HGNC and UniProt CURIEs, with
  the Biolink category each implies. 369,627 terms vendored as 10.8 MB of gzipped TSV, read with
  stdlib gzip. Refuses most strings; reports ambiguity rather than resolving it; no edit-distance
  fallback.
- **`biolink`** — Biolink Model 4.4.4 categories, predicates and CURIE typing. Returns None for the
  ~50 prefixes declared by several classes instead of guessing.
- **`adjudicate`** — assert / abstain / conflict per candidate edge over an `EdgeScorer` Protocol.
  Default scorer is keyless and deterministic and is explicit that it scores grounding, not truth.
- **`maintain`** — re-runs that append revisions instead of overwriting. Edges are weakened,
  contradicted or withdrawn, never silently deleted.
- **`evaluation`** — edge-level precision as a function of abstention, against an explicit
  no-abstention baseline, with a dependency-free SVG renderer.
- **`kgx`** — KGX interchange in TSV and JSON Lines, including the Biolink-required
  `knowledge_level` and `agent_type` that most KGX files omit.
- **`pipeline`** — the whole spine in one call, plus `scripts/run_pipeline.py`.
- **`evidence`** — `RetrievalHit` as the library's public input type.
- **`ro_crate_validation`** — the RO-Crate structural validator, decoupled from any application.
- Corpus builder for the PubMed Central Open Access subset on AWS Open Data, filtering on licence.

### Fixed

Four defects that only a real corpus exposed, three of them in code that was already in production:

- `relations` truncated evidence quotes to the first 160 characters while the matched value could
  be anywhere in the passage, so **a citation did not necessarily contain what it was cited for**.
  Quotes are now windowed on the match. Adjudicated coverage moved from 6.5% to 53.8%.
- Evidence notes carried the normalised entity key, which by construction never appears in the
  source text, making those citations unverifiable. Each side now quotes its own surface form.
- The entity and measurement grammars matched prose and identifiers: `at 0.01%` became an entity,
  `PMC12345` and `IgG1` became measured quantities. Candidate edges fell from 18,191 to 12,185.
- `adjudicate` treated descriptive trigger notes as verifiable claims about the quote, and required
  a normalised label to appear in prose where it never does.

Plus, in new code: `Vocabulary.__len__` made an empty vocabulary falsy, so `vocabulary or
load_vocabulary()` silently loaded the real data; and lowercase prose grounded to gene symbols
(`for` to WWOX) until surface shape was required for symbol vocabularies.

### Known limitations

- No gold standard for the bundled corpus, so no published precision figure.
- The `sciway:` JSON-LD namespace in RO-Crate and PROV-O output, and the `id`/`chunk_id` key
  inconsistency between chunks and hits, are both inherited and both breaking changes to fix.
