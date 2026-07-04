"""Segment-plan helper (``_split``) + the single-stream ``download_file``."""
from __future__ import annotations

import threading

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import download_segmented as seg
from fileheron_client.api.files import DownloadCancelled, download_file

SERVER = "https://files.example.com"
DATA = bytes((i % 251) for i in range(50))  # 50 deterministic bytes


def test_split_inclusive_ranges():
    assert seg._split(50, 10) == [(0, 9), (10, 19), (20, 29), (30, 39), (40, 49)]
    assert seg._split(25, 10) == [(0, 9), (10, 19), (20, 24)]
    assert seg._split(5, 10) == [(0, 4)]


@respx.mock
def test_single_stream_cancel_removes_partial(tmp_path):
    respx.get(f"{SERVER}/api/files/fid/download").mock(
        return_value=httpx.Response(
            200, content=DATA, headers={"Content-Length": str(len(DATA))}
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    dest = tmp_path / "out.bin"
    ev = threading.Event()
    ev.set()  # cancel before the first chunk is written
    with pytest.raises(DownloadCancelled):
        download_file(api, "fid", dest=dest, cancel=ev)
    assert not dest.exists()  # partial removed
