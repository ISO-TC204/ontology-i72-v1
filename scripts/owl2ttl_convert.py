#!/usr/bin/env python3
"""Convert docs/i72.owl into RITSO-style modular Turtle files.

Produces:
  docs/CorePattern.ttl
  docs/IndicatorsPattern.ttl
  docs/IndicatorsSHACL.ttl
  docs/i72.ttl
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parents[1]
OWL_PATH = ROOT / "docs" / "i72.owl"
OUT_DIR = ROOT / "docs"

NS = Namespace("https://w3id.org/citydata/21972/v1/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
VANN = Namespace("http://purl.org/vocab/vann/")
CC = Namespace("http://creativecommons.org/ns#")
DC = Namespace("http://purl.org/dc/elements/1.1/")
OM = Namespace("http://www.wurvoc.org/vocabularies/om-1.8/")
ADMS = Namespace("http://www.w3.org/ns/adms#")
SCHEMA = Namespace("http://schema.org/")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
TIME = Namespace("http://www.w3.org/2006/time#")
DASH = Namespace("http://datashapes.org/dash#")
SH = Namespace("http://www.w3.org/ns/shacl#")

# Broken prefix present in the source OWL xmlns declaration
GEO_TYPO = Namespace("http://http://www.opengis.net/ont/geosparql#")

CORE_SUBJECTS = {
    NS.ISO21972Thing,
    NS.iso21972ObjectProperty,
    NS.iso21972DataProperty,
}

# Predicates written in a stable, readable order
TYPE_FIRST = [RDF.type]
CLASS_ORDER = [
    RDFS.subClassOf,
    OWL.equivalentClass,
    OWL.disjointWith,
    OWL.disjointUnionOf,
    SKOS.definition,
    RDFS.label,
    OM.alternative_label,
    SKOS.example,
    SKOS.note,
]
PROP_ORDER = [
    RDFS.subPropertyOf,
    RDF.type,  # extra types e.g. FunctionalProperty (primary type handled separately)
    OWL.inverseOf,
    OWL.propertyChainAxiom,
    SKOS.definition,
    RDFS.domain,
    RDFS.range,
    SCHEMA.domainIncludes,
    SCHEMA.rangeIncludes,
    RDFS.label,
    OM.alternative_label,
]
IND_ORDER = [
    RDF.type,
    SKOS.definition,
    RDFS.label,
]

# Promoted to skos:definition; do not re-emit
SUPPRESSED_ANNOTATIONS = {RDFS.comment, DC.description, DCTERMS.description, SKOS.definition}

RESTRICT_PRED_ORDER = [
    RDF.type,
    OWL.onProperty,
    OWL.onClass,
    OWL.onDataRange,
    OWL.allValuesFrom,
    OWL.someValuesFrom,
    OWL.hasValue,
    OWL.cardinality,
    OWL.minCardinality,
    OWL.maxCardinality,
    OWL.qualifiedCardinality,
    OWL.minQualifiedCardinality,
    OWL.maxQualifiedCardinality,
]


def qname(term, nsm) -> str:
    if isinstance(term, URIRef):
        s = str(term)
        if s.startswith(str(NS)):
            local = s[len(str(NS)) :]
            if local and "/" not in local and "#" not in local:
                return f":{local}"
        # Explicit prefix map to avoid rdflib inventing schema1:/geo1:
        prefix_map = [
            ("schema:", str(SCHEMA)),
            ("geo:", str(GEO)),
            ("time:", str(TIME)),
            ("xsd:", str(XSD)),
            ("owl:", str(OWL)),
            ("rdf:", str(RDF)),
            ("rdfs:", str(RDFS)),
            ("skos:", str(SKOS)),
            ("dcterms:", str(DCTERMS)),
            ("om-1:", str(OM)),
            ("dash:", str(DASH)),
            ("sh:", str(SH)),
            ("vann:", str(VANN)),
            ("cc:", str(CC)),
            ("adms:", str(ADMS)),
            ("i72:", str(NS)),
        ]
        for pfx, iri in prefix_map:
            if s.startswith(iri):
                return pfx + s[len(iri) :]
        try:
            return term.n3(nsm)
        except Exception:
            return f"<{s}>"
    if isinstance(term, Literal):
        return term.n3(nsm)
    if isinstance(term, BNode):
        return "_:" + str(term)
    return str(term)


def is_list_node(g: Graph, node) -> bool:
    return (node, RDF.first, None) in g or node == RDF.nil


def list_items(g: Graph, node):
    return list(Collection(g, node))


def union_members(g: Graph, node) -> list | None:
    """If node is an owl:unionOf class expression, return its members; else None."""
    if not isinstance(node, BNode):
        return None
    union_col = g.value(node, OWL.unionOf)
    if union_col is None:
        return None
    return list_items(g, union_col)


def expand_domain_range_pairs(g: Graph, subject) -> list[tuple]:
    """
    Prefer schema:domainIncludes / schema:rangeIncludes over rdfs:domain/range
    when the filler is an owl:unionOf blank node (RITSO style).
    """
    pairs = []
    for pred, includes_pred in (
        (RDFS.domain, SCHEMA.domainIncludes),
        (RDFS.range, SCHEMA.rangeIncludes),
    ):
        for obj in g.objects(subject, pred):
            members = union_members(g, obj)
            if members is not None:
                for m in sorted(members, key=str):
                    pairs.append((includes_pred, m))
            else:
                pairs.append((pred, obj))
    return pairs


def rewrite_term(term):
    """Fix known IRI typos from the source file."""
    if isinstance(term, URIRef):
        s = str(term)
        if s.startswith(str(GEO_TYPO)):
            return URIRef(str(GEO) + s[len(str(GEO_TYPO)) :])
        if s == "http://schema.org/City" or s.startswith("http://schema.org/"):
            return URIRef(s.replace("http://schema.org/", str(SCHEMA)))
        # Prefer https schema if encountered
        if s.startswith("https://schema.org/"):
            return URIRef(s.replace("https://schema.org/", str(SCHEMA)))
    return term


def load_graph() -> Graph:
    g = Graph()
    g.parse(OWL_PATH)
    # Rewrite typo IRIs in-place by rebuilding
    fixed = Graph()
    for s, p, o in g:
        fixed.add((rewrite_term(s), rewrite_term(p), rewrite_term(o)))
    return fixed


def definition_text(g: Graph, subject) -> Literal | None:
    """Prefer existing skos:definition; else promote comment/description."""
    for pred in (SKOS.definition, RDFS.comment, DC.description, DCTERMS.description):
        vals = list(g.objects(subject, pred))
        if vals:
            v = vals[0]
            if isinstance(v, Literal):
                return Literal(str(v), lang=v.language or "en")
            return Literal(str(v), lang="en")
    return None


def restriction_pairs(g: Graph, node: BNode):
    """Return predicate/object pairs for a restriction.

    Keep OWL-legal form:
      - qualified cardinalities use owl:onClass / owl:onDataRange
      - universal restrictions use owl:allValuesFrom (not bare owl:onClass)
    """
    pairs = []
    props = {p: o for p, o in g.predicate_objects(node)}
    pairs.append((RDF.type, OWL.Restriction))

    on_prop = props.get(OWL.onProperty)
    if on_prop is not None:
        pairs.append((OWL.onProperty, on_prop))

    has_qcard = any(
        p in props
        for p in (
            OWL.qualifiedCardinality,
            OWL.minQualifiedCardinality,
            OWL.maxQualifiedCardinality,
        )
    )
    on_class = props.get(OWL.onClass)
    all_from = props.get(OWL.allValuesFrom)

    if has_qcard:
        if on_class is not None:
            pairs.append((OWL.onClass, on_class))
        elif all_from is not None and not (
            isinstance(all_from, URIRef) and str(all_from).startswith(str(XSD))
        ):
            # Unexpected: treat class filler with cardinality as onClass
            pairs.append((OWL.onClass, all_from))
        if all_from is not None and isinstance(all_from, URIRef) and str(all_from).startswith(str(XSD)):
            pairs.append((OWL.allValuesFrom, all_from))
    else:
        # Universal / existential style — never emit bare owl:onClass
        if all_from is not None:
            pairs.append((OWL.allValuesFrom, all_from))
        elif on_class is not None:
            pairs.append((OWL.allValuesFrom, on_class))

    for pred in (
        OWL.onDataRange,
        OWL.someValuesFrom,
        OWL.hasValue,
        OWL.cardinality,
        OWL.minCardinality,
        OWL.maxCardinality,
        OWL.qualifiedCardinality,
        OWL.minQualifiedCardinality,
        OWL.maxQualifiedCardinality,
    ):
        if pred in props:
            pairs.append((pred, props[pred]))

    # Preserve unexpected predicates
    known = {
        RDF.type,
        OWL.onProperty,
        OWL.onClass,
        OWL.allValuesFrom,
        OWL.onDataRange,
        OWL.someValuesFrom,
        OWL.hasValue,
        OWL.cardinality,
        OWL.minCardinality,
        OWL.maxCardinality,
        OWL.qualifiedCardinality,
        OWL.minQualifiedCardinality,
        OWL.maxQualifiedCardinality,
    }
    for pred, obj in sorted(props.items(), key=lambda x: str(x[0])):
        if pred not in known:
            pairs.append((pred, obj))
    return pairs


def is_restriction(g: Graph, node) -> bool:
    return isinstance(node, BNode) and (node, RDF.type, OWL.Restriction) in g


def is_class_expr(g: Graph, node) -> bool:
    if not isinstance(node, BNode):
        return False
    if (node, RDF.type, OWL.Class) in g or (node, RDF.type, OWL.Restriction) in g:
        return True
    if (node, OWL.unionOf, None) in g:
        return True
    if (node, OWL.intersectionOf, None) in g:
        return True
    if (node, OWL.complementOf, None) in g:
        return True
    if (node, OWL.oneOf, None) in g:
        return True
    return False


class TurtleWriter:
    def __init__(self, g: Graph):
        self.g = g
        # Use a clean namespace manager so parse-time auto-prefixes (schema1:, etc.)
        # do not leak into the serialised output.
        self.nsm = Graph().namespace_manager
        binds = {
            "": NS,
            "rdf": RDF,
            "rdfs": RDFS,
            "owl": OWL,
            "xsd": XSD,
            "skos": SKOS,
            "dcterms": DCTERMS,
            "vann": VANN,
            "cc": CC,
            "schema": SCHEMA,
            "geo": GEO,
            "time": TIME,
            "dash": DASH,
            "sh": SH,
            "om-1": OM,
            "adms": ADMS,
            "i72": NS,
        }
        for pfx, ns in binds.items():
            self.nsm.bind(pfx, ns, override=True)

    def term(self, t) -> str:
        return qname(t, self.nsm)

    def write_collection(self, node, indent: int) -> list[str]:
        items = list_items(self.g, node)
        pad = " " * indent
        if not items:
            return ["()"]
        lines = ["("]
        for item in items:
            rendered = self.write_object(item, indent + 4)
            if len(rendered) == 1:
                lines.append(f"{pad}    {rendered[0]}")
            else:
                lines.append(f"{pad}    {rendered[0]}")
                lines.extend(rendered[1:])
        lines.append(f"{pad})")
        return lines

    def write_bnode(self, node: BNode, indent: int) -> list[str]:
        g = self.g
        pad = " " * indent
        inner = " " * (indent + 4)

        # Inverse-only blank node used as onProperty
        inv = list(g.objects(node, OWL.inverseOf))
        other = [(p, o) for p, o in g.predicate_objects(node) if p != OWL.inverseOf]
        if inv and not other:
            inv_lines = self.write_object(inv[0], indent + 4)
            if len(inv_lines) == 1:
                return [f"[ owl:inverseOf {inv_lines[0]} ]"]
            lines = ["[", f"{inner}owl:inverseOf {inv_lines[0]}"]
            lines.extend(inv_lines[1:])
            lines.append(f"{pad}]")
            return lines

        if is_restriction(g, node):
            pairs = restriction_pairs(g, node)
        elif is_class_expr(g, node) or (node, OWL.unionOf, None) in g or (
            node,
            OWL.intersectionOf,
            None,
        ) in g:
            pairs = []
            types = list(g.objects(node, RDF.type))
            if OWL.Class in types or not types:
                pairs.append((RDF.type, OWL.Class))
            for pred in (OWL.unionOf, OWL.intersectionOf, OWL.complementOf, OWL.oneOf):
                for obj in g.objects(node, pred):
                    pairs.append((pred, obj))
            for pred, obj in g.predicate_objects(node):
                if pred not in {RDF.type, OWL.unionOf, OWL.intersectionOf, OWL.complementOf, OWL.oneOf}:
                    pairs.append((pred, obj))
        else:
            pairs = list(g.predicate_objects(node))
            # stable order
            pairs.sort(key=lambda po: str(po[0]))

        if not pairs:
            return ["[]"]

        lines = ["["]
        for i, (pred, obj) in enumerate(pairs):
            pred_s = self.term(pred)
            obj_lines = self.write_object(obj, indent + 4)
            sep = " ;" if i < len(pairs) - 1 else ""
            # Align predicate width roughly like sample files
            pred_col = f"{pred_s:<28}"
            if len(obj_lines) == 1:
                lines.append(f"{inner}{pred_col}{obj_lines[0]}{sep}")
            else:
                lines.append(f"{inner}{pred_col}{obj_lines[0]}")
                for j, ol in enumerate(obj_lines[1:]):
                    if j == len(obj_lines) - 2:
                        lines.append(f"{ol}{sep}")
                    else:
                        lines.append(ol)
        lines.append(f"{pad}]")
        return lines

    def write_object(self, obj, indent: int) -> list[str]:
        if isinstance(obj, BNode):
            if is_list_node(self.g, obj):
                return self.write_collection(obj, indent)
            return self.write_bnode(obj, indent)
        return [self.term(obj)]

    def ordered_po(self, subject, order) -> list[tuple]:
        g = self.g
        used = set()
        result = []
        # Always emit rdf:type values first (except we may pass custom order)
        types = list(g.objects(subject, RDF.type))
        primary = {
            OWL.Class,
            OWL.ObjectProperty,
            OWL.DatatypeProperty,
            OWL.AnnotationProperty,
            OWL.NamedIndividual,
            OWL.Ontology,
        }
        types_sorted = sorted(types, key=lambda t: (0 if t in primary else 1, str(t)))
        if types_sorted and (order[0] == RDF.type):
            for t in types_sorted:
                result.append((RDF.type, t))
            used.add(RDF.type)

        for pred in order:
            if pred == RDF.type:
                continue
            # Expand union blank-node domains/ranges into schema:*Includes
            if pred in (RDFS.domain, RDFS.range, SCHEMA.domainIncludes, SCHEMA.rangeIncludes):
                if RDFS.domain in used or SCHEMA.domainIncludes in used:
                    continue
                expanded = expand_domain_range_pairs(g, subject)
                result.extend(expanded)
                used.add(RDFS.domain)
                used.add(RDFS.range)
                used.add(SCHEMA.domainIncludes)
                used.add(SCHEMA.rangeIncludes)
                continue
            objs = list(g.objects(subject, pred))
            # Stable sort: URIRefs by string, blank nodes last as encountered
            named = sorted([o for o in objs if not isinstance(o, BNode)], key=str)
            blanks = [o for o in objs if isinstance(o, BNode)]
            for o in named + blanks:
                result.append((pred, o))
            if objs:
                used.add(pred)

        # Remaining predicates
        remaining = defaultdict(list)
        for pred, obj in g.predicate_objects(subject):
            if pred in used:
                continue
            # Skip annotation sources already promoted to skos:definition
            if pred in SUPPRESSED_ANNOTATIONS:
                continue
            remaining[pred].append(obj)
        for pred in sorted(remaining, key=str):
            for obj in remaining[pred]:
                result.append((pred, obj))
        return result

    def write_subject(self, subject, order) -> str:
        g = self.g
        lines_out = [self.term(subject)]

        # Inject skos:definition when comments/descriptions exist
        defn = definition_text(g, subject)
        pairs = self.ordered_po(subject, order)
        if defn is not None:
            # Insert definition after structural axioms when possible
            insert_at = None
            for i, (p, _) in enumerate(pairs):
                if p in {
                    SKOS.definition,
                    RDFS.label,
                    OM.alternative_label,
                    SKOS.example,
                    SKOS.note,
                }:
                    insert_at = i
                    break
            # Remove any existing definition-like we'll replace
            pairs = [(p, o) for p, o in pairs if p != SKOS.definition]
            if insert_at is None:
                # after last structural property
                structural = {
                    RDF.type,
                    RDFS.subClassOf,
                    RDFS.subPropertyOf,
                    OWL.equivalentClass,
                    OWL.disjointWith,
                    OWL.disjointUnionOf,
                    OWL.inverseOf,
                    RDFS.domain,
                    RDFS.range,
                }
                insert_at = 0
                for i, (p, _) in enumerate(pairs):
                    if p in structural:
                        insert_at = i + 1
                pairs.insert(insert_at, (SKOS.definition, defn))
            else:
                pairs.insert(insert_at, (SKOS.definition, defn))

        if not pairs:
            return f"{lines_out[0]} .\n"

        formatted = [f"{lines_out[0]}"]
        for i, (pred, obj) in enumerate(pairs):
            pred_s = self.term(pred)
            obj_lines = self.write_object(obj, 4)
            sep = " ;" if i < len(pairs) - 1 else " ."
            pred_col = f"{pred_s:<24}"
            if len(obj_lines) == 1:
                formatted.append(f"    {pred_col}{obj_lines[0]}{sep}")
            else:
                formatted.append(f"    {pred_col}{obj_lines[0]}")
                for j, ol in enumerate(obj_lines[1:]):
                    if j == len(obj_lines) - 2:
                        formatted.append(f"{ol}{sep}")
                    else:
                        formatted.append(ol)
        return "\n".join(formatted) + "\n"


def prefixes_block(extra: dict[str, str] | None = None) -> str:
    lines = [
        "@prefix : <https://w3id.org/citydata/21972/v1/> .",
        "@prefix i72: <https://w3id.org/citydata/21972/v1/> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix vann: <http://purl.org/vocab/vann/> .",
        "@prefix cc: <http://creativecommons.org/ns#> .",
        "@prefix dash: <http://datashapes.org/dash#> .",
        "@prefix schema: <http://schema.org/> .",
        "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
        "@prefix time: <http://www.w3.org/2006/time#> .",
        "@prefix om-1: <http://www.wurvoc.org/vocabularies/om-1.8/> .",
        "@prefix adms: <http://www.w3.org/ns/adms#> .",
    ]
    if extra:
        for pfx, iri in extra.items():
            lines.append(f"@prefix {pfx}: <{iri}> .")
    return "\n".join(lines) + "\n\n"


def section(title: str) -> str:
    bar = "# " + "=" * 48
    return f"\n{bar}\n#                {title}\n{bar}\n\n"


def ontology_header(
    iri: URIRef,
    title: str,
    definition: str,
    imports: list[URIRef] | None = None,
    version_iri: URIRef | None = None,
    extra: list[tuple] | None = None,
) -> str:
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [qname(iri, Graph().namespace_manager).replace(
        str(iri), f":{str(iri)[len(str(NS)):]}" if str(iri).startswith(str(NS)) else f"<{iri}>"
    )]
    # Manual formatting with local names
    if str(iri).startswith(str(NS)):
        local = str(iri)[len(str(NS)) :]
        subj = f":{local}" if local else ":"
    else:
        subj = f"<{iri}>"

    body = [
        f"{subj}",
        f"    rdf:type                      owl:Ontology ;",
        f'    dcterms:title                 "{title}"@en ;',
        f'    skos:definition               "{definition}"@en ;',
        f'    vann:preferredNamespaceUri    "https://w3id.org/citydata/21972/v1/" ;',
        f'    vann:preferredNamespacePrefix "i72" ;',
        f'    dcterms:license               <http://creativecommons.org/licenses/by/4.0/> ;',
        f'    dcterms:creator               "Mark S. Fox"@en ;',
        f'    dcterms:creator               "Kenneth Vaughn"@en ;',
        f'    dcterms:modified              "{modified}"^^xsd:date ;',
        f'    owl:versionInfo               "1.1.0" ;',
    ]
    if version_iri is not None:
        body.append(f"    owl:versionIRI                <{version_iri}> ;")
    if imports:
        for imp in imports:
            if str(imp).startswith(str(NS)):
                local = str(imp)[len(str(NS)) :]
                body.append(f"    owl:imports                   :{local} ;")
            else:
                body.append(f"    owl:imports                   <{imp}> ;")
    if extra:
        for pred, obj in extra:
            body.append(f"    {pred:<28} {obj} ;")
    # terminate last line
    body[-1] = body[-1].rstrip(" ;") + " ."
    return "\n".join(body) + "\n"


def classify_subjects(g: Graph):
    classes = []
    obj_props = []
    data_props = []
    individuals = []
    other = []
    ann_props = []

    skip_types = {
        OWL.Ontology,
        OWL.AnnotationProperty,
        OWL.AllDisjointClasses,
        OWL.Restriction,
        OWL.Class,  # handled via typed subjects that are named
    }

    named = set()
    for s in g.subjects():
        if isinstance(s, URIRef) and str(s).startswith(str(NS)):
            named.add(s)

    for s in sorted(named, key=str):
        types = set(g.objects(s, RDF.type))
        if OWL.Ontology in types:
            continue
        if OWL.AnnotationProperty in types:
            # skip external annotation property stubs; keep only local if any
            continue
        if OWL.ObjectProperty in types:
            obj_props.append(s)
        elif OWL.DatatypeProperty in types:
            data_props.append(s)
        elif OWL.Class in types:
            classes.append(s)
        elif OWL.NamedIndividual in types or any(
            t for t in types if isinstance(t, URIRef) and str(t).startswith(str(NS))
        ):
            # Named individuals often typed as both NamedIndividual and a class
            if OWL.NamedIndividual in types or (
                OWL.Class not in types
                and OWL.ObjectProperty not in types
                and OWL.DatatypeProperty not in types
            ):
                individuals.append(s)
            else:
                classes.append(s)
        else:
            # untyped local IRIs (e.g. root properties declared without useful type beyond OP/DP)
            other.append(s)

    # Ensure root props are included even if only typed as ObjectProperty
    return classes, obj_props, data_props, individuals


def gather_bnode_closure(g: Graph, roots: set) -> set:
    """All triples needed for the given named subjects, including nested blank nodes."""
    needed = set()
    stack = list(roots)
    seen_b = set()
    while stack:
        s = stack.pop()
        for p, o in g.predicate_objects(s):
            needed.add((s, p, o))
            if isinstance(o, BNode) and o not in seen_b:
                seen_b.add(o)
                stack.append(o)
            if isinstance(o, BNode) and is_list_node(g, o):
                for item in list_items(g, o):
                    if isinstance(item, BNode) and item not in seen_b:
                        seen_b.add(item)
                        stack.append(item)
    # Also include list linkage triples
    for b in list(seen_b):
        for p, o in g.predicate_objects(b):
            needed.add((b, p, o))
            if isinstance(o, BNode) and o not in seen_b:
                seen_b.add(o)
                # continue expansion
                stack2 = [o]
                while stack2:
                    n = stack2.pop()
                    for p2, o2 in g.predicate_objects(n):
                        needed.add((n, p2, o2))
                        if isinstance(o2, BNode) and o2 not in seen_b:
                            seen_b.add(o2)
                            stack2.append(o2)
    return needed


def disjoint_axioms(g: Graph):
    axioms = []
    for s in g.subjects(RDF.type, OWL.AllDisjointClasses):
        members = list(g.objects(s, OWL.members))
        axioms.append((s, members[0] if members else None))
    return axioms


def write_core(g: Graph, writer: TurtleWriter) -> str:
    parts = [prefixes_block()]
    parts.append(
        ontology_header(
            NS.CorePattern,
            "ISO 21972 Ontology - Core Pattern",
            "Core organisational concepts for the ISO 21972 upper-level ontology for smart city indicators.",
            version_iri=URIRef("https://w3id.org/citydata/21972/v1/CorePattern/r1.1.0"),
        )
    )
    parts.append(section("Core Classes"))
    # ISO21972Thing with dash:abstract
    parts.append(
        """:ISO21972Thing
    rdf:type        owl:Class ;
    dash:abstract   true ;
    skos:definition "A class used to organise all classes defined in the ISO 21972 ontology."@en .
"""
    )
    parts.append(section("Core Properties"))
    parts.append(
        """:iso21972ObjectProperty
    rdf:type           owl:ObjectProperty ;
    dash:abstract      true ;
    skos:definition    "An object property used to organise all object properties defined in the ISO 21972 ontology."@en .

:iso21972DataProperty
    rdf:type           owl:DatatypeProperty ;
    dash:abstract      true ;
    skos:definition    "A data property used to organise all data properties defined in the ISO 21972 ontology."@en .
"""
    )
    return "".join(parts)


def write_indicators(g: Graph, writer: TurtleWriter) -> str:
    classes, obj_props, data_props, individuals = classify_subjects(g)
    # Exclude core subjects from this module
    classes = [c for c in classes if c not in CORE_SUBJECTS]
    obj_props = [p for p in obj_props if p not in CORE_SUBJECTS]
    data_props = [p for p in data_props if p not in CORE_SUBJECTS]

    parts = [prefixes_block()]
    parts.append(
        ontology_header(
            NS.IndicatorsPattern,
            "ISO 21972 Ontology - Indicators Pattern",
            "Concepts for quantities, units of measure, measurement scales, statistics, and smart city indicators from ISO 21972.",
            imports=[NS.CorePattern],
            version_iri=URIRef("https://w3id.org/citydata/21972/v1/IndicatorsPattern/r1.1.0"),
            extra=[
                (
                    "dcterms:bibliographicCitation",
                    '"ISO 21972 Information technology — Upper level ontology for smart city indicators"',
                ),
                ("adms:relatedDocumentation", '"ISO21972"'),
                (
                    "owl:priorVersion",
                    "<https://w3id.org/citydata/21972/v1/1.1.0>",
                ),
                (
                    "rdfs:comment",
                    '"Archived import of iso21972.owl; converted to modular RITSO Turtle."@en',
                ),
            ],
        )
    )

    parts.append(section("Object Properties"))
    for s in obj_props:
        parts.append(writer.write_subject(s, [RDF.type] + PROP_ORDER))
        parts.append("\n")

    parts.append(section("Data Properties"))
    for s in data_props:
        parts.append(writer.write_subject(s, [RDF.type] + PROP_ORDER))
        parts.append("\n")

    parts.append(section("Classes"))
    for s in classes:
        parts.append(writer.write_subject(s, [RDF.type] + CLASS_ORDER))
        parts.append("\n")

    parts.append(section("Individuals"))
    for s in individuals:
        parts.append(writer.write_subject(s, IND_ORDER))
        parts.append("\n")

    parts.append(section("General Axioms"))
    for s, members_node in disjoint_axioms(g):
        if members_node is None:
            continue
        items = list_items(g, members_node)
        item_lines = "\n".join(f"            {writer.term(i)}" for i in items)
        parts.append(
            f"""[]
    rdf:type     owl:AllDisjointClasses ;
    owl:members  (
{item_lines}
        ) .
"""
        )
        parts.append("\n")

    return "".join(parts)


def write_shacl(g: Graph, writer: TurtleWriter) -> str:
    """Derive SHACL node shapes from qualified cardinality restrictions."""
    parts = [
        prefixes_block({"sh": "http://www.w3.org/ns/shacl#"}),
    ]
    parts.append(
        ontology_header(
            NS.IndicatorsSHACL,
            "SHACL constraints for the ISO 21972 Indicators pattern",
            "Validation shapes derived from qualified cardinality restrictions in the Indicators pattern.",
            imports=[NS.IndicatorsPattern],
        )
    )
    parts.append(section("Node Shapes"))

    shapes = []
    for cls in sorted(
        (s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef) and str(s).startswith(str(NS))),
        key=str,
    ):
        props = []
        for super_ in g.objects(cls, RDFS.subClassOf):
            if not is_restriction(g, super_):
                continue
            on_prop = g.value(super_, OWL.onProperty)
            if on_prop is None or isinstance(on_prop, BNode):
                continue
            on_class = g.value(super_, OWL.onClass) or g.value(super_, OWL.allValuesFrom)
            on_data = g.value(super_, OWL.onDataRange)
            qcard = g.value(super_, OWL.qualifiedCardinality)
            minq = g.value(super_, OWL.minQualifiedCardinality)
            maxq = g.value(super_, OWL.maxQualifiedCardinality)
            card = g.value(super_, OWL.cardinality)

            if not any([qcard, minq, maxq, card]):
                # Universal class restriction → class-only property shape without cardinality
                if on_class is not None and isinstance(on_class, URIRef):
                    props.append((on_prop, on_class, None, None, None))
                continue

            min_c = None
            max_c = None
            if qcard is not None:
                min_c = max_c = int(qcard)
            if card is not None:
                min_c = max_c = int(card)
            if minq is not None:
                min_c = int(minq)
            if maxq is not None:
                max_c = int(maxq)

            filler = on_class if on_class is not None else on_data
            if filler is not None and not isinstance(filler, URIRef):
                filler = None
            props.append((on_prop, filler, min_c, max_c, on_data is not None))

        if props:
            shapes.append((cls, props))

    for cls, props in shapes:
        local = str(cls)[len(str(NS)) :]
        lines = [
            f":{local}Shape",
            f"    rdf:type        sh:NodeShape ;",
            f"    sh:targetClass  :{local} ;",
        ]
        for i, (on_prop, filler, min_c, max_c, is_datatype) in enumerate(props):
            prop_local = writer.term(on_prop)
            block = ["    sh:property [", f"        sh:path  {prop_local} ;"]
            if filler is not None:
                key = "sh:datatype" if is_datatype or str(filler).startswith(str(XSD)) else "sh:class"
                block.append(f"        {key:<11} {writer.term(filler)} ;")
            if min_c is not None:
                block.append(f'        sh:minCount "{min_c}"^^xsd:integer ;')
            if max_c is not None:
                block.append(f'        sh:maxCount "{max_c}"^^xsd:integer ;')
            # strip trailing semicolon on last constraint line
            block[-1] = block[-1].rstrip(" ;")
            block.append("    ]")
            sep = " ;" if i < len(props) - 1 else " ."
            block[-1] = block[-1] + sep
            lines.extend(block)
        parts.append("\n".join(lines) + "\n\n")

    return "".join(parts)


def write_master() -> str:
    parts = [prefixes_block()]
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts.append(
        f""":
    rdf:type                      owl:Ontology ;
    dcterms:title                 "ISO 21972 Information technology — Upper level ontology for smart city indicators"@en ;
    skos:definition               "This ontology imports the ISO 21972 modules (core pattern, indicators pattern, and SHACL constraints)."@en ;
    vann:preferredNamespaceUri    "https://w3id.org/citydata/21972/v1/" ;
    vann:preferredNamespacePrefix "i72" ;
    dcterms:license               <http://creativecommons.org/licenses/by/4.0/> ;
    dcterms:creator               "Mark S. Fox"@en ;
    dcterms:creator               "Kenneth Vaughn"@en ;
    dcterms:bibliographicCitation "ISO 21972" ;
    dcterms:modified              "{modified}"^^xsd:date ;
    owl:versionInfo               "1.1.0" ;
    owl:versionIRI                <https://w3id.org/citydata/21972/v1/1.1.0> ;
    owl:imports                   :CorePattern ;
    owl:imports                   :IndicatorsPattern ;
    owl:imports                   :IndicatorsSHACL .
"""
    )
    return "".join(parts)


def main():
    g = load_graph()
    writer = TurtleWriter(g)

    core = write_core(g, writer)
    indicators = write_indicators(g, writer)
    shacl = write_shacl(g, writer)
    master = write_master()

    (OUT_DIR / "CorePattern.ttl").write_text(core, encoding="utf-8")
    (OUT_DIR / "IndicatorsPattern.ttl").write_text(indicators, encoding="utf-8")
    (OUT_DIR / "IndicatorsSHACL.ttl").write_text(shacl, encoding="utf-8")
    (OUT_DIR / "i72.ttl").write_text(master, encoding="utf-8")

    print("Wrote:")
    for name in ("CorePattern.ttl", "IndicatorsPattern.ttl", "IndicatorsSHACL.ttl", "i72.ttl"):
        path = OUT_DIR / name
        print(f"  {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
