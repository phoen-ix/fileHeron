"""One definition of "how wide is this column".

Several write paths clip a value to its column's declared width rather than to
a literal, because a clip to the WRONG width is the same failure with a longer
fuse - `audit_log.target_id` was clipped to 255 against a String(64), so the
clip did nothing at all (027fe08). Deriving the number from the column means a
migration that narrows it narrows the clip with it.

Doing that inline costs four lines every time: `.type` is typed
``TypeEngine[Any]`` so mypy cannot see `.length` without a cast, and a column
that somehow lost its width yields ``None`` - where ``s[:None]`` does not clip
at all, silently reinstating the defect the clip exists to prevent. That was
already written out twice; this is the same thing said once.
"""
from __future__ import annotations

from typing import Any, cast

from sqlalchemy import String


def declared_width(column: Any) -> int:
    """The declared character width of a bounded String column.

    Raises at import time (callers bind this to a module constant) rather than
    returning ``None``, so a column that lost its width is a loud failure
    instead of a clip that quietly stops clipping.
    """
    col_type = cast(String, column.type)
    length = col_type.length
    if length is None:  # pragma: no cover - defends the invariant
        raise RuntimeError(
            f"{column.table.name}.{column.name} must declare a length to clip against"
        )
    return int(length)
