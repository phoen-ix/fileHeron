"""L18: the rescan_inbound_attachments cron settles attachments stuck `pending`
(e.g. left that way by a ClamAV outage at ingest), and defers when clamd is
still unavailable."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass
from app.services import av_scan
from app.services import storage_backend as sb
from app.services.av_scan import ScanResult
from app.workers import rescan_inbound_attachments as mod
from app.workers.rescan_inbound_attachments import rescan_inbound_attachments


def _seed_pending(db) -> InboundAttachment:
    msg = InboundMessage(
        received_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
        sender_email="a@b.c",
        subject="s",
        imap_uid=1,
        uidvalidity=1,
        classification=MessageClass.normal,
        has_attachments=True,
    )
    db.add(msg)
    db.flush()

    # Real on-disk artifact so the backend can resolve local_path / open.
    backend = sb.get_storage_backend()
    locator = backend.generate_locator("inbound-rescan-test")
    fd, tmp = tempfile.mkstemp()
    os.write(fd, b"some bytes")
    os.close(fd)
    backend.finalize(tmp, locator)

    att = InboundAttachment(
        message_id=msg.id, filename="x.pdf", size_bytes=9,
        storage_key=locator, av_state=AttachmentAVState.pending,
    )
    db.add(att)
    db.commit()
    return att


@pytest.mark.asyncio
async def test_rescan_settles_pending_to_clean(db, monkeypatch):
    att = _seed_pending(db)
    monkeypatch.setattr(av_scan, "scan_path", lambda _p: ScanResult(state="clean", signature=None, raw="ok"))
    monkeypatch.setattr(av_scan, "scan_stream", lambda _fh: ScanResult(state="clean", signature=None, raw="ok"))
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await rescan_inbound_attachments(None)
    assert result["clean"] == 1
    db.expire_all()
    a = db.query(InboundAttachment).filter_by(id=att.id).one()
    assert a.av_state == AttachmentAVState.clean


@pytest.mark.asyncio
async def test_rescan_defers_when_clamd_unavailable(db, monkeypatch):
    att = _seed_pending(db)

    def _down(_p):
        raise av_scan.AVUnavailableError("clamd down")

    monkeypatch.setattr(av_scan, "scan_path", _down)
    monkeypatch.setattr(av_scan, "scan_stream", _down)
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await rescan_inbound_attachments(None)
    assert result["rescanned"] == 0
    db.expire_all()
    a = db.query(InboundAttachment).filter_by(id=att.id).one()
    assert a.av_state == AttachmentAVState.pending  # untouched, retries next run


def test_the_scan_does_not_run_on_the_event_loop():
    """Both scan paths are blocking socket I/O and this is an `async def` on the
    ARQ worker's single event loop. With _BATCH = 200 and a 1800s socket
    timeout the worst case is ~100 hours of frozen loop - and job_timeout cannot
    cut it short, because asyncio.wait_for cannot pre-empt a blocking recv. That
    freezes cron_dispatch itself, the every-minute tick every other job depends
    on.

    Asserted structurally because the failure is the ABSENCE of a yield point:
    a behavioural test would have to actually block the loop to observe it.
    workers/av_scan.py carries the same guarantee, established by the
    2026-07-30 audit - which fixed one of the two call sites.
    """
    import inspect

    from app.workers import rescan_inbound_attachments as mod

    src = inspect.getsource(mod.rescan_inbound_attachments)
    assert "asyncio.to_thread" in src, (
        "the blocking clamd call is back on the event loop"
    )
    for direct in ("av_scan.scan_path(", "av_scan.scan_stream("):
        # They may appear inside the threaded closure; what must not happen is a
        # bare await-less call in the loop body.
        assert f"result = {direct}" not in src, f"{direct} is called inline again"
