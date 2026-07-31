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
  a key HKDF-derived from JWT_SECRET. Rotation: change JWT_SECRET + run
  ``backend/scripts/rotate_jwt_secret.py``, which re-encrypts ALL FIVE Fernet
  columns (TOTP secrets, OIDC client secrets, SMTP/IMAP passwords in
  app_settings, public-link tokens, webhook signing secrets) - not just TOTP.
  The path named here used to be scripts/rotate_totp_key.py, which has never
  existed (audit 2026-07-30).
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
    """SHA-256 of refresh token. Tokens are 64 raw bytes (86 chars b64url) of
    crypto-random data; SHA-256 is sufficient and avoids Argon2 cost on every
    refresh."""
    return sha256_hex(token_plain)


def normalize_email(email: str) -> str:
    """Canonical form for storage + lookup: leading/trailing whitespace
    stripped, ASCII-lowercased local + domain. Always pass user-supplied
    email through this before writing or querying."""
    return email.strip().lower()


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    """Constant-time comparison that tolerates arbitrary attacker input.

    `hmac.compare_digest` raises TypeError("comparing strings with non-ASCII
    characters is not supported") when either str argument is non-ASCII. Every
    token verifier in this codebase compares a computed hex digest against a
    value straight off the wire (a cookie, a `?dt=` query param, an
    Authorization header, tusd metadata), so a single non-ASCII byte turned an
    invalid-token rejection into an unhandled 500 - unauthenticated, on several
    endpoints at once (audit 2026-07-30; same shape as the v2.1.0 public-link
    password fix, which only patched one of them).

    Encoding with errors="replace" cannot itself raise, and a value that needed
    replacing was never going to match a hex digest anyway.
    """
    a_b = a if isinstance(a, bytes) else a.encode("utf-8", "replace")
    b_b = b if isinstance(b, bytes) else b.encode("utf-8", "replace")
    return hmac.compare_digest(a_b, b_b)


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


class SecretUndecryptableError(Exception):
    """A stored Fernet field could not be decrypted under the current key.

    Almost always means JWT_SECRET was rotated without re-encrypting the
    dependent rows (see backend/scripts/rotate_jwt_secret.py). Callers should
    catch this and degrade deliberately: services/settings.py already treats an
    undecryptable setting as missing, and that is the pattern to copy. Letting
    the raw InvalidToken escape turns "one row is unreadable" into an
    unhandled 500 on a login or a permanently wedged worker
    (audit 2026-07-30)."""


def decrypt_totp_secret(ciphertext: bytes) -> str:
    """Raises SecretUndecryptableError rather than InvalidToken so the two
    call sites in services/totp.py can fail cleanly instead of 500ing every
    2FA login."""
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except Exception as e:
        raise SecretUndecryptableError("TOTP secret") from e


# ---------------------------------------------------------------------------
# Passphrase-based encryption for portable config backups.
#
# Independent of JWT_SECRET: the key is scrypt-derived from an admin-supplied
# passphrase + a per-file random salt, so a backup encrypted on one system can
# be restored on another that has a *different* JWT_SECRET. Used only by
# services/config_backup.py to wrap the whole payload when secret_mode is
# "passphrase". The salt + scrypt params travel in the (cleartext) envelope.
# ---------------------------------------------------------------------------

# scrypt cost params for NEW exports. n=2^17 (128 MiB, roughly a second) is the
# current OWASP floor. 2^14 was scrypt's original "interactive login" setting,
# which is the wrong trade for this file: with include_env it carries
# JWT_SECRET, DB_PASSWORD, every users.password_hash and every decrypted TOTP
# secret; it is MEANT to be stored off-site; and it travels with its salt and
# cost params in cleartext, so a leaked copy can be ground offline against a
# 12-character human passphrase. Derivation runs once per export and once per
# import, so the extra second buys an 8x memory cost per guess for no
# user-visible latency. The params live in the envelope, which is exactly what
# that was for - backups written under the old value keep opening - and
# validate_scrypt_params bounds whatever comes back out of one
# (audit 2026-07-30).
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1

# Upper bounds for params read back OUT of a backup envelope. scrypt's memory
# cost is roughly 128 * n * r * p bytes, and those three numbers arrive from the
# (cleartext, attacker-authorable) envelope of a file an admin was handed - a
# documented DR/migration flow. `n=2**30, r=8` asks for ~1 TB and OOM-kills the
# container before the passphrase is even checked (audit 2026-07-30).
#
# The ceiling is on the PRODUCT, not just the individual values, because the
# three multiply. 256 MiB leaves headroom over what this project writes today
# (2**17 * 8 * 1 = 128 MiB) while staying survivable.
_SCRYPT_MAX_MEMORY_BYTES = 256 * 1024 * 1024
_SCRYPT_MAX_N = 2**20
_SCRYPT_MAX_R = 64
_SCRYPT_MAX_P = 16


class ScryptParamsRejectedError(ValueError):
    """Backup envelope asked for KDF parameters outside the safe envelope."""


def validate_scrypt_params(n: int, r: int, p: int) -> tuple[int, int, int]:
    """Bound KDF parameters taken from an untrusted backup envelope."""
    if not all(isinstance(v, int) for v in (n, r, p)):
        raise ScryptParamsRejectedError("scrypt parameters must be integers")
    if n < 2 or (n & (n - 1)) != 0:
        raise ScryptParamsRejectedError("scrypt n must be a power of two")
    if not (1 <= r <= _SCRYPT_MAX_R) or not (1 <= p <= _SCRYPT_MAX_P):
        raise ScryptParamsRejectedError("scrypt r/p out of range")
    if n > _SCRYPT_MAX_N:
        raise ScryptParamsRejectedError("scrypt n out of range")
    if 128 * n * r * p > _SCRYPT_MAX_MEMORY_BYTES:
        raise ScryptParamsRejectedError("scrypt parameters demand too much memory")
    return n, r, p


def new_backup_salt() -> bytes:
    return secrets.token_bytes(16)


def derive_backup_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """scrypt(passphrase, salt) -> 32 bytes -> urlsafe base64 -> Fernet key.

    Parameters are bounded here rather than at the call site: this is the only
    place they are consumed, so a future caller cannot forget."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    n, r, p = validate_scrypt_params(n, r, p)
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    derived = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def encrypt_with_passphrase(plaintext: bytes, passphrase: str, salt: bytes) -> str:
    key = derive_backup_key(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return Fernet(key).encrypt(plaintext).decode("ascii")


def decrypt_with_passphrase(
    token: str, passphrase: str, salt: bytes, *, n: int, r: int, p: int
) -> bytes:
    """Raises cryptography.fernet.InvalidToken on a wrong passphrase / tampered
    token - the caller maps that to BACKUP_BAD_PASSPHRASE."""
    key = derive_backup_key(passphrase, salt, n=n, r=r, p=p)
    return Fernet(key).decrypt(token.encode("ascii"))


# Generic alias for the Phase 9 app_settings table. Same Fernet key, same
# crypto - separate names so callsites read self-documenting.
def encrypt_setting(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_setting(ciphertext: str) -> str:
    """Raises SecretUndecryptableError rather than InvalidToken. An empty
    string is a legitimate "unset" value in several models
    (Webhook.secret_encrypted is `nullable=False, default=""`), so it is
    reported the same way rather than blowing up deeper in the caller."""
    if not ciphertext:
        raise SecretUndecryptableError("setting (empty)")
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception as e:
        raise SecretUndecryptableError("setting") from e


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
