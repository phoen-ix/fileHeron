"""Per-file upload worker - v0.4.0 CTk port.

Replaces the v0.3.x ``UploadWorker(QThread)`` with a plain function
that wraps the existing ``run_with_progress`` async helper. The
direct-multipart vs TUS-resumable split is the same as before;
threading + UI marshalling lives in ``ui/_async.py``."""
from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Callable, Optional

from .. import api as api_pkg
from ..api import ApiClient
from ..tus import upload_tus
from ._async import run_with_progress

# The backend's DEFAULT for `uploads.max_direct_bytes`. The live value comes
# from /api/config-public at sign-in (set_direct_upload_limit): an admin can
# lower it, and a client that kept deciding on this constant sent every file
# between the two limits as a direct upload the server then refused with 413.
DIRECT_LIMIT_BYTES = 100 * 1024 * 1024
_direct_limit_bytes = DIRECT_LIMIT_BYTES

# Files uploading at once, however many were picked. Every file in a batch used
# to start immediately - one thread and one connection each - so a 40-file
# share was 40 simultaneous uploads competing for one uplink, with every bar
# crawling and the batch as a whole no faster. Queued files show "Pending"
# until a slot frees.
MAX_PARALLEL_UPLOADS = 4
_UPLOAD_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL_UPLOADS)


def set_direct_upload_limit(value: Optional[int]) -> None:
    """Adopt the instance's ceiling; anything unusable keeps the default."""
    global _direct_limit_bytes
    try:
        n = int(value) if value is not None else 0
    except (TypeError, ValueError):
        n = 0
    _direct_limit_bytes = n if n > 0 else DIRECT_LIMIT_BYTES


def direct_upload_limit() -> int:
    return _direct_limit_bytes

# A private database rather than the module-level `mimetypes.guess_type`.
#
# `mimetypes.init()` merges the WINDOWS REGISTRY (HKEY_CLASSES_ROOT\.ext ->
# "Content Type") into the module database, so on the platform this client
# ships for the answer depends on whatever the uploader happens to have
# installed. Two colleagues uploading the same .pdf could record different MIME
# types, and the value is stored server-side and served back to everyone - so a
# machine with an odd registry entry mislabels a file for every recipient, in
# the browser as much as here. `MimeTypes()` builds from Python's own table
# only, which is the same table on every machine.
_MIME = mimetypes.MimeTypes()


def guess_mime(path: str) -> str:
    """Deterministic, machine-independent MIME type for an upload."""
    mime, _ = _MIME.guess_type(path)
    return mime or "application/octet-stream"


def start_upload(
    root,
    api: ApiClient,
    *,
    share_id: str,
    file_path: Path,
    on_progress: Callable[[str, int, int], None],
    on_done: Callable[[str, str], None],
    on_failed: Callable[[str, str], None],
):
    """Kick off a single file upload in a background thread. ``on_*``
    callbacks fire on the Tk main loop (via run_with_progress).

    All three callbacks take ``path`` as their first arg so the
    caller can correlate concurrent uploads without juggling worker
    instances."""
    path_str = str(file_path)

    def _do(tick):
        with _UPLOAD_SLOTS:
            size = file_path.stat().st_size
            mime = guess_mime(path_str)
            if size <= direct_upload_limit():
                resp = api_pkg.upload_direct(
                    api,
                    share_id=share_id,
                    file_path=file_path,
                    mime_type=mime,
                    on_progress=tick,
                )
                return resp.file_id
            init = api_pkg.upload_init(
                api,
                share_id=share_id,
                filename=file_path.name,
                size_bytes=size,
                mime_type=mime,
            )
            upload_tus(
                server_url=api.server_url,
                tus_endpoint=init.tus_endpoint,
                upload_metadata_header=init.upload_metadata_header,
                file_path=file_path,
                bearer=api.bearer,
                on_progress=tick,
            )
            return init.file_id

    def _on_tick(done, total):
        on_progress(path_str, done, total)

    def _on_done(file_id):
        on_done(path_str, file_id)

    def _on_failed(exc):
        msg = (exc.localized() if hasattr(exc, "localized") else str(exc))
        on_failed(path_str, msg)

    return run_with_progress(
        root, _do,
        on_progress=_on_tick,
        on_done=_on_done,
        on_failed=_on_failed,
    )
