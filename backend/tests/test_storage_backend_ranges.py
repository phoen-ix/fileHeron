"""The storage-backend primitives encryption at rest reads and writes through:
ranged reads, streamed create-only writes, sibling locators - on the local
backend and on S3 (moto), which the suite otherwise rarely selects."""
from __future__ import annotations

import io
import os

import boto3
import pytest
from moto import mock_aws

from app.services import storage_backend as sb
from app.utils import file_crypto

_BUCKET = "fh-ranges-bucket"


@pytest.fixture
def s3(monkeypatch):
    for k, v in {
        "STORAGE_BACKEND": "s3", "S3_BUCKET": _BUCKET, "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_KEY_PREFIX": "p/",
    }.items():
        monkeypatch.setattr(f"app.config.settings.{k}", v)
    sb.reset_storage_backend_cache()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield sb.get_storage_backend()
    sb.reset_storage_backend_cache()


@pytest.fixture
def local():
    sb.reset_storage_backend_cache()
    return sb.get_storage_backend()


@pytest.fixture(params=["local", "s3"])
def backend(request):
    return request.getfixturevalue(request.param)


def test_write_then_ranged_reads(backend):
    data = os.urandom(200_000)
    loc = backend.generate_locator(f"r-{os.urandom(3).hex()}")
    backend.write_stream(loc, io.BytesIO(data))
    assert backend.size(loc) == len(data)
    for start, end in [(0, 0), (1, 1), (0, len(data) - 1), (12345, 67890), (len(data) - 1, len(data) - 1)]:
        with backend.open_range(loc, start, end) as fh:
            assert fh.read() == data[start : end + 1], (start, end)


def test_an_encrypting_stream_round_trips_through_the_backend(backend):
    data = os.urandom(file_crypto.CHUNK_SIZE + 77)
    key = file_crypto.new_data_key()
    loc = backend.generate_locator(f"e-{os.urandom(3).hex()}")
    backend.write_stream(loc, file_crypto.EncryptingReader(io.BytesIO(data), key, len(data)))
    assert backend.size(loc) == file_crypto.ciphertext_size(len(data))
    opener = lambda a, b: backend.open_range(loc, a, b)  # noqa: E731
    assert b"".join(file_crypto.iter_plaintext(opener, key, len(data))) == data
    assert b"".join(file_crypto.iter_plaintext(opener, key, len(data), 1_000_000, 1_048_600)) == (
        data[1_000_000:1_048_601]
    )


def test_sibling_locators_stay_beside_the_original(local, s3):
    assert local.sibling_locator("/data/files/2026/10/abc.bin", "abc.1234.fhe") == (
        "/data/files/2026/10/abc.1234.fhe"
    )
    assert s3.sibling_locator("p/2026/10/abc.bin", "abc.1234.fhe") == "p/2026/10/abc.1234.fhe"


def test_the_local_write_never_overwrites(local):
    loc = local.generate_locator(f"x-{os.urandom(3).hex()}")
    local.write_stream(loc, io.BytesIO(b"first"))
    with pytest.raises(FileExistsError):
        local.write_stream(loc, io.BytesIO(b"second"))
    with local.open(loc) as fh:
        assert fh.read() == b"first"
