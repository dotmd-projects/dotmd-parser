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
