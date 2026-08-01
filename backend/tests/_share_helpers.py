"""Shared setup for tests that assert on a share's recipient announcement.

A share is created EMPTY - files attach at upload time, so every client (SPA,
desktop, API) posts the share first and uploads into it afterwards. The
`share_created` announcement is therefore deferred until the uploads land
(`services/share.announce_if_ready`); before audit #2 it fired at create time
and told every recipient "shared 0 files with you".

Tests that want to observe the announcement have to reproduce that second half.
`land_file` is that second half.
"""
from __future__ import annotations

import uuid

from app.models.file import File, FileState
from app.services import share as share_svc


def land_file(db, share, uploader, *, name="doc.bin", size=10) -> File:
    """Attach one finalized file to `share` exactly as the upload pipeline
    would, without announcing."""
    f = File(
        id=str(uuid.uuid4()),
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=size,
        storage_path=f"/tmp/{uuid.uuid4()}.bin",
        state=FileState.ready_unscanned,
        uploaded_by_id=uploader.id,
    )
    db.add(f)
    db.flush()
    return f


def land_file_and_announce(db, share, uploader, **kw) -> File:
    """Attach a file and fire the deferred announcement, i.e. what the tus
    post-finish hook and the direct-upload route do."""
    f = land_file(db, share, uploader, **kw)
    share_svc.announce_if_ready(db, share.id)
    return f
