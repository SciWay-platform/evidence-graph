#!/usr/bin/env python3
"""Distil public biomedical ontologies into the compact dictionaries this library vendors.

A BUILD script, not part of the library. It fetches ~130 MB of ontology releases and writes about
6.5 MB of gzipped TSV into `evidence_graph/data/`, which is what gets committed. The library reads
those files with `gzip` from the standard library and never touches the network.

Output shape, one uniform format for every source:

    CURIE <TAB> primary label <TAB> synonym|synonym|... <TAB> qualifier

The qualifier is empty for most sources. For GO it carries the term's namespace, because the `GO:`
prefix alone is declared by five Biolink classes and cannot type a term: `biological_process`,
`molecular_function` and `cellular_component` are three different categories sharing one prefix.

plus `redirects.tsv.gz` mapping obsolete and alternate CURIEs to their replacements, so an
identifier that appears in older text still resolves.

**Only EXACT synonyms are kept.** This is a deliberate precision choice. GO lists "apoptosis" as a
NARROW synonym of `GO:0006915 apoptotic process`; grounding the bare word through a NARROW synonym
produces a citation that is subtly wrong about what was measured, which is worse than not grounding
it at all. Callers who want recall over precision should rebuild with `--synonym-scopes all`.

Licences, all permitting redistribution of a derived subset (see `evidence_graph/data/NOTICE`):
HGNC is CC0; MONDO, GO, CL, ChEBI and UniProt are CC BY 4.0; UBERON is CC BY 3.0.

Usage:
    python scripts/build_vocabulary.py                 # everything
    python scripts/build_vocabulary.py --only mondo go # a subset, for iterating
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "evidence_graph" / "data"
USER_AGENT = "evidence-graph vocabulary builder (https://github.com/SciWay-platform/evidence-graph)"

SOURCES: dict[str, dict[str, Any]] = {
    "mondo": {
        "url": "https://purl.obolibrary.org/obo/mondo/mondo-simple.obo",
        "format": "obo", "prefix": "MONDO", "licence": "CC BY 4.0",
        "attribution": "Mondo Disease Ontology (https://mondo.monarchinitiative.org/)",
    },
    "go": {
        "url": "https://current.geneontology.org/ontology/go-basic.obo",
        "format": "obo", "prefix": "GO", "licence": "CC BY 4.0",
        "attribution": "Gene Ontology (https://geneontology.org/)",
    },
    "uberon": {
        "url": "https://purl.obolibrary.org/obo/uberon/basic.obo",
        "format": "obo", "prefix": "UBERON", "licence": "CC BY 3.0",
        "attribution": "Uberon multi-species anatomy ontology (https://obophenotype.github.io/uberon/)",
    },
    "cl": {
        "url": "https://purl.obolibrary.org/obo/cl/cl-basic.obo",
        "format": "obo", "prefix": "CL", "licence": "CC BY 4.0",
        "attribution": "Cell Ontology (https://obophenotype.github.io/cell-ontology/)",
    },
    "chebi": {
        "url": "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/names.tsv.gz",
        "compounds_url": "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/compounds.tsv.gz",
        "format": "chebi", "prefix": "CHEBI", "licence": "CC BY 4.0",
        "attribution": "ChEBI (https://www.ebi.ac.uk/chebi/)",
    },
    "hgnc": {
        "url": "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt",
        "format": "hgnc", "prefix": "HGNC", "licence": "CC0 1.0",
        "attribution": "HUGO Gene Nomenclature Committee (https://www.genenames.org/)",
    },
    "uniprot": {
        "url": ("https://rest.uniprot.org/uniprotkb/stream?query=%28reviewed%3Atrue%29+AND+"
                "%28organism_id%3A9606%29&fields=accession%2Cid%2Cprotein_name%2Cgene_names"
                "&format=tsv&compressed=true"),
        "format": "uniprot", "prefix": "UniProtKB", "licence": "CC BY 4.0",
        "attribution": "UniProt (https://www.uniprot.org/)",
    },
}

# --- .obo 1.2 parsing --------------------------------------------------------------------------
# The grammar that actually bites: a synonym line carries an optional synonym-TYPE CURIE between the
# scope and the xref bracket, and synonym strings may contain escaped quotes.
_SYNONYM = re.compile(r'^synonym:\s+"((?:[^"\\]|\\.)*)"\s+(EXACT|BROAD|NARROW|RELATED)(?:\s+\S+)?\s*\[')
_TAG = re.compile(r"^([a-z_]+):\s*(.*)$")


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    return gzip.decompress(data) if url.endswith(".gz") or data[:2] == b"\x1f\x8b" else data


def _obo_stanzas(text: str) -> Iterator[dict[str, list[str]]]:
    """Yield [Term] stanzas as tag -> list of values. [Typedef]/[Instance] stanzas are skipped."""
    stanza: dict[str, list[str]] | None = None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("["):
            if stanza is not None:
                yield stanza
            stanza = defaultdict(list) if line == "[Term]" else None
            continue
        if stanza is None or not line:
            continue
        match = _TAG.match(line)
        if match:
            stanza[match.group(1)].append(match.group(2))
    if stanza is not None:
        yield stanza


def parse_obo(text: str, prefix: str, scopes: set[str]) -> tuple[dict[str, tuple[str, list[str], str]],
                                                                 dict[str, str]]:
    """(terms, redirects). Obsolete terms leave the forward index but stay as redirects."""
    terms: dict[str, tuple[str, list[str], str]] = {}
    redirects: dict[str, str] = {}
    for stanza in _obo_stanzas(text):
        ids = stanza.get("id") or []
        if not ids or not ids[0].startswith(f"{prefix}:"):
            continue
        curie = ids[0]
        if "true" in stanza.get("is_obsolete", []):
            for target in stanza.get("replaced_by", []) or stanza.get("consider", []):
                redirects[curie] = target
                break
            continue
        name = (stanza.get("name") or [""])[0].strip()
        if not name:
            continue
        synonyms: list[str] = []
        for raw in stanza.get("synonym", []):
            match = _SYNONYM.match(f"synonym: {raw}")
            if match and match.group(2) in scopes:
                synonyms.append(match.group(1).replace('\\"', '"'))
        for alt in stanza.get("alt_id", []):
            redirects[alt] = curie
        # `namespace` is what lets a GO term be typed; harmless and empty elsewhere.
        terms[curie] = (name, synonyms, (stanza.get("namespace") or [""])[0].strip())
    return terms, redirects


# --- non-OBO sources ---------------------------------------------------------------------------


def parse_chebi(names_tsv: bytes, compounds_tsv: bytes, min_stars: int = 2):
    """ChEBI flat files.

    Column names are lowercase in the live files (`chebi_accession`, `stars`), and `names.tsv` keys
    on the NUMERIC compound id rather than the accession, so the two files have to be joined through
    it. `ascii_name` is preferred over `name` because the latter carries HTML markup for
    superscripts and stereo descriptors.

    `min_stars` defaults to 2, not 3. Three-star means manually curated, and the obvious move is to
    keep only those — but `trastuzumab` is two-star, as are most biologic drug names, and dropping
    the compounds a formulation paper is actually about to gain precision against systematic names
    is the wrong trade. The precision argument for three-star applies to FUZZY matching; this
    library matches exactly after normalisation, where a long systematic name essentially never
    collides with prose. One-star entries (2,904 of them) are preliminary and are dropped.
    """
    primary: dict[str, str] = {}
    accession_by_id: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(compounds_tsv.decode("utf-8", "replace")), delimiter="\t")
    for row in reader:
        try:
            stars = int(float((row.get("stars") or "0").strip()))
        except ValueError:
            stars = 0
        if stars < min_stars:
            continue
        accession = (row.get("chebi_accession") or "").strip()
        if not accession.startswith("CHEBI:"):
            continue
        accession_by_id[(row.get("id") or "").strip()] = accession
        label = (row.get("ascii_name") or row.get("name") or "").strip()
        if label and label.lower() != "null":
            primary[accession] = label

    synonyms: dict[str, list[str]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(names_tsv.decode("utf-8", "replace")), delimiter="\t")
    for row in reader:
        accession = accession_by_id.get((row.get("compound_id") or "").strip())
        if not accession:
            continue
        if (row.get("language_code") or "en").strip() not in {"en", ""}:
            continue
        name = re.sub(r"<[^>]+>", "", (row.get("ascii_name") or row.get("name") or "")).strip()
        if not name:
            continue
        if accession not in primary:
            primary[accession] = name
        elif name.lower() != primary[accession].lower() and name not in synonyms[accession]:
            synonyms[accession].append(name)
    return {c: (primary[c], synonyms.get(c, [])) for c in sorted(primary)}, {}


def parse_hgnc(tsv: bytes):
    terms: dict[str, tuple[str, list[str]]] = {}
    redirects: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(tsv.decode("utf-8", "replace")), delimiter="\t")
    for row in reader:
        if (row.get("status") or "").strip() != "Approved":
            continue
        curie = (row.get("hgnc_id") or "").strip()
        symbol = (row.get("symbol") or "").strip()
        if not curie or not symbol:
            continue
        aliases = [symbol]
        for field in ("name", "alias_symbol", "alias_name", "prev_symbol", "prev_name"):
            value = (row.get(field) or "").strip().strip('"')
            aliases += [v.strip() for v in value.split("|") if v.strip()]
        seen: list[str] = []
        for alias in aliases[1:]:
            if alias.lower() != symbol.lower() and alias not in seen:
                seen.append(alias)
        terms[curie] = (symbol, seen)
    return terms, redirects


def parse_uniprot(tsv: bytes):
    terms: dict[str, tuple[str, list[str]]] = {}
    reader = csv.DictReader(io.StringIO(tsv.decode("utf-8", "replace")), delimiter="\t")
    for row in reader:
        accession = (row.get("Entry") or "").strip()
        if not accession:
            continue
        full = (row.get("Protein names") or "").strip()
        # "Name (Alt one) (Alt two)" -> primary plus alternates.
        primary = re.sub(r"\s*\(.*$", "", full).strip() or accession
        alternates = [a.strip() for a in re.findall(r"\(([^()]+)\)", full) if len(a.strip()) > 2]
        genes = [g for g in (row.get("Gene Names") or "").split() if g]
        seen: list[str] = []
        for alias in alternates + genes:
            if alias.lower() != primary.lower() and alias not in seen:
                seen.append(alias)
        terms[f"UniProtKB:{accession}"] = (primary, seen)
    return terms, {}


# --- writing -----------------------------------------------------------------------------------


def write_dictionary(path: Path, terms: dict[str, tuple]) -> int:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as out:
        for curie in sorted(terms):
            entry = terms[curie]
            label, synonyms = entry[0], entry[1]
            qualifier = entry[2] if len(entry) > 2 else ""
            clean = [s.replace("\t", " ").replace("|", " ") for s in synonyms]
            out.write(f"{curie}\t{label}\t{'|'.join(clean)}\t{qualifier}\n")
    return len(terms)


def build(names: list[str], scopes: set[str], chebi_stars: int = 2) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    redirects: dict[str, str] = {}
    report: dict[str, Any] = {"built": date.today().isoformat(), "synonym_scopes": sorted(scopes),
                              "chebi_min_stars": chebi_stars, "sources": {}}

    for name in names:
        source = SOURCES[name]
        print(f"  {name}: fetching {source['url'][:76]}…", file=sys.stderr)
        payload = _fetch(source["url"])
        if source["format"] == "obo":
            terms, source_redirects = parse_obo(payload.decode("utf-8", "replace"),
                                                source["prefix"], scopes)
        elif source["format"] == "chebi":
            compounds = _fetch(source["compounds_url"])
            terms, source_redirects = parse_chebi(payload, compounds, min_stars=chebi_stars)
        elif source["format"] == "hgnc":
            terms, source_redirects = parse_hgnc(payload)
        else:
            terms, source_redirects = parse_uniprot(payload)
        redirects.update(source_redirects)

        path = DATA_DIR / f"{name}.tsv.gz"
        count = write_dictionary(path, terms)
        size = path.stat().st_size
        report["sources"][name] = {
            "terms": count, "bytes": size, "licence": source["licence"],
            "attribution": source["attribution"], "url": source["url"],
        }
        print(f"    -> {count:,} terms, {size / 1e6:.2f} MB", file=sys.stderr)

    if redirects:
        path = DATA_DIR / "redirects.tsv.gz"
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as out:
            for old in sorted(redirects):
                out.write(f"{old}\t{redirects[old]}\n")
        report["redirects"] = {"entries": len(redirects), "bytes": path.stat().st_size}
        print(f"  redirects: {len(redirects):,} entries, {path.stat().st_size / 1e6:.2f} MB",
              file=sys.stderr)

    (DATA_DIR / "MANIFEST.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", choices=sorted(SOURCES), help="build only these sources")
    ap.add_argument("--synonym-scopes", default="exact", choices=["exact", "all"],
                    help="EXACT only (default, precise) or every scope (higher recall, more noise)")
    ap.add_argument("--chebi-stars", type=int, default=2, choices=[1, 2, 3],
                    help="minimum ChEBI curation level (default 2: keeps biologics like trastuzumab)")
    args = ap.parse_args()

    scopes = {"EXACT"} if args.synonym_scopes == "exact" else {"EXACT", "BROAD", "NARROW", "RELATED"}
    report = build(args.only or sorted(SOURCES), scopes, chebi_stars=args.chebi_stars)
    total = sum(s["bytes"] for s in report["sources"].values()) + report.get("redirects", {}).get("bytes", 0)
    terms = sum(s["terms"] for s in report["sources"].values())
    print(f"\n{terms:,} terms in {total / 1e6:.2f} MB -> {DATA_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
