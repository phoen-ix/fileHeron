"""QThread that runs an upload (direct or TUS) and emits progress."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..tus import TusError, upload_tus

# Match backend default unless overridden at runtime.
DIRECT_LIMIT_BYTES = 100 * 1024 * 1024


class UploadWorker(QThread):
    progress = Signal(str, int, int)  # path, done, total
    finished_one = Signal(str, str)   # path, file_id
    failed = Signal(str, str)         # path, message

    def __init__(
        self,
        api: ApiClient,
        share_id: str,
        file_path: Path,
    ) -> None:
        super().__init__()
        self.api = api
        self.share_id = share_id
        self.file_path = file_path

    def run(self) -> None:
        try:
            size = self.file_path.stat().st_size
            mime, _ = mimetypes.guess_type(str(self.file_path))
            mime = mime or "application/octet-stream"

            if size <= DIRECT_LIMIT_BYTES:
                resp = api_pkg.upload_direct(
                    self.api,
                    share_id=self.share_id,
                    file_path=self.file_path,
                    mime_type=mime,
                    on_progress=lambda d, t: self.progress.emit(str(self.file_path), d, t),
                )
                self.finished_one.emit(str(self.file_path), resp.file_id)
                return

            init = api_pkg.upload_init(
                self.api,
                share_id=self.share_id,
                filename=self.file_path.name,
                size_bytes=size,
                mime_type=mime,
            )
            upload_tus(
                server_url=self.api.server_url,
                tus_endpoint=init.tus_endpoint,
                upload_metadata_header=init.upload_metadata_header,
                file_path=self.file_path,
                bearer=self.api.bearer,
                on_progress=lambda d, t: self.progress.emit(str(self.file_path), d, t),
            )
            self.finished_one.emit(str(self.file_path), init.file_id)
        except ApiError as exc:
            self.failed.emit(str(self.file_path), exc.message or str(exc))
        except TusError as exc:
            self.failed.emit(str(self.file_path), str(exc))
        except Exception as exc:  # network / disk
            self.failed.emit(str(self.file_path), str(exc))
