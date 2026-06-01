"""Segmented (parallel-range) downloader."""
from __future__ import annotations

import httpx
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import download_segmented as seg

SERVER = "https://files.example.com"
DATA = bytes((i % 251) for i in range(50))  # 50 deterministic bytes


def _range_handler(request: httpx.Request) -> httpx.Response:
    """A range-capable server: 206 for ranged GETs, 200 for full."""
    rng = request.headers.get("range")
    if not rng:
        return httpx.Response(
            200, content=DATA, headers={"Content-Length": str(len(DATA))}
        )
    spec = rng.split("=", 1)[1].split(",", 1)[0]
    a_s, b_s = spec.split("-", 1)
    a = int(a_s)
    b = int(b_s) if b_s else len(DATA) - 1
    b = min(b, len(DATA) - 1)
    chunk = DATA[a : b + 1]
    return httpx.Response(
        206,
        content=chunk,
        headers={
            "Content-Range": f"bytes {a}-{b}/{len(DATA)}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(chunk)),
        },
    )


def test_split_inclusive_ranges():
    assert seg._split(50, 10) == [(0, 9), (10, 19), (20, 29), (30, 39), (40, 49)]
    assert seg._split(25, 10) == [(0, 9), (10, 19), (20, 24)]
    assert seg._split(5, 10) == [(0, 4)]


@respx.mock
def test_segmented_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(seg, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(seg, "SEGMENT_SIZE", 10)
    respx.get(f"{SERVER}/api/files/fid/download").mock(side_effect=_range_handler)

    api = ApiClient(SERVER, api_token="fh_xx_yy")
    dest = tmp_path / "out.bin"
    progress: list[tuple[int, int]] = []
    out = seg.download_file_segmented(
        api, "fid", dest=dest, connections=4,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert out == dest
    assert dest.read_bytes() == DATA  # reassembled from 5 parallel segments
    assert not (tmp_path / "out.bin.part").exists()  # atomic rename cleaned up
    assert progress and progress[-1] == (len(DATA), len(DATA))


@respx.mock
def test_fallback_when_ranges_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(seg, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(seg, "SEGMENT_SIZE", 10)
    # Server ignores Range and always 200s → probe sees 200 → single-stream.
    respx.get(f"{SERVER}/api/files/fid/download").mock(
        return_value=httpx.Response(
            200, content=DATA, headers={"Content-Length": str(len(DATA))}
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    dest = tmp_path / "out.bin"
    out = seg.download_file_segmented(api, "fid", dest=dest, connections=4)
    assert out == dest
    assert dest.read_bytes() == DATA


@respx.mock
def test_connections_one_uses_single_stream(tmp_path):
    route = respx.get(f"{SERVER}/api/files/fid/download").mock(
        return_value=httpx.Response(
            200, content=DATA, headers={"Content-Length": str(len(DATA))}
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    dest = tmp_path / "out.bin"
    seg.download_file_segmented(api, "fid", dest=dest, connections=1)
    assert dest.read_bytes() == DATA
    assert route.call_count == 1  # no probe, single GET
