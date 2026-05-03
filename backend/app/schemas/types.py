"""Custom field types used across schemas.

`EmailLike` is a permissive email type — it accepts `.local`, `.test`, `.dev`
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
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    ),
]
