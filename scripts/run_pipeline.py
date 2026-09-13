#!/usr/bin/env python3
"""Run the evidence-graph spine over a corpus and write the results.

    python scripts/run_pipeline.py corpora/mab-aggregation --out build/mab-aggregation

Writes, into `--out`:

    kgx_nodes.tsv / kgx_edges.tsv     the graph, KGX TSV
    kgx_nodes.jsonl / kgx_edges.jsonl the same, with passages preserved exactly
    open_questions.json               what the adjudicator would not assert, with its evidence
    history.json                      per-edge revision history, for the next run
    summary.json                      counts for every stage

`open_questions.json` is not a log. It is half the output: the edges a curator needs to settle, each
carrying the passages the system read and the reason it was not convinced.

Pass `--history` from a previous run to reconcile instead of rebuilding, so an edge that loses its
support is recorded as weakened rather than quietly disappearing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence_graph.kgx import to_jsonl, to_tsv, validate_kgx  # noqa: E402
from evidence_graph.maintain import EdgeHistory, Revision  # noqa: E402
from evidence_graph.pipeline import build  # noqa: E402


def _load_history(path: Path):
    """Rebuild a History from the JSON a previous run wrote."""
    if not path.exists():
        return None
    history = {}
    for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
        revisions = tuple(
            Revision(
                revision=r["revision"], at=r["at"], status=r["status"],
                verdict=r["verdict"], score=r["score"], reason=r["reason"],
                evidence_fingerprint=r["evidence_fingerprint"],
                conflicts_with=tuple(r.get("conflicts_with") or ()),
            )
            for r in entry["revisions"]
        )
        history[key] = EdgeHistory(key=key, edge=entry["edge"], revisions=revisions)
    return history


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", help="directory holding artifacts.json and chunks.json")
    ap.add_argument("--out", default="build/pipeline", help="where to write the results")
    ap.add_argument("--history", help="history.json from a previous run, to reconcile against")
    ap.add_argument("--assert-at", type=float, default=None,
                    help="score floor for assertion; higher asserts less at higher precision")
    ap.add_argument("--no-grounding", action="store_true",
                    help="skip concept grounding (faster; emits documents and relations only)")
    ap.add_argument("--limit", type=int, help="use only the first N chunks (for a quick look)")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    artifacts = json.loads((corpus / "artifacts.json").read_text(encoding="utf-8"))
    chunks = json.loads((corpus / "chunks.json").read_text(encoding="utf-8"))
    if args.limit:
        chunks = chunks[:args.limit]

    print(f"{len(artifacts)} artifacts, {len(chunks)} chunks", file=sys.stderr)
    kwargs = {} if args.assert_at is None else {"assert_at": args.assert_at}
    result = build(
        artifacts, chunks,
        at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        history=_load_history(Path(args.history)) if args.history else None,
        ground_concepts=not args.no_grounding,
        **kwargs,
    )

    issues = validate_kgx(result.graph)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    nodes_tsv, edges_tsv = to_tsv(result.graph)
    nodes_jsonl, edges_jsonl = to_jsonl(result.graph)
    for name, text in [
        ("kgx_nodes.tsv", nodes_tsv), ("kgx_edges.tsv", edges_tsv),
        ("kgx_nodes.jsonl", nodes_jsonl), ("kgx_edges.jsonl", edges_jsonl),
        ("open_questions.json", json.dumps(result.open_questions, indent=2, ensure_ascii=False)),
        ("history.json", json.dumps({k: v.as_dict() for k, v in result.history.items()},
                                    indent=2, ensure_ascii=False)),
        ("summary.json", json.dumps({**result.summary(), "kgx_valid": not issues,
                                     "kgx_issues": issues[:20]}, indent=2)),
    ]:
        (out / name).write_text(text, encoding="utf-8")

    summary = result.summary()
    print(json.dumps(summary, indent=2), file=sys.stderr)
    print(f"KGX valid: {not issues}" + (f" ({len(issues)} issues)" if issues else ""), file=sys.stderr)
    print(f"-> {out}", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
