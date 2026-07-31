"""Custom field types used across schemas.

`EmailLike` is a permissive email type - it accepts `.local`, `.test`, `.dev`
domains that strict EmailStr (Pydantic + email-validator) reject. fileHeron
hashes and stores emails for indexed lookup but never *delivers* via DNS, so
strict deliverability validation is overkill and wrong for internal/dev use.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

EmailLike = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        # 254, not the theoretical 320: every email column in the schema is
        # VARCHAR(254) and MariaDB runs strict mode, so a longer address passed
        # validation and then died at flush() as an unhandled DataError - a 500
        # with a stack trace and an error_log row where the caller should have
        # got a clean 422 at the boundary. 254 is the deliverable ceiling anyway.
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    ),
]
