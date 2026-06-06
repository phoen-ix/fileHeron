"""Crypto primitives. All security-relevant operations live here so they can
be audited in one place and unit-tested.

- argon2_hash / argon2_verify: passwords, recovery codes (Phase 1b).
  NOT used for refresh tokens (those use SHA-256 - see refresh_token_hash).
- random_token(n): urlsafe base64-encoded random bytes.
- sha256_hex: deterministic hash of high-entropy strings (refresh tokens, public
  link tokens - Phase 5).
- normalize_email: lower + strip; use this every time you write or
  query against ``users.email`` / ``invite_tokens.email``.
- hmac_sign(payload, secret): used for tusd metadata signing (Phase 3a).
- encrypt_totp_secret / decrypt_totp_secret: Fernet (AES-128 CBC + HMAC) under
  a key HKDF-derived from JWT_SECRET. Rotation: change JWT_SECRET + run a one-
  shot re-encrypt script (Phase 1b's TOTP secrets are stored encrypted at rest;
  rotation script lives at scripts/rotate_totp_key.py).
- new_recovery_code(): 8-char alphanumeric recovery code, "K7XQ-2L9P" style.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import settings


def _build_hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.ARGON2_TIME_COST,
        memory_cost=settings.ARGON2_MEMORY_COST_KIB,
        parallelism=settings.ARGON2_PARALLELISM,
    )


_hasher = _build_hasher()


def argon2_hash(plaintext: str) -> str:
    """Hash a password (or other low-entropy secret) with Argon2id."""
    return _hasher.hash(plaintext)


def argon2_verify(hash_str: str, plaintext: str) -> bool:
    try:
        return _hasher.verify(hash_str, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def random_token(num_bytes: int = 32) -> str:
    """URL-safe random token. Default 32 bytes → 43-character base64url string."""
    return base64.urlsafe_b64encode(secrets.token_bytes(num_bytes)).rstrip(b"=").decode("ascii")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def refresh_token_hash(token_plain: str) -> str:
    """SHA-256 of refresh token. Tokens are 64 raw bytes (43 chars b64url) of
    crypto-random data; SHA-256 is sufficient and avoids Argon2 cost on every
    refresh."""
    return sha256_hex(token_plain)


def normalize_email(email: str) -> str:
    """Canonical form for storage + lookup: leading/trailing whitespace
    stripped, ASCII-lowercased local + domain. Always pass user-supplied
    email through this before writing or querying."""
    return email.strip().lower()


def hmac_sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# TOTP secret encryption (Fernet under HKDF-derived key from JWT_SECRET)
# ---------------------------------------------------------------------------

_FERNET_HKDF_INFO = b"fileheron-totp-secret-key-v1"
_fernet_instance: Fernet | None = None


def _derive_fernet_key(jwt_secret: str) -> bytes:
    """HKDF(SHA-256) → 32 bytes → urlsafe base64 → Fernet key."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_FERNET_HKDF_INFO,
    )
    derived = hkdf.derive(jwt_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(_derive_fernet_key(settings.JWT_SECRET))
    return _fernet_instance


def encrypt_totp_secret(plaintext_b32: str) -> bytes:
    return _get_fernet().encrypt(plaintext_b32.encode("utf-8"))


def decrypt_totp_secret(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(ciphertext).decode("utf-8")


# Generic alias for the Phase 9 app_settings table. Same Fernet key, same
# crypto - separate names so callsites read self-documenting.
def encrypt_setting(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_setting(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Recovery codes (10 × 8-char alphanumeric, "K7XQ-2L9P" style)
# ---------------------------------------------------------------------------

# Crockford-ish alphabet - drop ambiguous chars (0/O, 1/I/L) so users can read
# codes from a printed page without confusion.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_recovery_code() -> str:
    """Single 8-char recovery code formatted as XXXX-XXXX."""
    chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
    return f"{chars[:4]}-{chars[4:]}"


def generate_recovery_codes(count: int = 10) -> list[str]:
    return [new_recovery_code() for _ in range(count)]
