"""Resumable + pausable download orchestrator (download_file_resumable)."""
from __future__ import annotations

import threading

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, DownloadCancelled, DownloadPaused
from fileheron_client.api import download_checkpoint as ck
from fileheron_client.api import download_resumable as dr

SERVER = "https://files.example.com"
DATA = bytes((i % 251) for i in range(50))  # 50 deterministic bytes
ETAG = '"abc-50"'


def _handler(request: httpx.Request) -> httpx.Response:
    """A range-capable server with a stable ETag (206 for ranges, 200 full)."""
    rng = request.headers.get("range")
    if not rng:
        return httpx.Response(
            200, content=DATA,
            headers={
                "Content-Length": str(len(DATA)),
                "ETag": ETAG,
                "Accept-Ranges": "bytes",
            },
        )
    spec = rng.split("=", 1)[1].split(",", 1)[0]
    a_s, b_s = spec.split("-", 1)
    a = int(a_s)
    b = int(b_s) if b_s else len(DATA) - 1
    b = min(b, len(DATA) - 1)
    chunk = DATA[a : b + 1]
    return httpx.Response(
        206, content=chunk,
        headers={
            "Content-Range": f"bytes {a}-{b}/{len(DATA)}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(chunk)),
            "ETag": ETAG,
        },
    )


def _api() -> ApiClient:
    return ApiClient(SERVER, api_token="fh_xx_yy")


@respx.mock
def test_probe_uses_offset_range_not_byte_zero():
    """The probe must start above byte 0. The backend counts a range that
    starts at byte 0 as a full (budget-charged) download, so a bytes=0-0 probe
    double-charged the share's download budget and got refused under
    maintenance mode; a start > 0 is an uncounted continuation."""
    seen: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        seen["range"] = request.headers.get("range")
        return httpx.Response(
            206, content=b"x",
            headers={"Content-Range": f"bytes 1-1/{len(DATA)}", "ETag": ETAG},
        )

    respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_capture)
    result = dr._probe(_api(), "/api/files/fid/download", {})
    assert seen["range"] == "bytes=1-1"
    assert result == (len(DATA), ETAG)


@respx.mock
def test_resumable_segmented_happy(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(dr, "SEGMENT_SIZE", 10)
    respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_handler)
    dest = tmp_path / "out.bin"
    out = dr.download_file_resumable(_api(), "fid", dest=dest, connections=4)
    assert out == dest
    assert dest.read_bytes() == DATA
    assert not ck.part_path(dest).exists()
    assert not ck.ckpt_path(dest).exists()  # checkpoint cleared on success


@respx.mock
def test_resume_segmented_skips_completed_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(dr, "SEGMENT_SIZE", 10)
    route = respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_handler)
    dest = tmp_path / "out.bin"
    # Pre-seed a partial: 50-byte .part with segments 0 (0-9) + 2 (20-29) done.
    buf = bytearray(50)
    buf[0:10] = DATA[0:10]
    buf[20:30] = DATA[20:30]
    ck.part_path(dest).write_bytes(bytes(buf))
    ck.write(dest, ck.Checkpoint(
        file_id="fid", total=50, etag=ETAG, mode="segmented",
        segment_size=10, completed=[0, 2],
    ))

    out = dr.download_file_resumable(_api(), "fid", dest=dest, connections=4)
    assert out == dest
    assert dest.read_bytes() == DATA  # reassembled correctly

    ranges = [c.request.headers.get("range") for c in route.calls]
    # The already-complete segments are NOT re-requested...
    assert "bytes=0-9" not in ranges
    assert "bytes=20-29" not in ranges
    # ...only the missing ones (1, 3, 4) plus the 0-0 probe.
    assert "bytes=10-19" in ranges
    assert "bytes=30-39" in ranges
    assert "bytes=40-49" in ranges


@respx.mock
def test_pause_then_resume_single_stream(tmp_path, monkeypatch):
    # 10-byte chunks so a 50-byte body streams in 5 ticks → we can pause mid-way.
    monkeypatch.setattr(dr, "CHUNK", 10)
    respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_handler)
    dest = tmp_path / "out.bin"
    pause = threading.Event()

    def _on_progress(done, total):
        if done >= 20:
            pause.set()  # ask to pause after ~2 chunks

    with pytest.raises(DownloadPaused):
        dr.download_file_resumable(
            _api(), "fid", dest=dest, connections=1,
            on_progress=_on_progress, pause=pause,
        )

    # Partial + checkpoint KEPT (not discarded).
    part = ck.part_path(dest)
    assert part.exists()
    assert 0 < part.stat().st_size < 50
    assert ck.read(dest) is not None

    # Resume (fresh control events) → completes from the saved offset.
    out = dr.download_file_resumable(_api(), "fid", dest=dest, connections=1)
    assert out == dest
    assert dest.read_bytes() == DATA
    assert not part.exists()
    assert not ck.ckpt_path(dest).exists()


@respx.mock
def test_resume_single_stream_sends_range(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "CHUNK", 10)
    route = respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_handler)
    dest = tmp_path / "out.bin"
    # Pre-seed a 20-byte single-stream partial.
    ck.part_path(dest).write_bytes(DATA[:20])
    ck.write(dest, ck.Checkpoint(file_id="fid", total=50, etag=ETAG, mode="single"))

    dr.download_file_resumable(_api(), "fid", dest=dest, connections=1)
    assert dest.read_bytes() == DATA
    ranges = [c.request.headers.get("range") for c in route.calls]
    assert "bytes=20-" in ranges  # resumed from the saved offset, not byte 0


def test_cancel_preset_discards_partial(tmp_path):
    dest = tmp_path / "out.bin"
    ev = threading.Event()
    ev.set()
    with pytest.raises(DownloadCancelled):
        dr.download_file_resumable(_api(), "fid", dest=dest, connections=4, cancel=ev)
    assert not ck.part_path(dest).exists()
    assert not ck.ckpt_path(dest).exists()
    assert not dest.exists()


@respx.mock
def test_stale_checkpoint_etag_mismatch_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "CHUNK", 10)
    respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_handler)
    dest = tmp_path / "out.bin"
    # A partial whose checkpoint references a DIFFERENT etag (file changed).
    ck.part_path(dest).write_bytes(b"OLDOLDOLD")
    ck.write(dest, ck.Checkpoint(file_id="fid", total=50, etag='"stale"', mode="single"))

    dr.download_file_resumable(_api(), "fid", dest=dest, connections=1)
    assert dest.read_bytes() == DATA  # discarded the stale partial, fetched fresh
    assert not ck.part_path(dest).exists()
    assert not ck.ckpt_path(dest).exists()
