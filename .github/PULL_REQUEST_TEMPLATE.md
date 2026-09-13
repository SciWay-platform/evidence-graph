## What this changes

<!-- One or two sentences. What behaviour is different after this merges? -->

## Why

<!-- The problem, not the patch. If it fixes an issue, link it. -->

## Checklist

- [ ] Tests cover the change, and `python -m unittest discover -s tests` passes.
- [ ] `ruff check .` passes.
- [ ] No new runtime dependency (`python scripts/check_no_runtime_deps.py` passes). If the change
      needs a model, a network call, or a credential, it is injected through a Protocol rather than
      imported here — see [CONTRIBUTING.md](../CONTRIBUTING.md#scope).
- [ ] Nothing secret, private, or customer-identifying is in the diff, including in test fixtures.
- [ ] If this changes how an edge is asserted or abstained, the reasoning is stated in the PR body
      and reflected in the docstring.
