"""One definition of "make this search term safe inside LIKE".

Seven list endpoints build `ilike(f"%{q}%")` from an admin- or user-typed
search box. `%` and `_` are wildcards there, so a term containing either
matched everything (`%`) or any single character (`_`) - a `_` in an email
address is common, and the admin mail-log, inbox and session searches had no
escaping at all while the share, user, file-history and IP-block searches each
carried their own copy of the same three `replace` calls.

Pair `escape_like(q)` with `escape="\\"` on the `like`/`ilike` call, always:
the escape character only means something to the database when it is declared
on the operator.
"""
from __future__ import annotations

LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Escape LIKE metacharacters so ``term`` matches itself literally."""
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def contains(term: str) -> str:
    """The ``%<escaped term>%`` pattern for a substring search."""
    return f"%{escape_like(term)}%"
