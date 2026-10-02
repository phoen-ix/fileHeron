"""An encrypted file is served exactly like its plaintext twin.

`_EncryptedFileResponse` keeps FileResponse's own Range/If-Range/HEAD logic and
replaces only the byte reads, so the ORACLE here is the plaintext file served by
the untouched FileResponse path: for every request shape, status, body and the
range headers must match. ETag and Last-Modified legitimately differ (the
encrypted ones come from the row, not the ciphertext's stat) and are checked on
their own. Then the real routes: the public-link download and the desktop
client's `bytes=1-1` probe, and S3, where an encrypted row is streamed by the
backend instead of redirected to a presigned URL of ciphertext.
"""
from __future__ import annotations

import os

import boto3
import httpx
import pytest
from moto import mock_aws
from starlette.applications import Starlette
from starlette.routing import Route

from app.middleware.errors import AppError
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import file_encryption
from app.services import storage_backend as sb
from app.utils import file_crypto
from tests._encryption_helpers import encrypt_row, store_plain

C = file_crypto.CHUNK_SIZE
DATA = os.urandom(3 * C + 7)
SIZE = len(DATA)


def _row(db, make_user, data=DATA, name="report.bin"):
    u = make_user(email=f"o{os.urandom(3).hex()}@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(share_id=sh.id, original_filename=name, size_bytes=len(data), mime_type="application/pdf",
             uploaded_by_id=u.id, state=FileState.clean, storage_path=store_plain(data))
    db.add(f)
    db.commit()
    return f


@pytest.fixture
def pair(db, make_user):
    """The same bytes stored twice: plaintext, and encrypted."""
    plain = _row(db, make_user)
    enc = _row(db, make_user)
    encrypt_row(db, enc)
    return plain, enc


def _app(plain, enc, *, name="report.bin", extra=None, counted=None):
    backend = sb.get_storage_backend()

    def serve(row):
        def endpoint(_request):
            return sb.serve_response(
                backend, locator=row.storage_path, cipher=file_encryption.cipher_for_file(row),
                filename=name, mime_type="application/pdf", ttl_sec=60,
                extra_headers=extra, count=counted is not None, file_id=row.id,
            )
        return endpoint

    return Starlette(routes=[
        Route("/plain", serve(plain), methods=["GET", "HEAD"]),
        Route("/enc", serve(enc), methods=["GET", "HEAD"]),
    ])


async def _both(app, headers=None, method="GET"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return (await c.request(method, "/plain", headers=headers or {}),
                await c.request(method, "/enc", headers=headers or {}))


_COMPARED = ("content-type", "content-length", "content-range", "accept-ranges", "content-disposition")

RANGES = [
    None, "bytes=0-0", "bytes=1-1", "bytes=0-", f"bytes={SIZE - 1}-{SIZE - 1}", "bytes=-1",
    "bytes=-5000", "bytes=-999999999", f"bytes={C - 3}-{C + 3}", f"bytes={C}-{2 * C}",
    f"bytes=12345-{3 * C}", f"bytes={SIZE}-", "bytes=0-0,5-9", "bytes=1-2,0-0",
    "bytes=abc", "items=0-1", "bytes=9-3",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("rng", RANGES)
async def test_every_range_answers_like_the_plaintext(pair, rng):
    plain, enc = pair
    a, b = await _both(_app(plain, enc), {"Range": rng} if rng else None)
    assert b.status_code == a.status_code, (rng, a.status_code, b.status_code)
    if a.headers.get("content-type", "").startswith("multipart/byteranges"):
        # Boundaries are random; compare the parts with the boundary removed.
        ba = a.headers["content-type"].split("boundary=")[1]
        bb = b.headers["content-type"].split("boundary=")[1]
        assert a.content.replace(ba.encode(), b"B") == b.content.replace(bb.encode(), b"B")
        assert a.headers["content-length"] == b.headers["content-length"]
        return
    assert b.content == a.content, rng
    for h in _COMPARED:
        assert b.headers.get(h) == a.headers.get(h), (rng, h)


@pytest.mark.asyncio
async def test_head_answers_like_the_plaintext(pair):
    plain, enc = pair
    a, b = await _both(_app(plain, enc), method="HEAD")
    assert (a.status_code, a.content) == (b.status_code, b.content) == (200, b"")
    for h in _COMPARED:
        assert b.headers.get(h) == a.headers.get(h), h


@pytest.mark.asyncio
async def test_the_probe_gets_206_with_a_content_range_and_an_etag(pair):
    plain, enc = pair
    _a, b = await _both(_app(plain, enc), {"Range": "bytes=1-1"})
    assert b.status_code == 206
    assert b.headers["content-range"] == f"bytes 1-1/{SIZE}"
    assert b.content == DATA[1:2]
    assert b.headers["etag"].startswith('"fhe-')


@pytest.mark.asyncio
async def test_if_range_resumes_on_a_match_and_restarts_otherwise(pair):
    plain, enc = pair
    app = _app(plain, enc)
    _a, b = await _both(app)
    etag, lm = b.headers["etag"], b.headers["last-modified"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for validator in (etag, lm):
            r = await c.get("/enc", headers={"Range": "bytes=100-199", "If-Range": validator})
            assert (r.status_code, r.content) == (206, DATA[100:200])
        r = await c.get("/enc", headers={"Range": "bytes=100-199", "If-Range": '"something-else"'})
        assert (r.status_code, len(r.content)) == (200, SIZE)


@pytest.mark.asyncio
async def test_the_etag_is_stable_and_per_file(db, make_user, pair):
    plain, enc = pair
    other = _row(db, make_user)
    encrypt_row(db, other)
    app = _app(enc, other)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        e1 = (await c.get("/plain")).headers["etag"]
        e2 = (await c.get("/plain")).headers["etag"]
        e3 = (await c.get("/enc")).headers["etag"]
    assert e1 == e2 != e3


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["report.bin", "Bericht März €.pdf", 'quote".txt'])
async def test_content_disposition_matches_for_any_name(pair, name):
    plain, enc = pair
    a, b = await _both(_app(plain, enc, name=name))
    assert b.headers["content-disposition"] == a.headers["content-disposition"]


@pytest.mark.asyncio
async def test_preview_headers_ride_along(pair):
    plain, enc = pair
    _a, b = await _both(_app(plain, enc, extra={"X-Content-Type-Options": "nosniff"}))
    assert b.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize("rng", [None, f"bytes={SIZE}-"])
async def test_the_drain_counter_is_released_after_a_send_and_after_a_416(pair, monkeypatch, rng):
    from app.services import transfer_activity

    started, finished = [], []
    monkeypatch.setattr(transfer_activity, "download_started", lambda fid: started.append(fid) or "dl-1")
    monkeypatch.setattr(transfer_activity, "download_finished", lambda dl: finished.append(dl))
    plain, enc = pair
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(plain, enc, counted=True)), base_url="http://t"
    ) as c:
        await c.get("/enc", headers={"Range": rng} if rng else {})
    assert started == [enc.id] and finished == ["dl-1"]


@pytest.mark.asyncio
async def test_the_drain_counter_is_released_when_the_client_goes_away(pair, monkeypatch):
    from app.services import transfer_activity

    finished = []
    monkeypatch.setattr(transfer_activity, "download_started", lambda _fid: "dl-2")
    monkeypatch.setattr(transfer_activity, "download_finished", lambda dl: finished.append(dl))
    _plain, enc = pair
    resp = sb.serve_response(
        sb.get_storage_backend(), locator=enc.storage_path, cipher=file_encryption.cipher_for_file(enc),
        filename="x", mime_type="application/pdf", ttl_sec=60, count=True, file_id=enc.id,
    )
    sent = []

    async def send(msg):
        sent.append(msg["type"])
        if msg["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b""}
    with pytest.raises(OSError):
        await resp(scope, receive, send)
    assert finished == ["dl-2"]


def test_a_ciphertext_of_the_wrong_size_is_refused_before_any_byte(db, make_user):
    enc = _row(db, make_user, DATA[:5000])
    encrypt_row(db, enc)
    with open(enc.storage_path, "ab") as fh:
        fh.write(b"x")
    with pytest.raises(AppError) as exc:
        sb.serve_response(
            sb.get_storage_backend(), locator=enc.storage_path, cipher=file_encryption.cipher_for_file(enc),
            filename="x", mime_type="application/pdf", ttl_sec=60,
        )
    assert exc.value.code == "FILE_UNREADABLE"


def _flip_a_bit(path: str, offset: int) -> None:
    with open(path, "r+b") as fh:
        fh.seek(offset)
        b = fh.read(1)
        fh.seek(-1, 1)
        fh.write(bytes([b[0] ^ 1]))


@pytest.mark.asyncio
async def test_tampered_bytes_abort_the_transfer_and_are_reported(db, make_user, monkeypatch):
    from app.models.audit_log import AuditEventType, AuditLog

    enc = _row(db, make_user, DATA[: 2 * C])
    encrypt_row(db, enc)
    _flip_a_bit(enc.storage_path, file_crypto.chunk_offset(1) + 10)
    app = _app(enc, enc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        with pytest.raises(file_crypto.FileIntegrityError):
            await c.get("/enc")
    db.expire_all()
    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.file_integrity_failed.value,
        AuditLog.target_id == enc.id,
    ).count() == 1


def test_serve_response_insists_on_a_cipher_decision():
    import inspect

    p = inspect.signature(sb.serve_response).parameters["cipher"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty


# --- the real public-link route ------------------------------------------------


@pytest.fixture
def public_encrypted(db, make_user):
    from app.services import public_link as public_link_svc

    f = _row(db, make_user)
    encrypt_row(db, f)
    sh = db.get(Share, f.share_id)
    owner_id = sh.created_by_id
    from app.models.user import User

    created = public_link_svc.create_link(
        db, share=sh, actor=db.get(User, owner_id), password=None, download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    return f, created.plaintext_token


@pytest.mark.asyncio
async def test_the_public_route_serves_the_plaintext_and_the_client_probe(client, public_encrypted):
    f, token = public_encrypted
    url = f"/api/public/{token}/files/{f.id}/download"
    r = await client.get(url, headers={"Range": "bytes=1-1"})
    assert (r.status_code, r.content, r.headers["content-range"]) == (206, DATA[1:2], f"bytes 1-1/{SIZE}")
    r = await client.get(url)
    assert r.status_code == 200 and r.content == DATA


# --- S3: streamed by the backend, never a presigned URL of ciphertext ---------------


@pytest.fixture
def s3(monkeypatch):
    for k, v in {
        "STORAGE_BACKEND": "s3", "S3_BUCKET": "fh-enc-serve", "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_KEY_PREFIX": "",
    }.items():
        monkeypatch.setattr(f"app.config.settings.{k}", v)
    sb.reset_storage_backend_cache()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="fh-enc-serve")
        yield
    sb.reset_storage_backend_cache()


@pytest.mark.asyncio
async def test_on_s3_an_encrypted_row_is_streamed_and_a_plaintext_one_redirected(db, make_user, s3):
    plain = _row(db, make_user, DATA[: C + 9])
    enc = _row(db, make_user, DATA[: C + 9])
    encrypt_row(db, enc)
    app = _app(plain, enc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/plain")
        assert r.status_code == 307
        r = await c.get("/enc", headers={"Range": f"bytes={C - 2}-{C + 2}"})
        assert (r.status_code, r.content) == (206, DATA[C - 2 : C + 3])
        r = await c.get("/enc")
        assert (r.status_code, r.content) == (200, DATA[: C + 9])
