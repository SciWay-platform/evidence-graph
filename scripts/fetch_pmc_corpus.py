#!/usr/bin/env python3
"""Build a redistributable corpus from the PubMed Central Open Access subset on AWS Open Data.

This is a BUILD script, not part of the library: it uses the network, and it exists so that the
committed corpora under `corpora/` are reproducible rather than mysterious. The library itself
never fetches anything.

Two public sources, no credentials and no boto3:

  * NCBI E-utilities `esearch` to find article ids for a topic.
  * `s3://pmc-oa-opendata` over plain HTTPS, which is world-readable. Each article lives at
    `PMC<id>.<v>/PMC<id>.<v>.txt` with metadata at `metadata/PMC<id>.<v>.json`.

**Licence filtering is the point of the metadata step.** The OA subset is not uniformly
redistributable: it mixes CC0, CC BY, CC BY-NC, CC BY-NC-ND and others. Only the licences in
`REDISTRIBUTABLE` are kept, so a committed corpus can ship in an Apache-2.0 repository with each
article's licence recorded next to it. Retracted articles and scanned-OCR historical documents are
dropped as well: the first because asserting graph edges from retracted findings is exactly the
failure this library exists to avoid, the second because OCR noise is not a fair test of parsing.

Usage:
    python scripts/fetch_pmc_corpus.py --query "protein aggregation antibody formulation" \\
        --limit 40 --name mab-aggregation
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
USER_AGENT = "evidence-graph corpus builder (https://github.com/SciWay-platform/evidence-graph)"

#: Licences whose terms permit redistribution of the full text in a derived corpus, with attribution.
#: Deliberately conservative: NC and ND variants are excluded because a downstream user of this
#: repository may well be commercial, and ND forbids the derivative that chunking produces.
REDISTRIBUTABLE = {"CC0", "CC BY"}

#: NCBI asks for no more than three requests a second without an API key.
POLITE_DELAY = 0.4

# The header block every PMC plain-text file opens with, before the article body.
_HEADER_END = re.compile(r"^License URL:.*$", re.MULTILINE)
_FIELD = re.compile(r"^(PMCID|PMID|DOI|Publication date|Electronic publication date): (.+)$", re.MULTILINE)
# A section heading: a short title-case line with no sentence punctuation, alone on its line.
_HEADING = re.compile(r"^[A-Z][A-Za-z0-9 ,/&:'()-]{2,70}$")


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def search(query: str, limit: int) -> list[str]:
    """Article ids for a query, newest first. Deterministic for a fixed corpus snapshot date."""
    params = urllib.parse.urlencode({
        "db": "pmc", "term": f"{query} AND open access[filter]",
        "retmax": limit, "retmode": "json", "sort": "relevance",
    })
    payload = json.loads(_get(f"{EUTILS}/esearch.fcgi?{params}"))
    return list(payload["esearchresult"]["idlist"])


def fetch_metadata(pmcid: str) -> dict[str, Any] | None:
    try:
        return json.loads(_get(f"{BUCKET}/metadata/PMC{pmcid}.1.json"))
    except Exception:
        return None


def parse_article(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split a PMC plain-text file into (header fields, [(section, paragraph), ...]).

    The body is line-oriented: each paragraph occupies one long line, and section headings sit alone
    on short title-case lines. That structure is what lets a chunk carry a real locator
    ("Results, paragraph 4") instead of a character offset nobody can check.
    """
    fields = {key: value.strip() for key, value in _FIELD.findall(text)}

    match = _HEADER_END.search(text)
    body = text[match.end():] if match else text

    paragraphs: list[tuple[str, str]] = []
    section = "Body"
    for raw in body.splitlines():
        line = raw.strip()
        if not line or set(line) == {"="}:
            continue
        if len(line) < 72 and _HEADING.match(line) and not line.endswith((".", ";", ",")):
            section = line
            continue
        if len(line) < 40:      # stray fragments: figure labels, page furniture
            continue
        paragraphs.append((section, line))
    return fields, paragraphs


def build(query: str, limit: int, name: str, out_root: Path) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    manifest_records: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for pmcid in search(query, limit):
        time.sleep(POLITE_DELAY)
        meta = fetch_metadata(pmcid)
        if meta is None:
            skip("no metadata in bucket")
            continue
        licence = (meta.get("license_code") or "").strip()
        if licence not in REDISTRIBUTABLE:
            skip(f"licence not redistributable ({licence or 'unknown'})")
            continue
        if meta.get("is_retracted") in (True, "True", "yes"):
            skip("retracted")
            continue
        if meta.get("is_historical_ocr") in (True, "True", "yes"):
            skip("historical OCR")
            continue

        try:
            text = _get(f"{BUCKET}/PMC{pmcid}.1/PMC{pmcid}.1.txt").decode("utf-8", "replace")
        except Exception:
            skip("text unavailable")
            continue

        fields, paragraphs = parse_article(text)
        if len(paragraphs) < 5:
            skip("too little body text")
            continue

        artifact_id = f"PMC{pmcid}"
        source_uri = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{artifact_id}/"
        doi = fields.get("DOI") or meta.get("doi") or ""
        year = (fields.get("Publication date") or fields.get("Electronic publication date") or "")[:4]

        artifacts.append({
            "id": artifact_id,
            "title": (meta.get("title") or "").strip() or artifact_id,
            "artifact_type": "paper",
            "source_system": "pmc-oa",
            "created_at": fields.get("Publication date", ""),
            "structured_metadata": {
                "doi": doi,
                "publisher": "PubMed Central Open Access Subset",
                "publication_year": year,
                "license": licence,
                "pmid": meta.get("pmid"),
                "creators": [],
            },
        })
        for index, (section, paragraph) in enumerate(paragraphs, start=1):
            chunks.append({
                "id": f"{artifact_id}-p{index:04d}",
                "artifact_id": artifact_id,
                "chunk_text": paragraph,
                "source_uri": source_uri,
                "page_or_row_reference": f"{section}, paragraph {index}",
            })
        manifest_records.append({
            "id": artifact_id, "pmid": meta.get("pmid"), "doi": doi,
            "licence": licence, "title": (meta.get("title") or "").strip(),
            "paragraphs": len(paragraphs), "source_uri": source_uri,
        })
        print(f"  + {artifact_id}  {licence:<7} {len(paragraphs):>4} paragraphs  "
              f"{(meta.get('title') or '')[:58]}", file=sys.stderr)

    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "query": query,
        "built": date.today().isoformat(),
        "source": "PubMed Central Open Access Subset via AWS Open Data (s3://pmc-oa-opendata)",
        "redistributable_licences": sorted(REDISTRIBUTABLE),
        "articles": len(artifacts),
        "chunks": len(chunks),
        "skipped": skipped,
        "records": sorted(manifest_records, key=lambda r: r["id"]),
    }
    artifacts.sort(key=lambda a: a["id"])
    chunks.sort(key=lambda c: c["id"])
    (out / "artifacts.json").write_text(json.dumps(artifacts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "chunks.json").write_text(json.dumps(chunks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True, help="PubMed Central search terms")
    ap.add_argument("--name", required=True, help="corpus directory name under --out")
    ap.add_argument("--limit", type=int, default=40, help="how many search hits to consider")
    ap.add_argument("--out", default="corpora", help="root directory for corpora")
    args = ap.parse_args()

    print(f"searching PMC for {args.query!r} ...", file=sys.stderr)
    manifest = build(args.query, args.limit, args.name, Path(args.out))
    print(
        f"\n{manifest['articles']} articles, {manifest['chunks']} chunks -> "
        f"{args.out}/{args.name}/\nskipped: {manifest['skipped'] or 'nothing'}",
        file=sys.stderr,
    )
    return 0 if manifest["articles"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
