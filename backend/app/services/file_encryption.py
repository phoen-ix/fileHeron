"""Encryption at rest for stored files - the read side.

Opt-in (`storage.encrypt_at_rest`, off by default). A row's `enc_version` says
how its bytes are stored: NULL is plaintext - every row on an instance that
never switches this on - and every reader takes exactly the path it always
took. Otherwise the bytes are utils/file_crypto format v1 under the row's own
data key, which `key_encrypted` holds wrapped by the instance key with the row
bound inside.

The rule that keeps the two kinds of row apart: NOTHING reads stored bytes for
a person or a scanner except through this module or the plaintext branch of a
reader that checked `enc_version` first. Ciphertext handed to clamd's path scan
would scan "clean" and switch antivirus off without a sound; handed to a
download it would serve the user noise. `tests/test_ciphertext_never_reaches_
plaintext_readers.py` pins the call sites.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, cast

from ..middleware.errors import AppError
from ..utils import crypto, file_crypto

if TYPE_CHECKING:
    from ..models.file import File
    from ..models.inbound_attachment import InboundAttachment
    from .storage_backend import StorageBackend

logger = logging.getLogger("fileheron.file_encryption")

KIND_FILE = "file"
KIND_ATTACHMENT = "inbound_attachment"


@dataclass(frozen=True)
class StoredCipher:
    """Everything a reader needs about one encrypted row, resolved up front -
    a streamed response must never touch a detached ORM object."""

    version: int
    dek: bytes
    plaintext_size: int
    # Strong, stable per row: the plaintext of a row never changes, and this
    # does not depend on the ciphertext, so a later re-encryption keeps it.
    etag: str
    last_modified: datetime | None


def _etag(kind: str, row_id: str, size: int) -> str:
    digest = hashlib.sha256(f"{kind}:{row_id}:{size}".encode()).hexdigest()[:32]
    return f'"fhe-{digest}"'


def _unreadable(kind: str, row_id: str, why: str) -> AppError:
    logger.error("encrypted %s %s is unreadable: %s", kind, row_id, why)
    return AppError(500, "FILE_UNREADABLE", "This file cannot be read.")


def _cipher(
    kind: str, row_id: str, version: int | None, wrapped: str | None, size: int,
    last_modified: datetime | None,
) -> StoredCipher | None:
    if version is None:
        return None
    if version != file_crypto.FORMAT_VERSION or not wrapped:
        raise _unreadable(kind, row_id, f"format {version}, key present: {bool(wrapped)}")
    try:
        dek = crypto.unwrap_file_key(wrapped, kind=kind, row_id=row_id)
    except crypto.SecretUndecryptableError as e:
        # The instance key changed without rotate_jwt_secret.py, or the key is
        # on the wrong row. Either way there is nothing to serve.
        raise _unreadable(kind, row_id, type(e).__name__) from e
    return StoredCipher(
        version=version,
        dek=dek,
        plaintext_size=size,
        etag=_etag(kind, row_id, size),
        last_modified=last_modified,
    )


def cipher_for_file(f: File) -> StoredCipher | None:
    """None for a plaintext file; raises FILE_UNREADABLE when it cannot be read."""
    return _cipher(KIND_FILE, f.id, f.enc_version, f.key_encrypted, f.size_bytes, f.finalized_at)


def cipher_for_attachment(a: InboundAttachment) -> StoredCipher | None:
    return _cipher(
        KIND_ATTACHMENT, str(a.id), a.enc_version, a.key_encrypted, a.size_bytes, None
    )


def opener(backend: StorageBackend, locator: str) -> file_crypto.Opener:
    return lambda start, end_incl: backend.open_range(locator, start, end_incl)


def open_plaintext(backend: StorageBackend, locator: str, cipher: StoredCipher | None) -> BinaryIO:
    """A readable stream of the file's PLAINTEXT, whichever way it is stored."""
    if cipher is None:
        return backend.open(locator)
    return cast(
        "BinaryIO",
        file_crypto.DecryptingReader(opener(backend, locator), cipher.dek, cipher.plaintext_size),
    )
