"""The requester's passphrase layer on an answer to a secret request (v2.24.0).

A request keeps only the PUBLIC half of a key pair derived from the requester's
passphrase; the answer is sealed to it, inside the instance layer. These pin
that the stored public key opens nothing, that each layer reports its own
wrong passphrase, and that the instance layer stays outermost (the rotation
script re-wraps it without any passphrase).
"""
from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.utils import crypto
from app.utils.crypto import (
    SecretPassphraseError,
    SecretRequestPassphraseError,
    open_secret_content,
    request_keypair,
    seal_secret,
    unwrap_secret_key,
)

TEXT = "router admin: Hx9#qL2v!"
REQUEST_PW = "requester's own passphrase"
ANSWER_PW = "answerer's passphrase"


def _open(sealed, keys, *, passphrase=None, request_passphrase=None) -> str:
    key = unwrap_secret_key(
        sealed.key_encrypted,
        kdf_salt=sealed.kdf_salt,
        kdf_params=sealed.kdf_params,
        passphrase=passphrase,
        req_kdf_salt=keys.kdf_salt,
        req_kdf_params=keys.kdf_params,
        req_ephemeral_key=sealed.req_ephemeral_key,
        request_passphrase=request_passphrase,
    )
    return open_secret_content(sealed.ciphertext, key)


def test_the_keypair_keeps_only_the_public_half():
    keys = request_keypair(REQUEST_PW)
    assert len(base64.urlsafe_b64decode(keys.public_key)) == 32
    assert REQUEST_PW not in repr(keys)
    # A fresh salt every time: the same passphrase never yields the same key.
    assert request_keypair(REQUEST_PW).public_key != keys.public_key


def test_the_requester_layer_opens_only_with_the_requesters_passphrase():
    keys = request_keypair(REQUEST_PW)
    sealed = seal_secret(TEXT, None, request_public_key=keys.public_key)
    assert sealed.req_ephemeral_key
    assert _open(sealed, keys, request_passphrase=REQUEST_PW) == TEXT
    with pytest.raises(SecretRequestPassphraseError):
        _open(sealed, keys, request_passphrase="not the passphrase")
    with pytest.raises(SecretRequestPassphraseError):
        _open(sealed, keys, request_passphrase=None)


def test_both_layers_each_report_their_own_wrong_passphrase():
    keys = request_keypair(REQUEST_PW)
    sealed = seal_secret(TEXT, ANSWER_PW, request_public_key=keys.public_key)
    assert _open(sealed, keys, passphrase=ANSWER_PW, request_passphrase=REQUEST_PW) == TEXT
    with pytest.raises(SecretRequestPassphraseError):
        _open(sealed, keys, passphrase=ANSWER_PW, request_passphrase="wrong")
    with pytest.raises(SecretPassphraseError):
        _open(sealed, keys, passphrase="wrong", request_passphrase=REQUEST_PW)


def test_the_instance_key_and_the_stored_public_key_open_nothing():
    """A database dump plus the .env: the outer layer opens, and what is under
    it is neither the data key nor anything the public key can unwrap."""
    keys = request_keypair(REQUEST_PW)
    sealed = seal_secret(TEXT, None, request_public_key=keys.public_key)
    inner = crypto._get_fernet().decrypt(sealed.key_encrypted.encode("ascii"))
    with pytest.raises(crypto.SecretUndecryptableError):
        open_secret_content(sealed.ciphertext, inner)
    with pytest.raises(InvalidToken):
        Fernet(base64.urlsafe_b64encode(base64.urlsafe_b64decode(keys.public_key))).decrypt(inner)


def test_the_instance_layer_stays_outermost(monkeypatch):
    """What the rotation script does: re-wrap the outer layer under a new key.
    The answer still opens with the requester's passphrase afterwards."""
    keys = request_keypair(REQUEST_PW)
    sealed = seal_secret(TEXT, None, request_public_key=keys.public_key)
    new = Fernet(Fernet.generate_key())
    rewrapped = new.encrypt(
        crypto._get_fernet().decrypt(sealed.key_encrypted.encode("ascii"))
    ).decode("ascii")
    monkeypatch.setattr(crypto, "_get_fernet", lambda: new)
    key = unwrap_secret_key(
        rewrapped,
        kdf_salt=None,
        kdf_params=None,
        passphrase=None,
        req_kdf_salt=keys.kdf_salt,
        req_kdf_params=keys.kdf_params,
        req_ephemeral_key=sealed.req_ephemeral_key,
        request_passphrase=REQUEST_PW,
    )
    assert open_secret_content(sealed.ciphertext, key) == TEXT


def test_kdf_params_are_bounds_checked_on_the_requester_layer():
    keys = request_keypair(REQUEST_PW)
    sealed = seal_secret(TEXT, None, request_public_key=keys.public_key)
    with pytest.raises(crypto.SecretUndecryptableError):
        unwrap_secret_key(
            sealed.key_encrypted,
            kdf_salt=None,
            kdf_params=None,
            passphrase=None,
            req_kdf_salt=keys.kdf_salt,
            req_kdf_params="t=1,m=99999999,p=1",
            req_ephemeral_key=sealed.req_ephemeral_key,
            request_passphrase=REQUEST_PW,
        )
