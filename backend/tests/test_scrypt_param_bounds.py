"""KDF parameters from a backup envelope must be bounded.

`n`, `r` and `p` for the config-backup passphrase KDF travel in the file's
CLEARTEXT envelope, and were read straight into scrypt with `int()` and no
bounds. scrypt's memory cost is roughly 128 * n * r * p bytes, so `n=2**30,
r=8` asks for about a terabyte - the container is OOM-killed while parsing,
before the passphrase is even checked (audit 2026-07-30).

Importing a third-party backup is a documented DR/migration flow, so "an admin
supplied the file" is not a reason to trust its contents.
"""
from __future__ import annotations

import pytest

from app.utils.crypto import (
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    ScryptParamsRejectedError,
    derive_backup_key,
    new_backup_salt,
    validate_scrypt_params,
)


def test_our_own_defaults_are_accepted():
    """Control: whatever we bound to must not reject what we ourselves write."""
    assert validate_scrypt_params(SCRYPT_N, SCRYPT_R, SCRYPT_P) == (
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
    )


@pytest.mark.parametrize(
    "n,r,p,why",
    [
        (2**30, 8, 1, "~1 TiB of memory"),
        (2**20, 64, 16, "product overflows the ceiling even with each value in range"),
        (2**14, 8, 10_000, "p unbounded"),
        (2**14, 100_000, 1, "r unbounded"),
        (12345, 8, 1, "n not a power of two"),
        (0, 8, 1, "n zero"),
        (-2, 8, 1, "n negative"),
        (2**14, 0, 1, "r zero"),
        (2**14, 8, 0, "p zero"),
    ],
)
def test_hostile_parameters_are_rejected(n, r, p, why):
    with pytest.raises(ScryptParamsRejectedError):
        validate_scrypt_params(n, r, p)


def test_derive_backup_key_enforces_the_bounds_itself():
    """The check lives in derive_backup_key, not only at the call site, so a
    future caller cannot forget it."""
    with pytest.raises(ScryptParamsRejectedError):
        derive_backup_key("pw", new_backup_salt(), n=2**30, r=8, p=1)


def test_derive_backup_key_still_works_normally():
    key = derive_backup_key("pw", new_backup_salt(), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    assert isinstance(key, bytes) and len(key) > 0
