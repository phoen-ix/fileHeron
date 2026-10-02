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
import secrets as _secrets
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.storage_purge import PURGE_ENC_PLAINTEXT, PURGE_ENC_TARGET, StoragePurge
from ..utils import crypto, file_crypto
from ..utils.dbresult import updated_rows
from ..utils.timeutil import utc_now

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


# ---------------------------------------------------------------------------
# The write side: moving a stored file between plaintext and ciphertext
# ---------------------------------------------------------------------------
#
# A stored file is never rewritten in place. Its other form is written to a NEW
# locator beside it, the row is swapped onto that locator by a conditional
# UPDATE (`storage_path` still the old one, `enc_version` still the old state),
# and the old bytes are queued for deletion after the commit - so a crash at any
# point leaves either the old row and some debris, or the new row and the old
# bytes queued; never a row pointing at missing or half-written bytes.
#
#   prepare_rewrite  lease row COMMITTED before the first byte (a crash leaves
#                    debris the sweep can find), write, verify the size
#   <caller's conditional UPDATE, same transaction as finish_rewrite>
#   finish_rewrite   swapped: drop the lease, queue the OLD bytes;
#                    lost the race: make the lease due now (our copy is debris)
#   sweep_purges     delete what is due; a failure is retried and audited

# How long a lease survives without a heartbeat before its target counts as
# debris. The writer bumps it every _LEASE_HEARTBEAT_SEC while it writes.
LEASE_TTL = timedelta(minutes=15)
_LEASE_HEARTBEAT_SEC = 60
# Replaced bytes of a file that was being SERVED (the backfill): a download or
# ZIP stream that opened the old locator before the swap can still finish.
PLAINTEXT_PURGE_GRACE = timedelta(hours=1)
_PURGE_RETRY = timedelta(minutes=30)


class InsufficientSpaceError(file_crypto.FileCryptoError):
    """Not enough free space to hold the second copy a rewrite needs."""


@dataclass(frozen=True)
class Target:
    """A stored row, either kind, as the engine needs it."""

    kind: str
    row_id: str
    model: Any
    pk: Any
    locator_col: Any
    locator: str
    size: int
    enc_version: int | None
    key_encrypted: str | None


def target_for_file(f: File) -> Target:
    from ..models.file import File as FileModel

    if not f.storage_path:
        raise ValueError(f"file {f.id} has no stored bytes")
    return Target(KIND_FILE, f.id, FileModel, f.id, FileModel.storage_path, f.storage_path,
                  f.size_bytes, f.enc_version, f.key_encrypted)


def target_for_attachment(a: InboundAttachment) -> Target:
    from ..models.inbound_attachment import InboundAttachment as AttModel

    return Target(KIND_ATTACHMENT, str(a.id), AttModel, a.id, AttModel.storage_key, a.storage_key,
                  a.size_bytes, a.enc_version, a.key_encrypted)


@dataclass(frozen=True)
class Prepared:
    target: Target
    new_locator: str
    # Columns the swap sets: the new locator plus enc_version/key_encrypted.
    values: dict[str, Any]
    lease_id: int
    crc32: int | None


def check_space(db: Session, backend: StorageBackend, needed: int) -> None:
    """Refuse a rewrite that would push free space under the low-space
    threshold. Object stores have no free-space reading."""
    if not backend.supports_disk_stats:
        return
    from ..config import settings
    from . import settings as settings_svc
    from . import settings_registry, storage

    if settings_svc.get_bool(db, settings_svc.Keys.STORAGE_CRITICAL_LOW, default=False):
        raise InsufficientSpaceError("storage is critically low")
    stats = storage.get_disk_stats(settings.STORAGE_ROOT)
    if "error" in stats:
        return  # fail open, as the upload gate does
    floor = settings_registry.effective(db, settings_svc.Keys.STORAGE_LOW_THRESHOLD_BYTES)
    if stats["free_bytes"] - needed < floor:
        raise InsufficientSpaceError(
            f"{needed} bytes would leave {stats['free_bytes'] - needed}, under the {floor} floor"
        )


class _Heartbeat:
    """Keeps a lease alive while a long write runs, from its own session."""

    def __init__(self, lease_id: int) -> None:
        self._lease_id = lease_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-{lease_id}", daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        from ..database import SessionLocal

        while not self._stop.wait(_LEASE_HEARTBEAT_SEC):
            db = SessionLocal()
            try:
                db.execute(
                    sa_update(StoragePurge)
                    .where(StoragePurge.id == self._lease_id)
                    .values(not_before=utc_now() + LEASE_TTL)
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("lease heartbeat failed for %s", self._lease_id, exc_info=True)
            finally:
                db.close()


class _Counting:
    def __init__(self, src) -> None:
        self._src = src
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self._src.read(size)
        self.count += len(data)
        return data


def prepare_rewrite(
    db: Session, target: Target, *, encrypt: bool, cancel: threading.Event | None = None
) -> Prepared:
    """Write the row's bytes in their OTHER form to a fresh locator beside
    them. Commits the lease first; on any failure makes it due and re-raises."""
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    if encrypt and target.enc_version is not None:
        raise ValueError(f"{target.kind} {target.row_id} is already encrypted")
    if not encrypt and target.enc_version is None:
        raise ValueError(f"{target.kind} {target.row_id} is not encrypted")
    out_size = file_crypto.ciphertext_size(target.size) if encrypt else target.size
    check_space(db, backend, out_size)

    suffix = "fhe" if encrypt else "bin"
    new_locator = backend.sibling_locator(
        target.locator, f"{target.row_id}.{_secrets.token_hex(4)}.{suffix}"
    )
    lease = StoragePurge(locator=new_locator, reason=PURGE_ENC_TARGET, ref=target.row_id,
                         not_before=utc_now() + LEASE_TTL)
    db.add(lease)
    db.commit()
    lease_id = lease.id

    try:
        with _Heartbeat(lease_id):
            if encrypt:
                dek = file_crypto.new_data_key()
                with backend.open(target.locator) as src:
                    reader = file_crypto.EncryptingReader(src, dek, target.size, cancel=cancel)
                    backend.write_stream(new_locator, reader)
                crc: int | None = reader.crc32
                values: dict[str, Any] = {
                    target.locator_col.key: new_locator,
                    "enc_version": file_crypto.FORMAT_VERSION,
                    "key_encrypted": crypto.wrap_file_key(dek, kind=target.kind, row_id=target.row_id),
                }
            else:
                cipher = _cipher(target.kind, target.row_id, target.enc_version,
                                 target.key_encrypted, target.size, None)
                counting = _Counting(open_plaintext(backend, target.locator, cipher))
                backend.write_stream(new_locator, counting)
                if counting.count != target.size:
                    raise file_crypto.FileIntegrityError(
                        f"decrypted {counting.count} bytes, expected {target.size}"
                    )
                crc = None
                values = {target.locator_col.key: new_locator, "enc_version": None, "key_encrypted": None}
        written = backend.size(new_locator)
        if written != out_size:
            raise file_crypto.FileIntegrityError(f"wrote {written} bytes, expected {out_size}")
    except BaseException:
        db.rollback()
        _make_due(db, lease_id)
        raise
    return Prepared(target, new_locator, values, lease_id, crc)


def _make_due(db: Session, lease_id: int) -> None:
    try:
        db.execute(sa_update(StoragePurge).where(StoragePurge.id == lease_id).values(not_before=utc_now()))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("could not release lease %s; it expires on its own", lease_id, exc_info=True)


def finish_rewrite(db: Session, prepared: Prepared, *, swapped: bool, purge_after: timedelta) -> None:
    """Settle the lease in the caller's transaction (the caller commits). Swapped:
    the lease goes and the OLD bytes are queued. Not swapped - another writer
    moved the row first - our copy is debris, due now."""
    if swapped:
        db.execute(sa_delete(StoragePurge).where(StoragePurge.id == prepared.lease_id))
        db.add(StoragePurge(locator=prepared.target.locator, reason=PURGE_ENC_PLAINTEXT,
                            ref=prepared.target.row_id, not_before=utc_now() + purge_after))
    else:
        db.execute(
            sa_update(StoragePurge).where(StoragePurge.id == prepared.lease_id).values(not_before=utc_now())
        )


def rewrite_stored(
    db: Session,
    target: Target,
    *,
    encrypt: bool,
    purge_after: timedelta,
    expected_states: Sequence[Any] | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    """Prepare, swap and settle in one call. Returns False when the row had
    moved underneath (deleted, quarantined, already rewritten): nothing changed
    except that our copy is queued as debris."""
    prepared = prepare_rewrite(db, target, encrypt=encrypt, cancel=cancel)
    model = target.model
    stmt = (
        sa_update(model)
        .where(
            model.id == target.pk,
            target.locator_col == target.locator,
            model.enc_version.is_(None) if encrypt else model.enc_version.isnot(None),
        )
        .values(**prepared.values)
    )
    if expected_states is not None:
        stmt = stmt.where(model.state.in_(list(expected_states)))
    swapped = updated_rows(db.execute(stmt)) == 1
    finish_rewrite(db, prepared, swapped=swapped, purge_after=purge_after)
    db.commit()
    sweep_purges(db)
    return swapped


def sweep_purges(db: Session, *, limit: int = 200) -> dict[str, int]:
    """Delete stored bytes whose purge is due. A failure is retried later and,
    the first time, recorded as `file_purge_failed` - by now no row names these
    bytes, so nothing else could ever find them again."""
    from .file import record_orphan_locator
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    now = utc_now()
    due = (
        db.query(StoragePurge)
        .filter(StoragePurge.not_before <= now)
        .order_by(StoragePurge.not_before, StoragePurge.id)
        .limit(limit)
        .all()
    )
    purged = failed = 0
    for row in due:
        try:
            backend.delete(row.locator)
        except Exception:
            failed += 1
            row.attempts += 1
            row.not_before = now + _PURGE_RETRY
            first = row.attempts == 1
            locator, reason = row.locator, f"encryption:{row.reason}"
            db.commit()
            if first:
                record_orphan_locator(db, locator=locator, reason=reason)
            continue
        db.delete(row)
        db.commit()
        purged += 1
    return {"purged": purged, "failed": failed}


# ---------------------------------------------------------------------------
# The switch and the admin status
# ---------------------------------------------------------------------------


def write_new_encrypted(backend: StorageBackend, locator: str, data: bytes, *, kind: str,
                        row_id: str) -> dict[str, Any]:
    """Store bytes that are encrypted from the first write (an inbound mail
    attachment, which is scanned in memory and never needs a plaintext copy).
    Returns the row's `enc_version` / `key_encrypted`."""
    import io

    dek = file_crypto.new_data_key()
    backend.write_stream(locator, file_crypto.EncryptingReader(io.BytesIO(data), dek, len(data)))
    return {
        "enc_version": file_crypto.FORMAT_VERSION,
        "key_encrypted": crypto.wrap_file_key(dek, kind=kind, row_id=row_id),
    }


def is_enabled(db: Session) -> bool:
    from . import settings as settings_svc

    return settings_svc.get_bool(db, settings_svc.Keys.STORAGE_ENCRYPT_AT_REST, default=False)


def status(db: Session) -> dict[str, Any]:
    """Counts for the admin page: a handful of counts, one sum, two settings
    reads and one Redis read."""
    import json

    from sqlalchemy import func

    from ..models.file import File as FileModel
    from ..models.file import FileState
    from ..models.inbound_attachment import InboundAttachment as AttModel
    from . import settings as settings_svc
    from .storage_backend import get_storage_backend

    stored = (FileState.clean, FileState.infected)
    enc_files = db.query(func.count(FileModel.id)).filter(
        FileModel.enc_version.isnot(None), FileModel.state.in_(stored)
    ).scalar() or 0
    plain = db.query(func.count(FileModel.id), func.coalesce(func.sum(FileModel.size_bytes), 0)).filter(
        FileModel.enc_version.is_(None), FileModel.state.in_(stored), FileModel.storage_path.isnot(None)
    ).one()
    awaiting = db.query(func.count(FileModel.id)).filter(
        FileModel.state == FileState.ready_unscanned, FileModel.release_verdict.isnot(None)
    ).scalar() or 0
    att = dict(
        db.query(AttModel.enc_version.isnot(None), func.count(AttModel.id))
        .filter(AttModel.storage_key.isnot(None))
        .group_by(AttModel.enc_version.isnot(None))
        .all()
    )
    from . import cron_schedule, encryption_lanes

    raw_last = settings_svc.get(db, settings_svc.Keys.STORAGE_ENCRYPT_LAST_RUN)
    try:
        parsed = json.loads(raw_last) if raw_last else None
    except ValueError:
        parsed = None
    # Read field by field: the summary is this instance's own history, and one
    # written by another release must not 500 the page.
    last_run = None
    if isinstance(parsed, dict) and isinstance(parsed.get("finished_at"), str):
        last_run = {"finished_at": parsed["finished_at"], "stopped": parsed.get("stopped")
                    if isinstance(parsed.get("stopped"), str) else None}
        for k in ("encrypted", "failed", "deferred", "skipped", "remaining"):
            v = parsed.get(k)
            last_run[k] = v if isinstance(v, int) else 0
    deferred = encryption_lanes.deferred_members()
    return {
        "enabled": is_enabled(db),
        "backend": get_storage_backend().name,
        "files": {
            "encrypted": int(enc_files),
            "plaintext": int(plain[0] or 0),
            "plaintext_bytes": int(plain[1] or 0),
            "awaiting_encryption": int(awaiting),
        },
        "inbound_attachments": {
            "encrypted": int(att.get(True, 0)),
            "plaintext": int(att.get(False, 0)),
        },
        "pending_purges": db.query(func.count(StoragePurge.id)).scalar() or 0,
        "failed_purges": db.query(func.count(StoragePurge.id)).filter(StoragePurge.attempts > 0).scalar() or 0,
        "last_run": last_run,
        "deferred": None if deferred is None else len(deferred),
        "backfill_task_enabled": cron_schedule.effective(db, encryption_lanes.BACKFILL_JOB).enabled,
    }
