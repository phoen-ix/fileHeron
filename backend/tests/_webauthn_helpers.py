"""A software passkey for the WebAuthn tests: a real P-256 key and real
signatures, so py_webauthn's own checks run - signature, challenge, origin, RP
id hash, the user-presence and user-verification flags - instead of being
monkeypatched away. Only the browser is missing, and these dicts are what it
would send.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from types import EllipsisType

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import settings
from app.models.user_webauthn_credential import UserWebAuthnCredential
from app.services import webauthn as webauthn_svc

_UP, _UV, _AT = 0x01, 0x04, 0x40


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def install_challenge_store(monkeypatch) -> dict[str, str]:
    """Stand in for the Redis challenge store, one dict per test. `get` is there
    so a regression from GETDEL to GET replays instead of crashing."""
    store: dict[str, str] = {}

    class _Store:
        async def set(self, key, value, ex=None):
            store[key] = value

        async def get(self, key):
            return store.get(key)

        async def getdel(self, key):
            return store.pop(key, None)

        async def aclose(self):
            return None

    monkeypatch.setattr(webauthn_svc, "_redis", lambda: _Store())
    return store


class SoftPasskey:
    """One credential on one authenticator. `user_handle` is what the
    authenticator stored at registration - the account's id, as file:Heron
    registers it."""

    def __init__(self, user_id: int, *, credential_id: bytes | None = None) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = credential_id or os.urandom(16)
        self.user_handle = str(user_id).encode("utf-8")
        self.sign_count = 0
        self.rp_id = settings.WEBAUTHN_RP_ID
        self.origin = webauthn_svc._origins()[0]

    def cose_public_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def store(self, db, user_id: int, *, name: str = "soft key") -> UserWebAuthnCredential:
        """Insert the credential row directly, as a past registration left it."""
        row = UserWebAuthnCredential(
            user_id=user_id,
            credential_id=self.credential_id,
            public_key=self.cose_public_key(),
            sign_count=0,
            transports="internal",
            name=name,
        )
        db.add(row)
        db.commit()
        return row

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode("utf-8")

    def attestation(self, options: dict, *, uv: bool = True, rk: bool | None = True) -> dict:
        """What navigator.credentials.create() returns for these options - a
        `none` attestation, and `credProps.rk` as the browser would report it
        (None: the browser reported nothing)."""
        flags = _UP | _AT | (_UV if uv else 0)
        attested = (
            bytes(16)
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.cose_public_key()
        )
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
            + attested
        )
        out: dict = {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"])),
                "attestationObject": b64url(
                    cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
                ),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }
        if rk is not None:
            out["clientExtensionResults"] = {"credProps": {"rk": rk}}
        return out

    def assertion(
        self,
        options: dict,
        *,
        uv: bool = True,
        user_handle: bytes | None | EllipsisType = ...,
        signer: SoftPasskey | None = None,
    ) -> dict:
        """What navigator.credentials.get() returns for these options.
        `user_handle=None` omits it; `signer` signs with another key (a forged
        assertion for this credential id)."""
        self.sign_count += 1
        flags = _UP | (_UV if uv else 0)
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
        )
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = (signer or self).key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        response: dict = {
            "clientDataJSON": b64url(client_data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
        }
        handle = self.user_handle if user_handle is ... else user_handle
        if handle is not None:
            response["userHandle"] = b64url(handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
