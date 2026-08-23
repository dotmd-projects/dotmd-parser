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
