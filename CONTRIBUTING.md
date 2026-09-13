# Contributing to evidence-graph

Thanks for looking. This is a research library at 0.x: the API will change, and there is no support
SLA. Issues and pull requests are welcome on that understanding.

## Scope

The library is deliberately narrow, and most of the review effort on a pull request goes into
whether a change belongs here at all. Two rules define the boundary:

**1. No network, no credentials, no models.** The library never opens a socket, never reads an API
key, and never imports a model at runtime. It has zero runtime dependencies and CI enforces that
(`scripts/check_no_runtime_deps.py` walks every import in the package and fails on anything outside the
standard library).

This is not minimalism for its own sake. A library that decides whether evidence supports a claim
has to be auditable by the scientist relying on it, and has to run inside an air-gapped lab. The
moment it needs a key, both properties are gone.

If your change genuinely needs a model — an LLM that scores whether a chunk supports an edge, say —
the right shape is a `Protocol` defined here and implemented by the caller. The library states what
it needs; it does not reach out and get it.

**2. No knowledge of users, tenants, projects, or requests.** Those concepts belong to whatever
application embeds this. The library's world is text with provenance in, asserted edges with
lineage out.

## What a good contribution looks like

- **A failing test first.** Every module here is deterministic and pure, so almost any bug can be
  written as a test before it is fixed. If it cannot, say why in the issue.
- **Evidence stays attached.** No code path may produce an edge, a value, or a quote that is not
  traceable to a specific location in a source document. A convenience that drops provenance will
  be rejected even when it is faster.
- **Abstention is a feature.** When the evidence does not support an assertion, the correct output
  is an abstention or a recorded conflict. Pull requests that improve coverage by lowering the bar
  for assertion need to show the precision cost, not just the coverage gain.
- **Synthetic fixtures only.** Never commit real unpublished research, patient data, or anything
  under an NDA, including in tests.

## Development

```bash
git clone https://github.com/SciWay-platform/evidence-graph.git
cd evidence-graph

python -m unittest discover -s tests -v      # the suite; standard library only, no install needed
pip install ruff==0.16.7 && ruff check .     # lint
python scripts/check_no_runtime_deps.py      # the zero-dependency guard

pip install pre-commit && pre-commit install # optional, runs the same checks plus a secret scan
```

There is no build step and nothing to install to run the tests. If that ever stops being true,
something has gone wrong.

## Style

Match the surrounding code. Concretely, for this repo that means:

- A module docstring that states what the module is for and why it exists in that form. The
  docstrings here carry design reasoning deliberately, because the *why* is the part that decays.
- Type hints on public functions, `from __future__ import annotations` at the top.
- Comments explain decisions, not mechanics.
- `ruff check .` must pass. There is no enforced formatter: line-length errors are switched off
  because much of this code is regex grammar and vocabulary tables where a hard wrap hurts.

## Licensing

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the same licence as the project. There is no CLA.

Code adapted from another project must keep its attribution and be recorded in [NOTICE](NOTICE),
with the upstream licence reproduced in full.

## Security

Do not report vulnerabilities in a public issue. See [SECURITY.md](SECURITY.md).

## Code of conduct

Participation is covered by the [Contributor Covenant](CODE_OF_CONDUCT.md).
