"""Small text helpers shared across services."""
from __future__ import annotations


def split_list(raw: str | None) -> list[str]:
    """A comma- or newline-separated list, each entry stripped, blanks dropped.

    The parse every allowlist, prefix list and code list was doing in its own
    loop - six copies of `for part in raw.split(","): part = part.strip()`
    (audit 2026-11-08). Accepting newlines everywhere is a superset of what each
    accepted: the admin textareas send them, the env values never contain them.
    """
    return [p for p in (s.strip() for s in (raw or "").replace("\n", ",").split(",")) if p]
