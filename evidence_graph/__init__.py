"""evidence-graph: knowledge graphs whose edges carry their evidence, or abstain.

The library takes retrieved passages with provenance (`RetrievalHit`) and produces structure that a
scientist can check: typed edges between documents, recorded disagreements, a tamper-evident
derivation record, and a FAIR-packaged export.

It never opens a socket, never reads a credential, and has no runtime dependencies. Anything that
needs a model is injected by the caller.

Layers, roughly in the order you would use them:

    evidence          RetrievalHit — a passage plus the provenance needed to cite it
    ontology          type a concept term against a controlled vocabulary
    relations         propose typed document→document edges, each carrying its evidence
    conflicts         detect numeric and documented disagreement between sources
    graph             assemble nodes and edges into a graph payload
    provenance        W3C PROV-O derivation records with SHA-256 tamper-evidence
    fair              DataCite report, Dublin Core XML, RO-Crate 1.1 JSON-LD
    ro_crate_validation   structural validation of an RO-Crate against the 1.1 spec

Status: 0.x. The API will change. See CONTRIBUTING.md for what belongs here.
"""

from __future__ import annotations

from evidence_graph.conflicts import (
    detect_conflict,
    detect_documented_conflict,
    detect_numeric_conflict,
    detect_numeric_conflict_any,
)
from evidence_graph.evidence import RetrievalHit
from evidence_graph.fair import (
    DEFAULT_PRODUCER,
    Producer,
    dublin_core_xml,
    fair_record,
    fair_report,
    render_dublin_core,
    ro_crate,
)
from evidence_graph.graph import build_project_graph
from evidence_graph.ontology import ONTOLOGY_TYPES, classify_concept
from evidence_graph.provenance import (
    PROV_SCHEMA_VERSION,
    ProvenanceEntry,
    answer_lineage,
    compute_checksum,
    finalize,
    lineage_report,
    prov_jsonld,
    prov_jsonld_nodes,
    trace_lineage,
    verify_checksum,
    verify_lineage,
)
from evidence_graph.relations import extract_typed_relations, normalize_entity
from evidence_graph.ro_crate_validation import validate_ro_crate

__version__ = "0.1.0.dev0"

__all__ = [
    "DEFAULT_PRODUCER",
    "ONTOLOGY_TYPES",
    "PROV_SCHEMA_VERSION",
    "Producer",
    "ProvenanceEntry",
    "RetrievalHit",
    "answer_lineage",
    "build_project_graph",
    "classify_concept",
    "compute_checksum",
    "detect_conflict",
    "detect_documented_conflict",
    "detect_numeric_conflict",
    "detect_numeric_conflict_any",
    "dublin_core_xml",
    "extract_typed_relations",
    "fair_record",
    "fair_report",
    "finalize",
    "lineage_report",
    "normalize_entity",
    "prov_jsonld",
    "prov_jsonld_nodes",
    "render_dublin_core",
    "ro_crate",
    "trace_lineage",
    "validate_ro_crate",
    "verify_checksum",
    "verify_lineage",
]
