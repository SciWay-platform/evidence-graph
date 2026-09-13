"""The unit of evidence: a passage of source text with enough provenance to cite it.

Every other module in this library consumes or produces `RetrievalHit`s. It is deliberately a plain
frozen dataclass rather than an interface, because the whole point is that a piece of evidence is
inert data you can serialise, checksum, and hand to a reviewer.

The invariant that matters: a hit always knows **where it came from**. `source_uri` locates the
document, `location` locates the passage inside it (page, row, or line), `quote` is the verbatim
text, and `chunk_id` is stable enough to join back to whatever store produced it. A hit missing
those is not evidence, it is a string, and the assertion layers here will refuse to build on it.

Callers produce hits however they like — lexical search, embeddings, a graph store, a hand-written
fixture. This library never retrieves anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalHit:
    """One retrieved passage, with the provenance needed to cite it.

    artifact_id/chunk_id: stable ids for the source document and the passage within it.
    source_system: where the document came from (an upload, a connector, a repository).
    title: human-readable document title, shown in a citation.
    score: the retriever's own relevance score. Scales differ between retrievers, so this library
        never compares scores across sources; it only ever orders within one result set.
    quote: the verbatim passage. Never paraphrase into this field — a citation the reader cannot
        find in the source is not a citation.
    source_uri: locator for the document (URL, DOI, file path).
    artifact_type: optional kind of document (paper, protocol, result table, note).
    location: optional position within the document (page/row/line), for a precise citation.
    bbox: optional normalised page rectangles, for highlighting a region of a rendered PDF.
    """

    artifact_id: str
    chunk_id: str
    source_system: str
    title: str
    score: float
    quote: str
    source_uri: str
    artifact_type: str = ""
    location: str = ""
    bbox: list = field(default_factory=list)
