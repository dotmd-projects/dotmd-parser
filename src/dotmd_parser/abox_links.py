"""dotmd-parser — ABox object-property links (v4c-obj).

Keys instances by a natural id column and materializes object-property links
(instance-to-instance triples) via foreign keys between two materialized
classes. stdlib only, deterministic; no imports from abox.py (one-way
dependency: abox imports abox_links).
"""
from __future__ import annotations

import re

# Minimum shared value count to accept a value-overlap FK-column match;
# mirrors _MIN_VALUE_OVERLAP in abox.py.
_MIN_VALUE_OVERLAP = 2

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def slug_key(value: str) -> str:
    """Deterministic Turtle-local-name-safe suffix from a raw key value.

    Replaces every run of chars outside [A-Za-z0-9_-] with a single '-',
    strips leading/trailing '-'; empty result becomes 'x'. The caller always
    prefixes 'Class_', which guarantees a leading letter for the full name.
    """
    s = _UNSAFE.sub("-", str(value)).strip("-")
    return s or "x"


def subject_uri(prefix: str, class_name: str, row: dict, rowindex: int,
                id_col: str | None) -> tuple[str, str | None]:
    """Return (uri, key) for one instance row.

    Unkeyed (id_col is None): prefix:Class_<rowindex>, key None.
    Keyed with a value:        prefix:Class_<slug(value)>, key <raw value>.
    Keyed but empty value:     prefix:Class__row<rowindex>, key None.
    """
    if id_col is None:
        return f"{prefix}:{class_name}_{rowindex}", None
    raw = (row.get(id_col) or "").strip()
    if not raw:
        return f"{prefix}:{class_name}__row{rowindex}", None
    return f"{prefix}:{class_name}_{slug_key(raw)}", raw


def parse_id_cols(args, mapped_classes) -> dict:
    """Parse ["Class=column", ...] -> {Class: column}. Validate format+membership."""
    out: dict = {}
    for arg in args or []:
        if "=" not in arg:
            raise ValueError(f"bad --id-col (expected Class=column): {arg!r}")
        cls, _, col = arg.partition("=")
        cls, col = cls.strip(), col.strip()
        if not col:
            raise ValueError(f"bad --id-col (empty column): {arg!r}")
        if cls not in mapped_classes:
            raise ValueError(f"--id-col class not in --map: {cls!r}")
        if cls in out:
            raise ValueError(f"duplicate class in --id-col: {cls!r}")
        out[cls] = col
    return out


def parse_links(args, oprops, id_cols, mapped_classes) -> dict:
    """Parse ["Src.prop=column", ...] -> {(Src, prop): column}.

    Validates the property exists (from == Src), both ends are mapped, and the
    target has an id-col. Column existence in the CSV is checked in run_abox.
    """
    by_name = {o["name"]: o for o in oprops}
    out: dict = {}
    for arg in args or []:
        if "=" not in arg or "." not in arg.split("=", 1)[0]:
            raise ValueError(f"bad --link (expected Src.prop=column): {arg!r}")
        lhs, _, col = arg.partition("=")
        src, _, prop = lhs.strip().partition(".")
        src, prop, col = src.strip(), prop.strip(), col.strip()
        if not col:
            raise ValueError(f"bad --link (empty column): {arg!r}")
        op = by_name.get(prop)
        if op is None:
            raise ValueError(f"--link unknown object property: {prop!r}")
        if op.get("from") != src:
            raise ValueError(
                f"--link {prop} is not from {src!r} (from={op.get('from')!r})")
        if src not in mapped_classes:
            raise ValueError(f"--link source class not in --map: {src!r}")
        tgt = op.get("to")
        if tgt not in mapped_classes:
            raise ValueError(f"--link target class not in --map: {tgt!r}")
        if tgt not in id_cols:
            raise ValueError(f"--link target class has no --id-col: {tgt!r}")
        if (src, prop) in out:
            raise ValueError(f"duplicate --link for {src}.{prop}")
        out[(src, prop)] = col
    return out


def resolve_fk_column(src_columns_values, tgt_keys, threshold):
    """Auto-detect the FK column by value overlap with the target key set.

    Returns (column, "value") for the best-overlapping accepted column, else
    (None, None). Accept when shared >= _MIN_VALUE_OVERLAP and
    shared / |col values| >= threshold. Ties break by column (fieldname) order.
    """
    best = None  # (shared, column)
    for col, vals in src_columns_values.items():
        if not vals:
            continue
        shared = len(vals & tgt_keys)
        if shared < _MIN_VALUE_OVERLAP:
            continue
        if shared / len(vals) < threshold:
            continue
        if best is None or shared > best[0]:   # strict > keeps first-in-order
            best = (shared, col)
    if best:
        return best[1], "value"
    return None, None


def materialize_links(oprops, class_ctx, explicit_links, threshold, prefix):
    """Emit object-property link triples between materialized, keyed classes.

    Returns (link_lines, reports). See module/spec docs for method semantics.
    """
    link_lines: list[str] = []
    reports: list[dict] = []
    for op in sorted(oprops, key=lambda o: o["name"]):
        src, tgt, prop = op.get("from"), op.get("to"), op["name"]
        if src not in class_ctx:
            continue  # 'from' side not materialized: not relevant
        rep = {"property": prop, "from": src, "to": tgt, "fk_column": None,
               "method": None, "emitted": 0, "dangling": 0,
               "dangling_examples": []}
        if tgt not in class_ctx:
            rep["method"] = "skipped-not-mapped"
            reports.append(rep)
            continue
        if not class_ctx[tgt].get("keyed"):
            rep["method"] = "skipped-no-id"
            reports.append(rep)
            continue
        tgt_keys = class_ctx[tgt]["keys"]
        col = explicit_links.get((src, prop))
        method = "explicit"
        if not col:
            col, method = resolve_fk_column(
                class_ctx[src]["columns_values"], tgt_keys, threshold)
        if not col:
            rep["method"] = "skipped-no-fk"
            reports.append(rep)
            continue
        rep["fk_column"] = col
        rep["method"] = method
        src_id_col = class_ctx[src].get("id_col")
        emitted = 0
        dangling = 0
        examples: list[str] = []
        for i, row in enumerate(class_ctx[src]["rows"], start=1):
            fk = (row.get(col) or "").strip()
            if not fk:
                continue
            if fk in tgt_keys:
                subj, _ = subject_uri(prefix, src, row, i, src_id_col)
                obj = f"{prefix}:{tgt}_{slug_key(fk)}"
                link_lines.append(f"{subj} {prefix}:{prop} {obj} .")
                emitted += 1
            else:
                dangling += 1
                if len(examples) < 5:
                    examples.append(fk)
        rep["emitted"] = emitted
        rep["dangling"] = dangling
        rep["dangling_examples"] = examples
        reports.append(rep)
    link_lines = sorted(link_lines)
    if link_lines:
        link_lines = ["# object-property links"] + link_lines + [""]
    return link_lines, reports
