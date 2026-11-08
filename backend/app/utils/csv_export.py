"""The admin log exports' shared CSV plumbing.

The audit, mail and error logs each carried the same three pieces - a
formula-injection guard, a keyset cursor codec and a batched streaming loop
(header, `yield_per(500)`, flush past 8 KiB, tail) - and analytics a fourth
copy of the guard (audit 2026-11-08). Each export keeps its own columns.
"""
from __future__ import annotations

import base64
import csv
import io
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any

from fastapi.responses import StreamingResponse

from ..middleware.errors import AppError

_FLUSH_AT = 8192
BATCH_SIZE = 500


def csv_safe(value: Any) -> str:
    """Neutralise CSV formula injection (finding L3). Spreadsheet apps execute
    a cell whose text starts with = + - @ (or a leading control char).
    User-influenced fields land in the exports (a failed-email `target_id`,
    display names in `extra`, mail subjects), so prefix a single quote to defuse
    the formula while keeping the value readable."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def encode_cursor(created_at: datetime, row_id: int) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(s: str) -> tuple[datetime, int]:
    try:
        padded = s + "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), int(id_str)
    except Exception as e:
        raise AppError(400, "INVALID_CURSOR", "Cursor is not valid.") from e


def stream_csv[T](header: list[str], rows: Iterable[T], to_row: Callable[[T], list[Any]]) -> Iterator[bytes]:
    """Header first, then rows in ~8 KiB chunks, so a large export never sits
    in memory whole. Pass a query's `.yield_per(BATCH_SIZE)` as `rows`."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue().encode("utf-8")
    buf.seek(0)
    buf.truncate()
    for r in rows:
        writer.writerow(to_row(r))
        if buf.tell() > _FLUSH_AT:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate()
    tail = buf.getvalue()
    if tail:
        yield tail.encode("utf-8")


def csv_response[T](
    filename: str, header: list[str], rows: Iterable[T], to_row: Callable[[T], list[Any]]
) -> StreamingResponse:
    return StreamingResponse(
        stream_csv(header, rows, to_row),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
