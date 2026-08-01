"""The bulk ZIP is seekable, and the seek is exact.

`flow-publiclink-5` (audit 2026-07-30): a public ZIP was charged before the
first byte and could not serve a partial response, so a 9 GB archive that died
at 90% was unrecoverable - the budget was already spent, and every retry started
over from byte 0 until the link ran out and answered 410 forever.

The fix is a real `Range` on the archive, which is only safe if three things
hold. These tests hold them:

1. **The seek is byte-exact.** Any concatenation of ranged reads equals the full
   archive, including offsets landing inside a local header, inside member data,
   inside a data descriptor, inside the central directory and on the last byte.
2. **The result is a valid ZIP.** The reassembled bytes open with stdlib
   `zipfile` and every member's contents match the source.
3. **The archive is reproducible.** Two generations of the same member list are
   byte-identical, so the two halves of a resumed download belong to the same
   archive. A change to the member list changes the ETag instead.

Plus the CRC economics: a member behind the resume point still needs its CRC for
the central directory, so it comes from the cache or is re-read - never guessed.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

from app.services.zip_writer import (
    _CENTRAL_HEADER,
    _CENTRAL_ZIP64_EXTRA,
    _DATA_DESCRIPTOR,
    _EOCD,
    _EOCD64,
    _EOCD64_LOCATOR,
    _LOCAL_HEADER,
    _LOCAL_ZIP64_EXTRA,
    SizedZipStream,
)

MEMBERS = [
    ("alpha.bin", b"A" * 700),
    ("beta.txt", b"beta-contents\n" * 40),
    ("gamma-with-a-long-name.dat", bytes(range(256)) * 9),
    ("empty.bin", b""),
    ("omega.bin", b"Z" * 1234),
]


class _DictCache:
    def __init__(self, seed=None):
        self.store = dict(seed or {})
        self.gets = 0
        self.puts = 0

    def get(self, key):
        self.gets += 1
        return self.store.get(key)

    def put(self, key, crc):
        self.puts += 1
        self.store[key] = crc


def _build(members=MEMBERS, *, mtime=1_700_000_000.0, cache=None) -> SizedZipStream:
    zs = SizedZipStream(mtime=mtime, crc_cache=cache)
    for name, data in members:
        zs.add_stream(
            (lambda d=data: io.BytesIO(d)), name, len(data), cache_key=f"key-{name}"
        )
    return zs


def _full(members=MEMBERS, **kw) -> bytes:
    return b"".join(_build(members, **kw))


# --- 1. the seek is byte-exact ----------------------------------------------


def test_the_declared_length_is_the_produced_length():
    zs = _build()
    assert len(b"".join(zs)) == len(zs)


def _interesting_offsets(zs: SizedZipStream) -> list[int]:
    """Every structural boundary, plus a byte on either side of it - the places
    an off-by-one in the layout arithmetic would hide."""
    offs: set[int] = {0, 1, len(zs) - 1}
    pos = 0
    for e in zs._entries:  # noqa: SLF001 - the layout IS what is under test
        n = len(e.name_bytes)
        for step in (
            _LOCAL_HEADER + n + _LOCAL_ZIP64_EXTRA,
            e.size,
            _DATA_DESCRIPTOR,
        ):
            offs.update({pos, pos + 1, max(0, pos + step - 1)})
            pos += step
    cd = pos
    for e in zs._entries:  # noqa: SLF001
        offs.update({pos, pos + 1})
        pos += _CENTRAL_HEADER + len(e.name_bytes) + _CENTRAL_ZIP64_EXTRA
    offs.update({cd, pos, pos + _EOCD64, pos + _EOCD64 + _EOCD64_LOCATOR})
    total = len(zs)
    offs.update({total - _EOCD, total - 1})
    return sorted(o for o in offs if 0 <= o < total)


@pytest.mark.parametrize("mtime", [0.0, 1_700_000_000.0])
def test_every_suffix_matches_the_full_archive(mtime):
    """`iter_from(n)` is the archive's tail from byte n. Checked at every block
    boundary and one byte either side of it."""
    zs = _build(mtime=mtime)
    full = b"".join(zs)
    for off in _interesting_offsets(zs):
        got = b"".join(_build(mtime=mtime).iter_from(off))
        assert got == full[off:], f"suffix from {off} diverges"


def test_a_bounded_range_is_the_matching_slice():
    zs = _build()
    full = b"".join(zs)
    total = len(full)
    for start, length in [
        (0, 1),
        (0, total),
        (5, 100),
        (total // 3, 700),
        (total - 1, 1),
        (total - 50, 50),
        (total - 50, 5000),  # asks past the end; clipped, not padded
    ]:
        got = b"".join(_build().iter_from(start, length))
        assert got == full[start : start + length], f"range {start}+{length}"


def test_an_arbitrary_split_reassembles_exactly():
    """The actual resume: N interruptions at unaligned points, each continued
    from where the last one stopped."""
    full = _full()
    cuts = [0, 37, 512, 900, 1500, 4096, len(full) - 3, len(full)]
    out = b"".join(
        b"".join(_build().iter_from(a, b - a)) for a, b in zip(cuts, cuts[1:], strict=False)
    )
    assert out == full


def test_an_offset_past_the_end_is_refused():
    zs = _build()
    with pytest.raises(ValueError):
        list(zs.iter_from(len(zs) + 1))


def test_the_last_byte_is_reachable():
    full = _full()
    assert b"".join(_build().iter_from(len(full) - 1)) == full[-1:]


def test_an_empty_member_list_still_produces_a_readable_archive():
    zs = SizedZipStream(mtime=0.0)
    blob = b"".join(zs)
    assert len(blob) == len(zs)
    assert zipfile.ZipFile(io.BytesIO(blob)).namelist() == []


# --- 2. the result is a valid ZIP -------------------------------------------


def test_the_reassembled_archive_opens_and_its_members_match():
    full = _full()
    for cut in (1, 300, len(full) // 2, len(full) - 40):
        blob = b"".join(_build().iter_from(0, cut)) + b"".join(
            _build().iter_from(cut)
        )
        assert blob == full
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert zf.testzip() is None
            assert zf.namelist() == [n for n, _ in MEMBERS]
            for name, data in MEMBERS:
                assert zf.read(name) == data


def test_a_resumed_tail_carries_the_real_crcs():
    """A member behind the resume point is never re-streamed, so its CRC has to
    come from somewhere. Getting that wrong produces an archive that opens and
    then fails `testzip()` - which is why this asserts on testzip and not just
    on namelist."""
    full = _full()
    cut = len(full) - (
        _EOCD + _EOCD64 + _EOCD64_LOCATOR + 30
    )  # inside the central directory
    blob = full[:cut] + b"".join(_build().iter_from(cut))
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.testzip() is None
        for name, data in MEMBERS:
            assert zf.read(name) == data


# --- 3. reproducibility ------------------------------------------------------


def test_two_generations_are_byte_identical():
    assert _full() == _full()


def test_the_timestamp_comes_from_the_caller_not_the_clock():
    """`__init__` used to call `time.time()`, so every generation differed in
    every DOS timestamp field and no resume could ever have been correct."""
    assert _full(mtime=1_700_000_000.0) != _full(mtime=1_600_000_000.0)
    assert _full(mtime=1_700_000_000.0) == _full(mtime=1_700_000_000.0)


def test_the_default_timestamp_is_fixed():
    """Pins the VALUE, not just self-consistency. Two streams built from
    `time.time()` in the same millisecond also match - DOS timestamps have
    two-second resolution - so a test that only compares two fresh streams
    passes against the very defect it is meant to catch, and fails in
    production only when a download straddles a two-second boundary."""
    from app.services.zip_writer import _DOS_EPOCH

    assert SizedZipStream()._mtime == _DOS_EPOCH  # noqa: SLF001
    assert _full(mtime=None) == _full(mtime=_DOS_EPOCH)


def test_the_timestamp_does_not_depend_on_the_container_timezone(monkeypatch):
    """`time.localtime` made the bytes a function of `TZ`."""
    import time as time_mod

    ref = _full()
    monkeypatch.setattr(time_mod, "localtime", lambda ts=None: time_mod.gmtime(43200))
    assert _full() == ref


# --- the ETag ---------------------------------------------------------------


def test_the_signature_changes_when_the_member_list_does():
    base = _build().signature()
    assert _build().signature() == base
    assert _build(MEMBERS[:-1]).signature() != base
    assert _build(list(reversed(MEMBERS))).signature() != base
    renamed = [("alpha-renamed.bin", MEMBERS[0][1])] + MEMBERS[1:]
    assert _build(renamed).signature() != base
    resized = [(MEMBERS[0][0], MEMBERS[0][1] + b"!")] + MEMBERS[1:]
    assert _build(resized).signature() != base


def test_the_signature_covers_the_timestamp_and_the_layout_version():
    assert _build(mtime=1.0).signature() != _build(mtime=2.0).signature()
    from app.services import zip_writer

    base = _build().signature()
    monkey = zip_writer.LAYOUT_VERSION
    try:
        zip_writer.LAYOUT_VERSION = monkey + 1
        assert _build().signature() != base
    finally:
        zip_writer.LAYOUT_VERSION = monkey


def test_two_different_names_of_the_same_total_length_differ():
    a = _build([("ab.bin", b"x"), ("c.bin", b"y")]).signature()
    b = _build([("a.bin", b"x"), ("bc.bin", b"y")]).signature()
    assert a != b


def test_a_name_containing_the_delimiter_cannot_forge_another_member_list():
    """Why the digest length-prefixes each name instead of trusting a separator:
    a filename is attacker-controlled, and `a|1|b` with one member serialises
    exactly like `a` and `b` with two unless the length goes in first. A forged
    match means `If-Range` accepts a resume across a different archive."""
    one = _build([("a|1|b", b"x")]).signature()
    two = _build([("a", b"x"), ("b", b"y")]).signature()
    assert one != two


# --- CRC economics -----------------------------------------------------------


def test_a_warm_cache_means_no_re_read():
    """The point of caching: resuming near the end of a 9 GB archive must not
    re-read the 8 GB already sent."""
    warm = _DictCache()
    b"".join(_build(cache=warm))  # a full stream populates it
    assert warm.puts == len(MEMBERS)

    zs = _build(cache=warm)
    assert zs.resume_cost(len(zs) - 10) == 0


def test_a_cold_cache_reports_the_re_read_it_would_cost():
    zs = _build(cache=_DictCache())
    cost = zs.resume_cost(len(zs) - 10)
    assert cost == sum(len(d) for _, d in MEMBERS)


def test_no_cache_at_all_still_produces_a_correct_archive():
    """Degrade, never corrupt: without a cache the writer re-reads instead of
    inventing a CRC."""
    full = _full(cache=None)
    cut = len(full) - 60
    blob = full[:cut] + b"".join(_build(cache=None).iter_from(cut))
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.testzip() is None


def test_a_cache_that_raises_is_survivable():
    class _Broken:
        def get(self, key):
            raise RuntimeError("redis down")

        def put(self, key, crc):
            raise RuntimeError("redis down")

    full = _full()
    got = b"".join(_build(cache=_Broken()).iter_from(0))
    assert got == full
    cut = len(full) - 60
    assert b"".join(_build(cache=_Broken()).iter_from(cut)) == full[cut:]


def test_a_cold_resume_pays_for_the_discarded_prefix():
    """With the CRC unknown, the member straddling the resume point must be read
    from byte 0 to compute it, so the prefix is read and thrown away.
    `resume_cost` says so rather than reporting a free lunch."""
    zs = _build(cache=_DictCache())
    data_start = _LOCAL_HEADER + len("alpha.bin") + _LOCAL_ZIP64_EXTRA
    assert zs.resume_cost(data_start + 200) == 200


def test_a_warm_resume_seeks_instead_of_paying_for_the_prefix():
    """Once the CRC is cached, the prefix does not need reading at all - so the
    cost is zero and the resume is accepted.

    This test asserted the opposite until audit #2, which is why it never
    surfaced that a resume was declined for exactly the archives the feature
    exists for: a single 9 GB member costs its whole 8.1 GiB prefix on the old
    accounting, over any sane ceiling, so the client that asked for byte 7.7e9
    got a 200 and the whole 9 GB from zero - restarting forever on a flaky
    link."""
    warm = _DictCache()
    b"".join(_build(cache=warm))
    zs = _build(cache=warm)
    data_start = _LOCAL_HEADER + len("alpha.bin") + _LOCAL_ZIP64_EXTRA
    assert zs.resume_cost(data_start + 200) == 0


def test_a_range_that_closes_mid_member_does_not_cache_a_partial_crc():
    """The loop returns early when the window is satisfied, at which point the
    running CRC covers only part of the member. Caching it would poison every
    later resume with a wrong checksum."""
    cache = _DictCache()
    zs = _build(cache=cache)
    data_start = _LOCAL_HEADER + len("alpha.bin") + _LOCAL_ZIP64_EXTRA
    list(zs.iter_from(0, data_start + 100))
    assert cache.store == {}


def test_a_short_member_fails_the_resume_instead_of_checksumming_the_shortfall():
    """The CRC-only re-read has the same obligation the streaming path has: a
    member that no longer produces the bytes it declares (truncated on disk, an
    object-store read cut short) must fail the transfer. Hashing whatever
    arrived would put a confident, wrong CRC in the central directory and into
    the cache, so every later resume of that archive would be corrupt too."""
    from app.services.zip_writer import ZipSizeMismatchError

    zs = SizedZipStream(mtime=0.0)
    zs.add_stream(lambda: io.BytesIO(b"A" * 700), "alpha.bin", 700, cache_key="k1")
    zs.add_stream(lambda: io.BytesIO(b"B" * 10), "beta.bin", 10, cache_key="k2")
    truncated = SizedZipStream(mtime=0.0)
    truncated.add_stream(lambda: io.BytesIO(b"A" * 3), "alpha.bin", 700, cache_key="k1")
    truncated.add_stream(lambda: io.BytesIO(b"B" * 10), "beta.bin", 10, cache_key="k2")

    tail = len(zs) - 40  # past alpha entirely: its CRC comes from a re-read
    with pytest.raises(ZipSizeMismatchError):
        list(truncated.iter_from(tail))


def test_the_cached_crc_is_the_one_the_archive_carries():
    cache = _DictCache()
    b"".join(_build(cache=cache))
    import zlib

    for name, data in MEMBERS:
        assert cache.store[f"key-{name}"] == zlib.crc32(data) & 0xFFFFFFFF


# --- audit #2: the data descriptor -------------------------------------------


def _parse_stream(zs, blob: bytes) -> list[tuple[str, int, bytes]]:
    """Read each member's bytes and its EMITTED data descriptor out of the
    stream. Returns (name, descriptor_crc, data).

    The member sizes in a local header are ZERO here - that is the whole point
    of the data-descriptor flag, and the reason a streaming extractor depends on
    the descriptor being right - so the offsets come from the writer's own
    layout rather than from the header fields.
    """
    out: list[tuple[str, int, bytes]] = []
    for e, (_h, d_start, desc_start) in zip(
        zs._entries, zs._positions(), strict=True
    ):
        data = blob[d_start : d_start + e.size]
        sig, dcrc, dc, du = struct.unpack(
            "<IIQQ", blob[desc_start : desc_start + _DATA_DESCRIPTOR]
        )
        assert sig == 0x08074B50, f"{e.arcname}: not a data descriptor"
        assert dc == du == e.size
        out.append((e.arcname, dcrc, data))
    return out


def test_every_data_descriptor_carries_the_real_crc():
    """Nothing tested the descriptor.

    Mutating `_descriptor` to emit `crc ^ 0xDEADBEEF` left the entire suite
    green, and `unzip -t` and `zipfile.testzip()` both reported no errors -
    they read the CENTRAL DIRECTORY, which carries a second, correct copy. Only
    a streaming extractor (libarchive/bsdtar, `unzip` from a pipe) reads the
    descriptor, and for those the archive was truncated garbage: measured, a
    1441-byte `a.txt` where 60 bytes were expected, `b.bin` never created, exit
    1 (audit #2).
    """
    zs = _build()
    blob = b"".join(zs)
    members = _parse_stream(zs, blob)
    assert members, "the stream parser found no members - check the walk"
    for name, descriptor_crc, data in members:
        assert descriptor_crc == zlib.crc32(data) & 0xFFFFFFFF, (
            f"{name}: the data descriptor's CRC does not match the bytes it "
            "describes; a streaming extractor rejects this archive"
        )


def test_a_warm_resume_still_carries_the_real_crc():
    """The seek-instead-of-read path reuses a CACHED crc for the descriptor. If
    the cache and the bytes ever disagree, this is where it shows."""
    warm = _DictCache()
    zs = _build(cache=warm)
    full = b"".join(zs)
    data_start = _LOCAL_HEADER + len("alpha.bin") + _LOCAL_ZIP64_EXTRA
    resumed = b"".join(_build(cache=warm).iter_from(data_start + 200))
    assert full[data_start + 200 :] == resumed
    for name, descriptor_crc, data in _parse_stream(zs, full):
        assert descriptor_crc == zlib.crc32(data) & 0xFFFFFFFF, name
