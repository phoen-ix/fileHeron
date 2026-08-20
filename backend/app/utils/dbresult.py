"""How many rows a conditional UPDATE actually claimed.

`Session.execute()` is typed `Result[Any]`, but `rowcount` lives on
`CursorResult`. Every DML statement really does return a `CursorResult`, so
reading `.rowcount` off the result works at runtime and fails type checking -
which is why 21 call sites across 9 modules were each a mypy error, and why
those 9 modules sat behind a blanket exemption that also hid everything else
in them.

One definition rather than 21 casts: the conditional-UPDATE idiom is the
atomicity primitive behind refresh-token rotation, the public-link download
counter, share-approval transitions and TOTP claim, so the narrowing wants to
be stated once, where it can carry this note.

Returns the raw driver value deliberately - callers keep their own semantics
(`== 0`, `== 1`, `!= 1`, `or len(ids)`), and DBAPI may report -1 for "unknown",
which no caller should have silently turned into 0.
"""
from __future__ import annotations

from typing import Any, cast

from sqlalchemy.engine import CursorResult, Result


def updated_rows(result: Result[Any]) -> int:
    """Rows matched by the UPDATE/DELETE that produced `result`."""
    return cast("CursorResult[Any]", result).rowcount
