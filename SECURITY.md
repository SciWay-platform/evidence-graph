# Security policy

## Reporting a vulnerability

Report privately through a [GitHub security advisory](https://github.com/SciWay-platform/evidence-graph/security/advisories/new),
or by email to **hello@sciway.software** with `evidence-graph security` in the subject. Please do not
open a public issue for a vulnerability.

We aim to acknowledge a report within five working days. This is a small project with no paid
support, so we would rather set that expectation honestly than promise a turnaround we cannot meet.
If you do not hear back, a reminder is welcome.

## What the realistic attack surface is

The library opens no sockets, holds no credentials, and executes nothing it reads. That rules out
most of the usual categories, and leaves three that are genuinely live:

1. **Regular-expression denial of service.** Edge proposal and conflict detection are built on
   regex grammar applied to untrusted document text. A pattern with catastrophic backtracking is a
   real vulnerability here, because the input is by definition attacker-influenced in any pipeline
   that ingests third-party documents. Reports of pathological inputs are in scope and welcome.
2. **Provenance integrity.** The checksum over a lineage entry exists so that a later edit to a
   quote, a source, or a derivation link is detectable. A way to alter recorded evidence while
   keeping `verify_lineage` satisfied defeats the purpose of the library and is a security issue,
   not merely a bug.
3. **Resource exhaustion on hostile input.** Quadratic behaviour reachable from a crafted corpus
   (very many entities in one chunk, for instance) is in scope.

Out of scope: anything that requires the caller to pass in code or a malicious Protocol
implementation. The library trusts its caller by design; it does not trust its *input*.

## Supported versions

Pre-1.0, only the latest release is supported. Fixes land on `main` and ship in the next release.

## A note on the corpus you scan

If you are reporting a bug, please reproduce it with synthetic text. Never attach unpublished
research, patient data, or anything under an NDA to a public issue or a pull request.
