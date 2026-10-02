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
  ``backend/scripts/rotate_jwt_secret.py``, which re-encrypts EVERY Fernet
  column (TOTP secrets, OIDC client secrets, SMTP/IMAP passwords in
  app_settings, public-link tokens, webhook signing secrets, secret keys and
  secret links) - not just TOTP. A test fails when a `*_encrypted` column is
  added that the script does not rotate.
  The path named here used to be scripts/rotate_totp_key.py, which has never
  existed (audit 2026-07-30).
- new_recovery_code(): 8-char alphanumeric recovery code, "K7XQ-2L9P" style.
- seal_secret / unwrap_secret_key / open_secret_content: the Secrets feature
  (v2.24.0). A fresh key per secret, optionally under an Argon2id key derived
  from the sender's passphrase, wrapped by the same instance Fernet key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
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


# ---------------------------------------------------------------------------
# Secrets (v2.24.0)
#
#   ciphertext    = Fernet(data_key).encrypt(text)
#   key_encrypted = instance_fernet.encrypt(data_key)                 no passphrase
#   key_encrypted = instance_fernet.encrypt(Fernet(kek).encrypt(dk))  passphrase
#   kek           = Argon2id(passphrase, salt)
#
# The instance layer is OUTERMOST, so backend/scripts/rotate_jwt_secret.py can
# re-wrap every secret without knowing any passphrase. The passphrase layer is
# what makes a passphrase more than a gate: without it, a database dump plus the
# .env opens nothing. A wrong passphrase is Fernet's InvalidToken on the middle
# layer - its HMAC is the verifier, so no separate hash of the passphrase exists.
#
# An ANSWER to a secret request may carry a second passphrase layer, the
# requester's, between the instance layer and the answerer's:
#
#   key_encrypted = instance_fernet.encrypt(Fernet(req_kek).encrypt(inner))
#   req_kek       = HKDF(X25519(ephemeral, requester_public))
#   requester key = X25519 from Argon2id(requester passphrase, salt)
#
# The request stores only the PUBLIC key (and salt + params), so the answer can
# be sealed to it while nobody - the server included - holds anything that
# opens it until the requester types the passphrase again at reveal.
# ---------------------------------------------------------------------------

# Bounds for KDF parameters read back from a row. The database is trusted, but
# these values decide how much memory one reveal allocates, so they are checked
# rather than believed - the same reasoning as validate_scrypt_params.
_SECRET_KDF_MAX_TIME = 20
_SECRET_KDF_MAX_MEMORY_KIB = 1024 * 1024
_SECRET_KDF_MAX_PARALLELISM = 16


class SecretPassphraseError(Exception):
    """The passphrase layer did not open: the passphrase is wrong or missing."""


class SecretRequestPassphraseError(Exception):
    """The requester's passphrase layer did not open: wrong or missing."""


@dataclass(frozen=True)
class SealedSecret:
    ciphertext: str
    key_encrypted: str
    kdf_salt: str | None
    kdf_params: str | None
    # The ephemeral X25519 public key of the requester's layer, when there is one.
    req_ephemeral_key: str | None = None


@dataclass(frozen=True)
class RequestKeypair:
    """What a secret request keeps of the requester's passphrase: salt, KDF
    params and the PUBLIC key. Nothing here opens an answer."""

    kdf_salt: str
    kdf_params: str
    public_key: str


_REQUEST_KEK_INFO = b"fileheron-secret-request"


def _argon2_raw(passphrase: str, salt: bytes, *, t: int, m: int, p: int) -> bytes:
    from argon2.low_level import Type, hash_secret_raw

    return hash_secret_raw(
        passphrase.encode("utf-8"),
        salt,
        time_cost=t,
        memory_cost=m,
        parallelism=p,
        hash_len=32,
        type=Type.ID,
    )


def _secret_kek(passphrase: str, salt: bytes, *, t: int, m: int, p: int) -> bytes:
    return base64.urlsafe_b64encode(_argon2_raw(passphrase, salt, t=t, m=m, p=p))


def _current_kdf_params() -> tuple[int, int, int]:
    return settings.ARGON2_TIME_COST, settings.ARGON2_MEMORY_COST_KIB, settings.ARGON2_PARALLELISM


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as e:
        raise SecretUndecryptableError("secret request key") from e
    if len(raw) != 32:
        raise SecretUndecryptableError("secret request key length")
    return raw


def _request_kek(shared: bytes, ephemeral_pub: bytes, requester_pub: bytes) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ephemeral_pub + requester_pub,
        info=_REQUEST_KEK_INFO,
    ).derive(shared)
    return base64.urlsafe_b64encode(raw)


def request_keypair(passphrase: str) -> RequestKeypair:
    """Derive the requester's X25519 key pair from their passphrase and keep the
    public half. The private key exists only for the length of this call."""
    salt = secrets.token_bytes(16)
    t, m, p = _current_kdf_params()
    private = X25519PrivateKey.from_private_bytes(_argon2_raw(passphrase, salt, t=t, m=m, p=p))
    return RequestKeypair(
        kdf_salt=salt.hex(),
        kdf_params=f"t={t},m={m},p={p}",
        public_key=_b64(private.public_key().public_bytes_raw()),
    )


def _parse_secret_kdf_params(params: str) -> tuple[int, int, int]:
    try:
        fields = dict(part.split("=", 1) for part in params.split(","))
        t, m, p = int(fields["t"]), int(fields["m"]), int(fields["p"])
    except (KeyError, ValueError) as e:
        raise SecretUndecryptableError("secret kdf params") from e
    if not (
        1 <= t <= _SECRET_KDF_MAX_TIME
        and 1 <= p <= _SECRET_KDF_MAX_PARALLELISM
        and 8 * p <= m <= _SECRET_KDF_MAX_MEMORY_KIB
    ):
        raise SecretUndecryptableError("secret kdf params out of range")
    return t, m, p


def seal_secret(
    plaintext: str,
    passphrase: str | None,
    *,
    request_public_key: str | None = None,
) -> SealedSecret:
    """Encrypt a secret's text under a fresh key, wrapped as described above:
    the passphrase layer (if any), then the requester's layer (an answer to a
    request with a passphrase), then the instance layer."""
    data_key = Fernet.generate_key()
    ciphertext = Fernet(data_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    wrapped = data_key
    salt_hex: str | None = None
    params: str | None = None
    if passphrase:
        salt = secrets.token_bytes(16)
        t, m, p = _current_kdf_params()
        wrapped = Fernet(_secret_kek(passphrase, salt, t=t, m=m, p=p)).encrypt(data_key)
        salt_hex = salt.hex()
        params = f"t={t},m={m},p={p}"
    ephemeral_b64: str | None = None
    if request_public_key:
        requester_pub = _unb64_key(request_public_key)
        ephemeral = X25519PrivateKey.generate()
        ephemeral_pub = ephemeral.public_key().public_bytes_raw()
        shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(requester_pub))
        wrapped = Fernet(_request_kek(shared, ephemeral_pub, requester_pub)).encrypt(wrapped)
        ephemeral_b64 = _b64(ephemeral_pub)
    key_encrypted = _get_fernet().encrypt(wrapped).decode("ascii")
    return SealedSecret(ciphertext, key_encrypted, salt_hex, params, ephemeral_b64)


def _open_request_layer(
    inner: bytes,
    *,
    req_kdf_salt: str | None,
    req_kdf_params: str | None,
    req_ephemeral_key: str,
    request_passphrase: str | None,
) -> bytes:
    if not request_passphrase or not req_kdf_salt or not req_kdf_params:
        raise SecretRequestPassphraseError()
    t, m, p = _parse_secret_kdf_params(req_kdf_params)
    try:
        salt = bytes.fromhex(req_kdf_salt)
    except ValueError as e:
        raise SecretUndecryptableError("secret request kdf salt") from e
    ephemeral_pub = _unb64_key(req_ephemeral_key)
    private = X25519PrivateKey.from_private_bytes(
        _argon2_raw(request_passphrase, salt, t=t, m=m, p=p)
    )
    shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub))
    kek = _request_kek(shared, ephemeral_pub, private.public_key().public_bytes_raw())
    try:
        return Fernet(kek).decrypt(inner)
    except InvalidToken as e:
        # A wrong passphrase derives a different key pair, so the ECDH secret
        # and the KEK differ and the Fernet HMAC refuses - no separate verifier.
        raise SecretRequestPassphraseError() from e


def unwrap_secret_key(
    key_encrypted: str,
    *,
    kdf_salt: str | None,
    kdf_params: str | None,
    passphrase: str | None,
    req_kdf_salt: str | None = None,
    req_kdf_params: str | None = None,
    req_ephemeral_key: str | None = None,
    request_passphrase: str | None = None,
) -> bytes:
    """The secret's data key. Raises SecretRequestPassphraseError when the
    requester's layer does not open, SecretPassphraseError for the answerer's
    or sender's passphrase (the caller counts both), SecretUndecryptableError
    when the instance layer does not open (JWT_SECRET rotated without the
    rotation script)."""
    try:
        inner = _get_fernet().decrypt(key_encrypted.encode("ascii"))
    except Exception as e:
        raise SecretUndecryptableError("secret key") from e
    if req_ephemeral_key:
        inner = _open_request_layer(
            inner,
            req_kdf_salt=req_kdf_salt,
            req_kdf_params=req_kdf_params,
            req_ephemeral_key=req_ephemeral_key,
            request_passphrase=request_passphrase,
        )
    if not kdf_salt:
        return inner
    if not passphrase or not kdf_params:
        raise SecretPassphraseError()
    t, m, p = _parse_secret_kdf_params(kdf_params)
    try:
        salt = bytes.fromhex(kdf_salt)
    except ValueError as e:
        raise SecretUndecryptableError("secret kdf salt") from e
    try:
        return Fernet(_secret_kek(passphrase, salt, t=t, m=m, p=p)).decrypt(inner)
    except InvalidToken as e:
        raise SecretPassphraseError() from e


def open_secret_content(ciphertext: str, data_key: bytes) -> str:
    try:
        return Fernet(data_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception as e:
        raise SecretUndecryptableError("secret content") from e
