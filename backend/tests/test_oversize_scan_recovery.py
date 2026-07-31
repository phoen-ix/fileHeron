"""A file clamd cannot scan must still reach a terminal state.

From the 2026-07-30 audit residual sweep.

fileHeron accepts uploads up to 30 GB. clamd clamps its own MaxFileSize to
INT_MAX (~2 GiB) whatever clamd.conf says, so for the flagship workload there is
no verdict to be had. The product's answer is to serve the file with an honest
label - `clean` state, `av_unscanned = True`, an `unscanned` badge and a
`file_served_unscanned` audit row - rather than refuse it.

The defect was that the answer was not TERMINAL on every path:

- `cleanup_stale_uploads`, the only automated recovery for a scan that never
  finished, excluded `size_bytes > AV_MAX_SCAN_BYTES` on the grounds that
  re-scanning "would loop forever". That was an object-store failure mode
  generalised to both backends.
- On the object store it really did not terminate: INSTREAM answers `error` for
  an oversize stream, `error` is not a state flip, so the file was re-enqueued
  forever.
- So an oversize file whose scan job burned its four retries - any clamav
  restart, reboot or OOM would do it, since the backoff totalled 50 seconds
  against a 180-second healthcheck budget - sat at `ready_unscanned` with
  nothing left to move it. Every download answered `425 SCAN_IN_PROGRESS`,
  "try again shortly", forever, while the bytes kept counting against quota.

Files UNDER the limit self-healed within 30 minutes, and every existing test
used `size_bytes=1024`, which is why this shipped: the working path was the only
one exercised.
"""
from __future__ import annotations

import pytest

from app.config import CLAMD_MAX_FILE_SIZE, settings
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.workers.av_scan import av_scan_file

OVERSIZE = CLAMD_MAX_FILE_SIZE + 1


@pytest.fixture
def oversize_file(db, make_user, tmp_path):
    """A finalized, unscanned file larger than anything clamd will read."""
    owner = make_user(email="big@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "huge.bin"
    path.write_bytes(b"not actually huge")
    from app.utils.timeutil import utc_now

    f = File(
        id="00000000-0000-0000-0000-0000000000big",
        share_id=sh.id,
        original_filename="huge.bin",
        mime_type="application/octet-stream",
        size_bytes=OVERSIZE,
        storage_path=str(path),
        state=FileState.ready_unscanned,
        uploaded_by_id=owner.id,
        finalized_at=utc_now(),
    )
    db.add(f)
    db.flush()
    db.commit()
    return f, sh, owner


# --- the file reaches a terminal state --------------------------------------


@pytest.mark.asyncio
async def test_an_oversize_file_is_released_as_unscanned(db, oversize_file, monkeypatch):
    f, sh, owner = oversize_file

    def _must_not_scan(*a, **k):
        raise AssertionError(
            "clamd was asked to scan a file past its MaxFileSize; the verdict "
            "would be meaningless and on S3 this streams gigabytes to be rejected"
        )

    monkeypatch.setattr(av_scan_svc, "scan_path", _must_not_scan)
    monkeypatch.setattr(av_scan_svc, "scan_stream", _must_not_scan)

    result = await av_scan_file({}, f.id)
    assert result["state"] == "clean"
    assert result["av_unscanned"] is True

    db.expire_all()
    row = db.query(File).filter(File.id == f.id).one()
    assert row.state == FileState.clean
    assert row.av_unscanned is True


@pytest.mark.asyncio
async def test_the_release_is_audited(db, oversize_file, monkeypatch):
    """Serving bytes without a verdict has to leave a durable record, or the
    only evidence is a UI badge on a row anyone can later change."""
    f, sh, owner = oversize_file
    monkeypatch.setattr(av_scan_svc, "scan_path", lambda *a, **k: None)

    await av_scan_file({}, f.id)
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.file_served_unscanned,
            AuditLog.target_id == f.id,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["reason"] == "exceeds_clamd_max_file_size"
    assert rows[0].extra["size_bytes"] == OVERSIZE


@pytest.mark.asyncio
async def test_rescanning_it_is_idempotent(db, oversize_file, monkeypatch):
    """`cleanup_stale_uploads` re-enqueues stuck files, so the oversize path has
    to converge rather than flip-flop or duplicate its audit trail."""
    f, sh, owner = oversize_file
    monkeypatch.setattr(av_scan_svc, "scan_path", lambda *a, **k: None)

    first = await av_scan_file({}, f.id)
    second = await av_scan_file({}, f.id)
    assert first["state"] == "clean"
    assert second.get("skipped") is True  # no longer ready_unscanned

    assert (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.file_served_unscanned,
            AuditLog.target_id == f.id,
        )
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_a_file_deleted_mid_scan_is_not_resurrected(
    db, oversize_file, monkeypatch
):
    """Share expiry can commit `deleted` and unlink the bytes while this runs.
    Flipping it back to `clean` would advertise a file that is gone."""
    f, sh, owner = oversize_file
    monkeypatch.setattr(av_scan_svc, "scan_path", lambda *a, **k: None)
    db.query(File).filter(File.id == f.id).update({File.state: FileState.deleted})
    db.commit()

    result = await av_scan_file({}, f.id)
    assert result.get("skipped") or result["state"] in ("superseded", "deleted")
    db.expire_all()
    assert db.query(File).filter(File.id == f.id).one().state == FileState.deleted


# --- the sweep must pick them up --------------------------------------------


def test_the_recovery_sweep_has_no_size_filter():
    """The exclusion is what made the state permanent. It is the only automated
    recovery there is, so a size filter here is a data-availability bug."""
    import inspect

    from app.workers import cleanup_stale_uploads

    src = inspect.getsource(cleanup_stale_uploads)
    assert "File.size_bytes <= settings.AV_MAX_SCAN_BYTES" not in src
    assert "File.size_bytes >" not in src.split("rescan_cutoff")[-1]


@pytest.mark.asyncio
async def test_an_oversize_stuck_file_is_re_enqueued(db, oversize_file, monkeypatch):
    """End to end: a file stuck past the threshold gets a job, whatever its size."""
    from datetime import timedelta

    from app.utils.timeutil import utc_now
    from app.workers import cleanup_stale_uploads

    f, sh, owner = oversize_file
    db.query(File).filter(File.id == f.id).update(
        {File.finalized_at: utc_now() - timedelta(hours=2)}
    )
    db.commit()

    enqueued: list = []

    async def _fake(name, *a, **k):
        enqueued.append((name, a))

    monkeypatch.setattr(cleanup_stale_uploads.job_queue, "aenqueue", _fake)
    out = await cleanup_stale_uploads.cleanup_stale_uploads({})
    assert out["rescans_requeued"] >= 1
    assert ("av_scan_file", (f.id,)) in enqueued


# --- the retry budget -------------------------------------------------------


def test_the_retry_backoff_outlasts_a_clamav_cold_start():
    """The trigger. `min(60, 5 * attempt)` gave 5+10+15+20 = 50 seconds across
    the four retries `max_tries=5` allows, against a clamav healthcheck that is
    allowed 180 seconds to come up (and a first freshclam sync far longer than
    that). Every clamav restart therefore burned the in-flight scans, which is
    what manufactured the stranded files in the first place."""
    from app.workers.av_scan import _RETRY_MAX_DEFER_SEC
    from app.workers.worker import WorkerSettings

    retries = WorkerSettings.max_tries - 1
    total = sum(min(_RETRY_MAX_DEFER_SEC, 30 * attempt) for attempt in range(1, retries + 1))
    assert total >= 180, f"retry budget is {total}s, under the clamav start budget"


# --- the setting that made it worse everywhere ------------------------------


def test_av_max_scan_bytes_cannot_be_raised_past_what_clamd_reads():
    """`.env.example` shipped 30 GiB for four releases and `install.sh` copies it
    onto every fresh self-host, so those deployments recorded 30 GB uploads as
    `clean` with `av_unscanned = False` - no badge, no audit row, nothing to
    distinguish them from a file clamd actually read. That is the original H3
    defect surviving its own fix one order of magnitude up. Clamping makes it
    unrepresentable instead of documented-against in three places."""
    from app.config import Settings

    # The operator must be TOLD, not silently corrected - their .env still says
    # 30 GiB and they need to know it is not what is in force.
    with pytest.warns(UserWarning, match="exceeds what clamd can scan"):
        s = Settings(AV_MAX_SCAN_BYTES=32212254720)
    assert s.AV_MAX_SCAN_BYTES == CLAMD_MAX_FILE_SIZE


def test_a_lower_limit_is_still_the_operators_to_set():
    """Clamping is a ceiling, not a fixed value - an operator may legitimately
    want a stricter TRUST threshold (a small clamd, a slow disk)."""
    from app.config import Settings

    assert Settings(AV_MAX_SCAN_BYTES=50_000_000).AV_MAX_SCAN_BYTES == 50_000_000


def test_a_lower_limit_does_not_disable_scanning():
    """The blocker an adversarial review caught before this shipped.

    The first cut keyed the pre-scan skip off `AV_MAX_SCAN_BYTES`. At the
    default that is a wash, because clamd genuinely cannot read past it - but
    `docker/clamav/clamd.conf` invites an operator to lower it to match a
    memory-constrained clamd, and lowering it then meant files above the new
    value were never handed to clamd at all while still being recorded `clean`.
    An infected 200 MB upload would have been served to every recipient where
    the previous code quarantined it and revoked the share.

    The skip is keyed to `CLAMD_MAX_FILE_SIZE` instead. The tunable decides
    whether a `clean` answer is TRUSTED, never whether the question is asked."""
    import inspect

    from app.workers import av_scan as av_scan_worker

    src = inspect.getsource(av_scan_worker.av_scan_file)
    pre_scan = src.split("def _scan()")[0]
    assert "> CLAMD_MAX_FILE_SIZE" in pre_scan
    assert "settings.AV_MAX_SCAN_BYTES" not in pre_scan, (
        "the scan-skip decision is keyed off the operator-tunable trust "
        "threshold; lowering it would silently disable antivirus"
    )


@pytest.mark.asyncio
async def test_an_infected_file_above_a_lowered_limit_is_still_quarantined(
    db, make_user, tmp_path, monkeypatch
):
    """The behavioural half. With a lowered trust threshold, a file above it
    must still be SCANNED - and an infection still quarantined and the share
    revoked - even though its clean verdict would not have been trusted."""
    from app.services import av_scan as svc
    from app.utils.timeutil import utc_now

    monkeypatch.setattr(settings, "AV_MAX_SCAN_BYTES", 1_048_576)
    owner = make_user(email="lowlimit@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "mal.bin"
    path.write_bytes(b"X" * 64)
    f = File(
        id="00000000-0000-0000-0000-00000000low1",
        share_id=sh.id,
        original_filename="mal.bin",
        mime_type="application/octet-stream",
        size_bytes=200 * 1024 * 1024,  # above the lowered threshold
        storage_path=str(path),
        state=FileState.ready_unscanned,
        uploaded_by_id=owner.id,
        finalized_at=utc_now(),
    )
    db.add(f)
    db.flush()
    db.commit()

    asked: list = []

    def _scan(p):
        asked.append(p)
        return svc.ScanResult(state="infected", signature="Eicar-Test", raw="")

    monkeypatch.setattr(svc, "scan_path", _scan)

    result = await av_scan_file({}, f.id)
    assert asked, "clamd was never asked; a lowered threshold disabled scanning"
    assert result["state"] == "infected"
    db.expire_all()
    assert db.query(File).filter(File.id == f.id).one().state == FileState.infected
    assert db.query(Share).filter(Share.id == sh.id).one().state == ShareState.revoked


def test_the_trust_threshold_cannot_be_set_to_an_av_off_value():
    """`AV_MAX_SCAN_BYTES=0` was accepted silently, and 0 means "unlimited" for
    several neighbouring settings, so it is a natural thing to type. It would
    have flagged every upload as unscanned - a badge on everything conveys the
    same as a badge on nothing. `AV_SKIP` is the deliberate no-antivirus switch
    and it fails fast in production; this must not be a quiet second one."""
    from app.config import AV_MIN_SCAN_BYTES, Settings

    for bad in (0, -1, 5):
        with pytest.warns(UserWarning, match="below the floor"):
            assert Settings(AV_MAX_SCAN_BYTES=bad).AV_MAX_SCAN_BYTES == AV_MIN_SCAN_BYTES


def test_the_scan_job_may_run_as_long_as_its_socket_allows():
    """arq's default job_timeout is 300s and it CANCELS the task; the clamd
    socket ceiling is 1800s, chosen so a slow scan of a big nested archive
    produces a real verdict. The default made that ceiling unreachable, and arq
    retries a CancelledError - so all five tries burned, the file returned to
    ready_unscanned, and the sweep re-enqueued it forever. Exactly the loop the
    1800s was raised to close."""
    from app.services.av_scan import SOCKET_TIMEOUT_SEC
    from app.workers.worker import WorkerSettings

    assert getattr(WorkerSettings, "job_timeout", 300) > SOCKET_TIMEOUT_SEC


def test_the_shipped_env_example_is_within_the_ceiling():
    """Reads the real file, so the two cannot drift apart."""
    import pathlib
    import re

    for base in (pathlib.Path("/repo"), pathlib.Path(__file__).resolve().parents[2]):
        env = base / ".env.example"
        if env.exists():
            break
    m = re.search(r"^AV_MAX_SCAN_BYTES=(\d+)", env.read_text(), re.MULTILINE)
    assert m, "AV_MAX_SCAN_BYTES is not set in .env.example"
    assert int(m.group(1)) <= CLAMD_MAX_FILE_SIZE


# --- the other half of "unrecoverable": the key that is not in the backup ----


def _repo_root():
    import pathlib

    for base in (pathlib.Path("/repo"), pathlib.Path(__file__).resolve().parents[2]):
        if (base / "README.md").exists():
            return base
    raise AssertionError("README.md not found")


def test_the_manual_says_to_back_up_the_env_file():
    """`scripts/backup.sh` has carried this warning in a comment for a long
    time. README's Backups section did not mention `.env` or `JWT_SECRET` at
    all - and README is the document an operator actually reads.

    The failure it prevents is silent and total: restore onto replacement
    hardware without the old key and every Fernet field comes back intact and
    permanently undecryptable. Row counts and checksums all pass. The weekly
    restore drill cannot catch it either, because it restores on the same host
    reading the same `.env`."""
    text = (_repo_root() / "README.md").read_text()
    backups = text.split("## Backups", 1)[1].split("\n## ", 1)[0]
    assert ".env" in backups, "README's Backups section never mentions .env"
    assert "JWT_SECRET" in backups, "README's Backups section never names the key"


def test_the_backup_script_still_agrees_with_the_manual():
    """If the script ever starts capturing .env, README must stop saying it
    does not - the two drifting apart is how this happened."""
    script = (_repo_root() / "scripts/backup.sh").read_text()
    assert "JWT_SECRET" in script
    assert ".env" in script


def test_size_bytes_cannot_be_inflated_to_skip_the_scan():
    """The pre-scan branch skips AV entirely, so it is only safe if `size_bytes`
    cannot be claimed - otherwise a 10 KB EICAR declaring itself 3 GB would
    never be scanned, which the OLD code would have caught and quarantined.

    Two enforcement points make it real bytes: the tus pre-finish hook refuses
    the upload unless the final size equals the HMAC-authorised max_size, and
    that authorised size is what the row carries; the direct-upload route
    records what it actually received. Reaching the branch therefore costs a
    genuine multi-gigabyte transfer - exactly the case where clamd was never
    going to produce a verdict."""
    import inspect

    from app.routers import uploads as uploads_router
    from app.services import tus_hooks

    hooks = inspect.getsource(tus_hooks)
    assert 'actual_size != envelope["max_size"]' in hooks, (
        "tus no longer forces the final size to equal the authorised size; the "
        "pre-scan oversize branch becomes a scan-skip primitive"
    )
    assert 'file_row.size_bytes != envelope["max_size"]' in hooks

    # Direct upload must persist the RECEIVED count, never a client-declared one.
    assert "size_bytes=received" in inspect.getsource(uploads_router)
