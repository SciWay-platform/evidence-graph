"""Biolink Model constants — categories, predicates, and the prefix map used to type a CURIE.

Verified against **Biolink Model 4.4.4** (CC0), the release current in September 2026. Only the
parts this library needs are transcribed; the full model is 544 KB of YAML and pulling it in would
break the zero-dependency rule for no benefit.

Two things here repay reading before use:

**A prefix does not determine a category.** Roughly fifty ID prefixes are declared by more than one
Biolink class, so inverting `id_prefixes` is not a function. `UMLS` is declared by fifteen classes,
`MESH` by thirteen, and `GO` by five — a GO term can be a biological process, a molecular activity,
a cellular component, an anatomical entity or a pathway, and the prefix alone cannot tell you which.
`AMBIGUOUS_PREFIXES` lists the ones this module refuses to guess at; `category_for_prefix` returns
None for them rather than inventing a type.

**Biolink has no assertion-level predicates.** There is no `supports`, `contradicts`, `decided` or
`uses_protocol` anywhere in the model, verified by enumerating all 247 descendants of `related to`.
This is by design: in Biolink an edge *is* the statement, and an Association cannot be the subject
of another Association. `evidence_graph.kgx` handles the consequence.
"""

from __future__ import annotations

#: The Biolink Model release these constants were transcribed and verified from.
BIOLINK_VERSION = "4.4.4"

#: Node categories this library can produce. A useful subset of the 157 descendants of `named thing`.
CATEGORIES = (
    "biolink:AnatomicalEntity", "biolink:BiologicalProcess", "biolink:Cell", "biolink:CellLine",
    "biolink:CellularComponent", "biolink:ChemicalEntity", "biolink:ChemicalMixture",
    "biolink:ClinicalTrial", "biolink:Disease", "biolink:Drug", "biolink:Gene", "biolink:GeneFamily",
    "biolink:Genome", "biolink:GrossAnatomicalStructure", "biolink:MaterialSample",
    "biolink:MolecularActivity", "biolink:MolecularMixture", "biolink:NamedThing",
    "biolink:OrganismTaxon", "biolink:Pathway", "biolink:PhenotypicFeature", "biolink:Procedure",
    "biolink:Protein", "biolink:Publication", "biolink:SequenceVariant", "biolink:SmallMolecule",
    "biolink:Transcript", "biolink:Treatment",
)

#: The category to fall back to when nothing more specific can be justified.
DEFAULT_CATEGORY = "biolink:NamedThing"

#: Full ancestor chains, for emitting the pipe-delimited category chain KGX files often carry.
CATEGORY_ANCESTORS: dict[str, tuple[str, ...]] = {
    "biolink:Gene": ("biolink:BiologicalEntity", "biolink:NamedThing", "biolink:Entity"),
    "biolink:Protein": ("biolink:Polypeptide", "biolink:BiologicalEntity", "biolink:NamedThing",
                        "biolink:Entity"),
    "biolink:Disease": ("biolink:DiseaseOrPhenotypicFeature", "biolink:BiologicalEntity",
                        "biolink:NamedThing", "biolink:Entity"),
    "biolink:PhenotypicFeature": ("biolink:DiseaseOrPhenotypicFeature", "biolink:BiologicalEntity",
                                  "biolink:NamedThing", "biolink:Entity"),
    "biolink:SmallMolecule": ("biolink:MolecularEntity", "biolink:ChemicalEntity",
                              "biolink:NamedThing", "biolink:Entity"),
    "biolink:ChemicalEntity": ("biolink:NamedThing", "biolink:Entity"),
    "biolink:Drug": ("biolink:MolecularMixture", "biolink:ChemicalMixture", "biolink:ChemicalEntity",
                     "biolink:NamedThing", "biolink:Entity"),
    "biolink:Cell": ("biolink:AnatomicalEntity", "biolink:OrganismalEntity",
                     "biolink:BiologicalEntity", "biolink:NamedThing", "biolink:Entity"),
    "biolink:CellularComponent": ("biolink:AnatomicalEntity", "biolink:OrganismalEntity",
                                  "biolink:BiologicalEntity", "biolink:NamedThing", "biolink:Entity"),
    "biolink:AnatomicalEntity": ("biolink:OrganismalEntity", "biolink:BiologicalEntity",
                                 "biolink:NamedThing", "biolink:Entity"),
    "biolink:BiologicalProcess": ("biolink:BiologicalProcessOrActivity", "biolink:BiologicalEntity",
                                  "biolink:NamedThing", "biolink:Entity"),
    "biolink:MolecularActivity": ("biolink:BiologicalProcessOrActivity", "biolink:BiologicalEntity",
                                  "biolink:NamedThing", "biolink:Entity"),
    "biolink:Pathway": ("biolink:BiologicalProcess", "biolink:BiologicalProcessOrActivity",
                        "biolink:BiologicalEntity", "biolink:NamedThing", "biolink:Entity"),
    "biolink:Publication": ("biolink:InformationContentEntity", "biolink:NamedThing",
                            "biolink:Entity"),
    "biolink:OrganismTaxon": ("biolink:NamedThing", "biolink:Entity"),
}

#: Prefixes declared by so many classes that typing from them would be a guess. Never auto-typed.
AMBIGUOUS_PREFIXES = frozenset({"UMLS", "MESH", "NCIT", "SNOMEDCT", "KEGG", "PO"})

#: ID prefix -> Biolink category, for prefixes that are unambiguous enough to type from.
PREFIX_TO_CATEGORY: dict[str, str] = {
    # disease / phenotype
    "MONDO": "biolink:Disease", "DOID": "biolink:Disease", "ORDO": "biolink:Disease",
    "OMIM": "biolink:Disease", "EFO": "biolink:Disease", "MEDDRA": "biolink:Disease",
    "ICD10": "biolink:Disease", "ICD9": "biolink:Disease",
    "HP": "biolink:PhenotypicFeature", "MP": "biolink:PhenotypicFeature",
    # chemical
    "CHEBI": "biolink:SmallMolecule", "PUBCHEM.COMPOUND": "biolink:SmallMolecule",
    "CHEMBL.COMPOUND": "biolink:SmallMolecule", "DRUGBANK": "biolink:SmallMolecule",
    "UNII": "biolink:SmallMolecule", "CAS": "biolink:SmallMolecule", "HMDB": "biolink:SmallMolecule",
    "INCHIKEY": "biolink:SmallMolecule", "RXCUI": "biolink:Drug", "NDC": "biolink:Drug",
    # gene / protein / variant
    "NCBIGene": "biolink:Gene", "HGNC": "biolink:Gene", "ENSEMBL": "biolink:Gene",
    "MGI": "biolink:Gene", "RGD": "biolink:Gene", "ZFIN": "biolink:Gene", "SGD": "biolink:Gene",
    "UniProtKB": "biolink:Protein", "PR": "biolink:Protein",
    "PANTHER.FAMILY": "biolink:GeneFamily", "PFAM": "biolink:GeneFamily",
    "CLINVAR": "biolink:SequenceVariant", "DBSNP": "biolink:SequenceVariant",
    # anatomy / cell
    "CL": "biolink:Cell", "CLO": "biolink:CellLine", "UBERON": "biolink:AnatomicalEntity",
    "FMA": "biolink:AnatomicalEntity",
    # pathway / activity
    "REACT": "biolink:Pathway", "RHEA": "biolink:MolecularActivity", "EC": "biolink:MolecularActivity",
    # misc
    "NCBITaxon": "biolink:OrganismTaxon",
    "PMID": "biolink:Publication", "PMC": "biolink:Publication", "doi": "biolink:Publication",
}

#: GO is declared by five classes, so its namespace decides the category, not its prefix.
GO_NAMESPACE_TO_CATEGORY = {
    "biological_process": "biolink:BiologicalProcess",
    "molecular_function": "biolink:MolecularActivity",
    "cellular_component": "biolink:CellularComponent",
}


def category_for_prefix(prefix: str, *, go_namespace: str | None = None) -> str | None:
    """The Biolink category for an ID prefix, or None when the prefix does not determine one.

    Returning None is the useful behaviour: an ambiguous prefix typed by guesswork produces a graph
    that looks well-formed and is quietly wrong about what its nodes are.
    """
    if prefix == "GO":
        return GO_NAMESPACE_TO_CATEGORY.get(go_namespace or "")
    if prefix in AMBIGUOUS_PREFIXES:
        return None
    return PREFIX_TO_CATEGORY.get(prefix)


def category_for_curie(curie: str, *, go_namespace: str | None = None) -> str | None:
    """The Biolink category for a CURIE such as `MONDO:0007254`."""
    prefix, _, rest = (curie or "").partition(":")
    return category_for_prefix(prefix, go_namespace=go_namespace) if rest else None


def category_chain(category: str) -> tuple[str, ...]:
    """`category` plus its ancestors, for the pipe-delimited chain KGX files often carry."""
    return (category, *CATEGORY_ANCESTORS.get(category, ()))
