"""Secrets at rest (v2.24.0): a key per secret, under an optional passphrase
layer, under the instance key.

What this pins: the passphrase is a KEY, not a gate (no stored hash, and the
instance key alone opens nothing); the instance layer is the OUTER one, so the
rotation script can re-wrap a secret without knowing its passphrase; and a
wrong passphrase is distinguishable from an instance-key mismatch.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.utils import crypto
from app.utils.crypto import (
    SecretPassphraseError,
    SecretUndecryptableError,
    open_secret_content,
    seal_secret,
    unwrap_secret_key,
)

TEXT = "hunter2 - and a newline\nand ümlauts"


def _open(sealed, passphrase=None) -> str:
    key = unwrap_secret_key(
        sealed.key_encrypted,
        kdf_salt=sealed.kdf_salt,
        kdf_params=sealed.kdf_params,
        passphrase=passphrase,
    )
    return open_secret_content(sealed.ciphertext, key)


def test_round_trip_without_a_passphrase():
    sealed = seal_secret(TEXT, None)
    assert sealed.kdf_salt is None and sealed.kdf_params is None
    assert _open(sealed) == TEXT


def test_round_trip_with_a_passphrase():
    sealed = seal_secret(TEXT, "a long passphrase")
    assert sealed.kdf_salt and sealed.kdf_params
    assert _open(sealed, "a long passphrase") == TEXT


@pytest.mark.parametrize("wrong", [None, "", "a long passphrasE", "nope"])
def test_a_wrong_or_missing_passphrase_is_its_own_error(wrong):
    sealed = seal_secret(TEXT, "a long passphrase")
    with pytest.raises(SecretPassphraseError):
        _open(sealed, wrong)


def test_the_instance_key_alone_does_not_open_a_passphrase_secret():
    """The passphrase is a layer of the key. Unwrapping the instance layer -
    what anyone holding the database AND the .env can do - yields another
    Fernet token, not the content key."""
    sealed = seal_secret(TEXT, "a long passphrase")
    inner = crypto._get_fernet().decrypt(sealed.key_encrypted.encode("ascii"))
    with pytest.raises(SecretUndecryptableError):
        open_secret_content(sealed.ciphertext, inner)


def test_nothing_stored_contains_the_text_or_the_passphrase():
    sealed = seal_secret(TEXT, "a long passphrase")
    stored = " ".join(str(v) for v in sealed.__dict__.values())
    assert "hunter2" not in stored
    assert "a long passphrase" not in stored


def test_two_seals_of_the_same_text_share_nothing():
    a, b = seal_secret(TEXT, "pw-pw-pw-pw"), seal_secret(TEXT, "pw-pw-pw-pw")
    assert a.ciphertext != b.ciphertext
    assert a.key_encrypted != b.key_encrypted
    assert a.kdf_salt != b.kdf_salt


def test_a_different_instance_key_is_undecryptable_not_a_wrong_passphrase(monkeypatch):
    """After a JWT_SECRET rotation without the script, the outer layer no
    longer opens. That must not be reported (or counted) as a wrong
    passphrase - the recipient did nothing wrong."""
    sealed = seal_secret(TEXT, "a long passphrase")
    monkeypatch.setattr(crypto, "_fernet_instance", Fernet(Fernet.generate_key()))
    with pytest.raises(SecretUndecryptableError):
        _open(sealed, "a long passphrase")


def test_rotating_the_outer_layer_keeps_the_passphrase_layer(monkeypatch):
    """What backend/scripts/rotate_jwt_secret.py does to `secrets.key_encrypted`:
    decrypt with the old instance key, encrypt with the new one. It never sees
    the passphrase, and the secret still opens with it afterwards."""
    sealed = seal_secret(TEXT, "a long passphrase")
    old = crypto._get_fernet()
    new = Fernet(Fernet.generate_key())
    rewrapped = new.encrypt(old.decrypt(sealed.key_encrypted.encode("ascii"))).decode("ascii")
    monkeypatch.setattr(crypto, "_fernet_instance", new)
    key = unwrap_secret_key(
        rewrapped,
        kdf_salt=sealed.kdf_salt,
        kdf_params=sealed.kdf_params,
        passphrase="a long passphrase",
    )
    assert open_secret_content(sealed.ciphertext, key) == TEXT


@pytest.mark.parametrize(
    "params",
    ["t=1,m=999999999,p=1", "t=999,m=8192,p=1", "garbage", "t=1,m=8,p=4", "t=1,p=1"],
)
def test_kdf_params_out_of_bounds_are_refused_before_deriving(params):
    """The params decide how much memory one reveal allocates, so they are
    bounded rather than believed - a corrupted row must not OOM the backend."""
    sealed = seal_secret(TEXT, "a long passphrase")
    with pytest.raises(SecretUndecryptableError):
        unwrap_secret_key(
            sealed.key_encrypted,
            kdf_salt=sealed.kdf_salt,
            kdf_params=params,
            passphrase="a long passphrase",
        )
