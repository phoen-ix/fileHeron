"""Encrypt a stored row in place, the way the lanes will - for tests of the
read side, which must not depend on the lanes existing."""
from __future__ import annotations

import os
import tempfile

from app.services import storage_backend as sb
from app.utils import crypto, file_crypto


def store_plain(data: bytes, *, name: str = "plain") -> str:
    """Put `data` into the active backend; return its locator."""
    backend = sb.get_storage_backend()
    locator = backend.generate_locator(f"{name}-{os.urandom(4).hex()}")
    fd, tmp = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    backend.finalize(tmp, locator)
    return locator


def encrypt_row(db, row, *, kind: str = "file", locator_attr: str = "storage_path") -> bytes:
    """Encrypt the row's stored bytes to a sibling locator, swap the row onto
    it, delete the plaintext. Returns the data key."""
    backend = sb.get_storage_backend()
    old = getattr(row, locator_attr)
    row_id = str(row.id)
    dek = file_crypto.new_data_key()
    target = backend.sibling_locator(old, f"{row_id}.{os.urandom(4).hex()}.fhe")
    with backend.open(old) as src:
        backend.write_stream(target, file_crypto.EncryptingReader(src, dek, row.size_bytes))
    setattr(row, locator_attr, target)
    row.enc_version = file_crypto.FORMAT_VERSION
    row.key_encrypted = crypto.wrap_file_key(dek, kind=kind, row_id=row_id)
    db.commit()
    backend.delete(old)
    return dek
