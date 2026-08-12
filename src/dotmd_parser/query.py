"""dotmd-parser — SPARQL query engine over a built ontology.ttl (v3).

Raw SPARQL passthrough + a few named queries (properties / relations /
defines / list) as parameterized SPARQL templates. Single backend: rdflib
over ontology.ttl. No inference (asserted graph only). Reads only; never
mutates the ontology.
"""
from __future__ import annotations

import json
from pathlib import Path

STANDARD_PREFIXES = {"owl", "rdfs", "xsd", "skos", "rdf", "xml"}


def _require_rdflib():
    try:
        from rdflib import Graph
        return Graph
    except ImportError as e:
        raise RuntimeError(
            "rdflib is required for ontology-query. Install it: "
            "pip install 'dotmd-parser[rdf]'"
        ) from e


def load_graph(directory) -> tuple[object, dict]:
    """Parse <dir>/ontology/ontology.ttl and resolve the ontology namespace/prefix."""
    Graph = _require_rdflib()
    base = Path(directory).resolve()
    ttl = base / "ontology" / "ontology.ttl"
    if not ttl.exists():
        raise FileNotFoundError(
            f"{ttl} not found. Build the ontology first: dotmd-parser ontology \"{directory}\"."
        )
    graph = Graph()
    graph.parse(data=ttl.read_text(encoding="utf-8"), format="turtle")

    meta = _resolve_meta(base, graph)
    return graph, meta


def _resolve_meta(base: Path, graph) -> dict:
    # 1) prefer ontology.json meta
    j = base / "ontology" / "ontology.json"
    if j.exists():
        try:
            ir = json.loads(j.read_text(encoding="utf-8"))
            m = ir.get("meta", {})
            if m.get("namespace") and m.get("prefix"):
                return {"namespace": m["namespace"], "prefix": m["prefix"]}
        except (json.JSONDecodeError, OSError):
            pass
    # 2) fall back to the first non-standard @prefix in the ttl
    for prefix, ns in graph.namespaces():
        if prefix and prefix not in STANDARD_PREFIXES:
            return {"namespace": str(ns), "prefix": prefix}
    raise RuntimeError(
        "Could not resolve the ontology namespace. Ensure ontology.json exists "
        "(dotmd-parser ontology emits it) or the ttl declares a custom @prefix."
    )


def run_sparql(graph, query: str) -> dict:
    """Execute a SPARQL query; shape the result by its type."""
    result = graph.query(query)
    rtype = str(result.type).upper()  # SELECT | ASK | CONSTRUCT | DESCRIBE
    out = {"type": "select", "columns": [], "rows": [], "boolean": False, "turtle": ""}

    if rtype == "ASK":
        out["type"] = "ask"
        out["boolean"] = bool(result.askAnswer)
        return out
    if rtype in ("CONSTRUCT", "DESCRIBE"):
        out["type"] = "graph"
        out["turtle"] = result.graph.serialize(format="turtle")
        return out

    # SELECT
    columns = [str(v) for v in result.vars]
    rows = []
    for binding in result:
        rows.append([("" if binding[v] is None else str(binding[v])) for v in result.vars])
    rows.sort(key=lambda r: tuple(r))   # deterministic order
    out["columns"] = columns
    out["rows"] = rows
    return out


_BAD_ARG_CHARS = set(' \t\n{}<>"')

_NAMED_TEMPLATES = {
    "properties": (
        "SELECT ?p ?kind ?range WHERE {{ ?p rdfs:domain ns:{arg} . "
        "{{ ?p a owl:DatatypeProperty BIND(\"datatype\" AS ?kind) }} UNION "
        "{{ ?p a owl:ObjectProperty BIND(\"object\" AS ?kind) }} "
        "OPTIONAL {{ ?p rdfs:range ?range }} }}"
    ),
    "relations": (
        "SELECT ?p ?domain ?range WHERE {{ ?p a owl:ObjectProperty ; "
        "rdfs:domain ?domain ; rdfs:range ?range . "
        "FILTER(?domain = ns:{arg} || ?range = ns:{arg}) }}"
    ),
    "defines": "SELECT ?doc WHERE {{ ns:{arg} ns:sourceDoc ?doc }}",
    "list": None,  # handled specially (arg selects the enumeration)
}

_LIST_TEMPLATES = {
    "classes": "SELECT ?c WHERE { ?c a owl:Class }",
    "properties": ("SELECT ?p ?kind WHERE { "
                   "{ ?p a owl:DatatypeProperty BIND(\"datatype\" AS ?kind) } UNION "
                   "{ ?p a owl:ObjectProperty BIND(\"object\" AS ?kind) } }"),
    "vocabularies": "SELECT ?v WHERE { ?v a skos:ConceptScheme }",
}


def _prefix_block(meta: dict) -> str:
    return (
        f"PREFIX ns: <{meta['namespace']}> "
        "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
    )


def named_query(graph, meta: dict, kind: str, arg: str) -> dict:
    if kind not in _NAMED_TEMPLATES:
        raise ValueError(f"unknown query kind: {kind} "
                         f"(choose from {sorted(_NAMED_TEMPLATES)})")
    if not arg:
        raise ValueError(f"query '{kind}' requires an argument")
    if set(arg) & _BAD_ARG_CHARS:
        raise ValueError(f"invalid characters in argument: {arg!r}")

    if kind == "list":
        body = _LIST_TEMPLATES.get(arg)
        if body is None:
            raise ValueError("list requires one of: classes, properties, vocabularies")
    else:
        body = _NAMED_TEMPLATES[kind].format(arg=arg)

    return run_sparql(graph, _prefix_block(meta) + body)
