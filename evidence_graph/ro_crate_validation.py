"""Offline structural validator for RO-Crate 1.1 metadata (standard library only).

Checks the structural rules from the RO-Crate 1.1 specification, so a crate can be validated
without the online playground or a network library:

  - a JSON-LD `@context` referencing the RO-Crate 1.1 context;
  - a **metadata descriptor** entity (`ro-crate-metadata.json`) of type `CreativeWork` whose
    `conformsTo` points at the spec and whose `about` points at the root data entity;
  - a **root data entity** (`./`) of type `Dataset` with a `name`;
  - every `@id` unique, every reference (`about`, `hasPart`, `author`, ...) resolvable within the
    graph (no dangling links);
  - each data entity carries provenance (`sciway:sourceSystem`).

`validate_ro_crate(crate) -> list[str]` returns human-readable issues; an empty list means valid.
It pairs with `evidence_graph.fair.ro_crate`, which writes crates this validator accepts.

Known wart, pre-1.0: the provenance check and the crates written by `fair.ro_crate` both use the
`sciway:` JSON-LD namespace, inherited from this code's origin. Renaming it would invalidate every
crate already written, so it is a 1.0 decision rather than a quiet one.

Reference: https://www.researchobject.org/ro-crate/specification/1.1/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
RO_CRATE_SPEC = "https://w3id.org/ro/crate/1.1"
_REFERENCE_KEYS = ("about", "hasPart", "author", "publisher", "license", "conformsTo")


def _types(entity: dict) -> set[str]:
    t = entity.get("@type", [])
    return set(t) if isinstance(t, list) else {t}


def _refs(value) -> list[str]:
    """Collect @id references from a value that may be a dict, a list of dicts, or scalar."""
    out: list[str] = []
    if isinstance(value, dict) and "@id" in value:
        out.append(value["@id"])
    elif isinstance(value, list):
        for v in value:
            out.extend(_refs(v))
    return out


def validate_ro_crate(crate: dict) -> list[str]:
    issues: list[str] = []

    # @context references the RO-Crate context (string or list containing it)
    ctx = crate.get("@context")
    ctx_strings = [c for c in (ctx if isinstance(ctx, list) else [ctx]) if isinstance(c, str)]
    if RO_CRATE_CONTEXT not in ctx_strings:
        issues.append(f"@context does not reference {RO_CRATE_CONTEXT}")

    graph = crate.get("@graph")
    if not isinstance(graph, list) or not graph:
        return issues + ["@graph missing or empty"]

    # unique @ids
    ids: list[str] = []
    for e in graph:
        if "@id" not in e:
            issues.append(f"entity without @id: {json.dumps(e)[:80]}")
        else:
            ids.append(e["@id"])
    id_set = set(ids)
    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        issues.append(f"duplicate @id(s): {', '.join(dupes)}")

    by_id = {e["@id"]: e for e in graph if "@id" in e}

    # metadata descriptor
    descriptor = by_id.get("ro-crate-metadata.json")
    if descriptor is None:
        issues.append("missing metadata descriptor entity 'ro-crate-metadata.json'")
        root_id = "./"
    else:
        if "CreativeWork" not in _types(descriptor):
            issues.append("metadata descriptor is not a CreativeWork")
        if _refs(descriptor.get("conformsTo")) != [RO_CRATE_SPEC]:
            issues.append(f"metadata descriptor conformsTo must be {RO_CRATE_SPEC}")
        about = _refs(descriptor.get("about"))
        if not about:
            issues.append("metadata descriptor has no 'about' pointing at the root data entity")
        root_id = about[0] if about else "./"

    # root data entity
    root = by_id.get(root_id)
    if root is None:
        issues.append(f"root data entity '{root_id}' not found in @graph")
    else:
        if "Dataset" not in _types(root):
            issues.append("root data entity is not a Dataset")
        if not root.get("name"):
            issues.append("root data entity has no name")

    # all references resolve
    for e in graph:
        for key in _REFERENCE_KEYS:
            if key not in e:
                continue
            for ref in _refs(e[key]):
                # external http(s) refs (spec/license/PIDs) are allowed to live outside the graph
                if ref.startswith("http://") or ref.startswith("https://"):
                    continue
                if ref not in id_set:
                    issues.append(f"entity '{e.get('@id')}' references missing @id '{ref}' (via {key})")

    # Provenance on data entities (those the root hasPart). See the namespace note in the docstring.
    if root is not None:
        for ref in _refs(root.get("hasPart")):
            part = by_id.get(ref)
            if part is not None and not part.get("sciway:sourceSystem"):
                issues.append(f"data entity '{ref}' missing sciway:sourceSystem provenance")

    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate an RO-Crate 1.1 metadata file.")
    ap.add_argument("file", help="path to ro-crate-metadata.json")
    args = ap.parse_args()

    crate = json.loads(Path(args.file).read_text(encoding="utf-8"))
    issues = validate_ro_crate(crate)
    if issues:
        print(f"RO-Crate INVALID ({args.file}) \u2014 {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    root = next((e for e in crate["@graph"] if e.get("@id", "").rstrip("/") in ("", ".")), {})
    n_parts = len(_refs(root.get("hasPart")))
    print(f"RO-Crate VALID ({args.file}) \u2014 structural checks passed; {n_parts} data entities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
