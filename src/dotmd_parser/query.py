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
