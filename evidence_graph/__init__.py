"""evidence-graph: knowledge graphs whose edges carry their evidence, or abstain.

The library takes retrieved passages with provenance (`RetrievalHit`) and produces structure that a
scientist can check: typed edges between documents, recorded disagreements, a tamper-evident
derivation record, and a FAIR-packaged export.

It never opens a socket, never reads a credential, and has no runtime dependencies. Anything that
needs a model is injected by the caller.

Layers, roughly in the order you would use them:

    evidence          RetrievalHit — a passage plus the provenance needed to cite it
    biolink           Biolink Model categories, predicates, and CURIE typing
    grounding         resolve a surface string to a biomedical CURIE, or refuse
    ontology          type a concept term against a small built-in vocabulary
    relations         propose typed document→document edges, each carrying its evidence
    conflicts         detect numeric and documented disagreement between sources
    adjudicate        assert / abstain / conflict per candidate edge, over a scorer Protocol
    maintain          re-run without overwriting: revision history per edge
    evaluation        edge-level precision as a function of abstention (risk-coverage)
    graph             assemble nodes and edges into a graph payload
    provenance        W3C PROV-O derivation records with SHA-256 tamper-evidence
    kgx               KGX interchange: Biolink-compatible TSV and JSON Lines
    fair              DataCite report, Dublin Core XML, RO-Crate 1.1 JSON-LD
    ro_crate_validation   structural validation of an RO-Crate against the 1.1 spec

Status: 0.x. The API will change. See CONTRIBUTING.md for what belongs here.
"""

from __future__ import annotations

from evidence_graph.adjudicate import (
    DEFAULT_ASSERT_AT,
    DEFAULT_SCORER,
    Adjudication,
    AdjudicationResult,
    EdgeScore,
    EdgeScorer,
    GroundingScorer,
    Verdict,
    adjudicate,
    edge_key,
    open_questions,
)
from evidence_graph.biolink import (
    BIOLINK_VERSION,
    CATEGORIES,
    DEFAULT_CATEGORY,
    category_chain,
    category_for_curie,
    category_for_prefix,
)
from evidence_graph.conflicts import (
    detect_conflict,
    detect_documented_conflict,
    detect_numeric_conflict,
    detect_numeric_conflict_any,
)
from evidence_graph.evaluation import (
    CurvePoint,
    EvaluationReport,
    baseline_precision,
    risk_coverage,
    risk_coverage_svg,
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
from evidence_graph.grounding import (
    Concept,
    Grounding,
    GroundingReport,
    Vocabulary,
    ground,
    ground_all,
    load_vocabulary,
    normalize_term,
)
from evidence_graph.kgx import (
    KgxGraph,
    from_adjudications,
    to_jsonl,
    to_tsv,
    validate_kgx,
)
from evidence_graph.maintain import (
    EdgeHistory,
    EdgeStatus,
    History,
    Revision,
    changelog,
    contested_edges,
    evidence_fingerprint,
    live_edges,
    reconcile,
)
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
    "Adjudication",
    "AdjudicationResult",
    "BIOLINK_VERSION",
    "CATEGORIES",
    "Concept",
    "CurvePoint",
    "DEFAULT_ASSERT_AT",
    "DEFAULT_CATEGORY",
    "DEFAULT_PRODUCER",
    "DEFAULT_SCORER",
    "EdgeHistory",
    "EdgeScore",
    "EdgeScorer",
    "EdgeStatus",
    "EvaluationReport",
    "Grounding",
    "GroundingReport",
    "GroundingScorer",
    "History",
    "KgxGraph",
    "ONTOLOGY_TYPES",
    "PROV_SCHEMA_VERSION",
    "Producer",
    "ProvenanceEntry",
    "RetrievalHit",
    "Revision",
    "Verdict",
    "Vocabulary",
    "adjudicate",
    "answer_lineage",
    "baseline_precision",
    "build_project_graph",
    "category_chain",
    "category_for_curie",
    "category_for_prefix",
    "changelog",
    "classify_concept",
    "compute_checksum",
    "contested_edges",
    "detect_conflict",
    "detect_documented_conflict",
    "detect_numeric_conflict",
    "detect_numeric_conflict_any",
    "dublin_core_xml",
    "edge_key",
    "evidence_fingerprint",
    "extract_typed_relations",
    "fair_record",
    "fair_report",
    "finalize",
    "from_adjudications",
    "ground",
    "ground_all",
    "lineage_report",
    "live_edges",
    "load_vocabulary",
    "normalize_entity",
    "normalize_term",
    "open_questions",
    "prov_jsonld",
    "prov_jsonld_nodes",
    "reconcile",
    "render_dublin_core",
    "risk_coverage",
    "risk_coverage_svg",
    "ro_crate",
    "to_jsonl",
    "to_tsv",
    "trace_lineage",
    "validate_kgx",
    "validate_ro_crate",
    "verify_checksum",
    "verify_lineage",
]
