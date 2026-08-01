"""A cron that fails repeatedly must reach an operator.

Found live on a production instance during audit #2: `imap_poll` had failed 12
times in one hour. It was recorded correctly - twelve `cron_runs` rows with
status `failure`, and twelve `error_log` rows with `code=CRON_FAILED`. Nobody
was told. The only way an operator would learn of it was by opening
`/admin/scheduled-tasks` or the error log unprompted.

`ops_check` is the operator-alerting cron, and its docstring said it runs "after
the other crons have had a chance to fire (**and the cron_tracker has recorded
any of their failures**)" - which reads as though those failures were consumed
there. They were not. It alerted on exactly three reasons: `av_unhealthy`,
`redis_unhealthy`, `smtp_failing`. Grepping the whole backend for
consecutive-failure detection returned only the login lockout counter and the
public-link brute-force counter.

So the "no operator alerting" gap this worker exists to close was only partly
closed, under a docstring implying otherwise - this repo's signature failure,
applied to its own alerting.

A threshold rather than "any failure" is deliberate: one transient failure is
what the retry and the next scheduled run are for, and alerting on it would
train operators to ignore the alert.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.cron_run import CronRun, CronRunStatus
from app.models.user import UserRole
from app.utils.timeutil import utc_now
from app.workers import ops_check as ops


@pytest.fixture
def quiet_deps(monkeypatch):
    """Silence the three checks that already alert, so a dispatch in a test can
    only have come from the cron check."""
    monkeypatch.setattr(ops, "_check_av", lambda _db: None)
    monkeypatch.setattr(ops, "_check_redis", lambda: None)
    monkeypatch.setattr(ops, "_check_smtp", lambda _db: None)
    monkeypatch.setattr(ops, "_dedup_seen", lambda _reason: False)


def _fail(db, job: str, n: int, *, minutes_ago: int = 30) -> None:
    for i in range(n):
        db.add(
            CronRun(
                job_name=job,
                status=CronRunStatus.failure,
                started_at=utc_now() - timedelta(minutes=minutes_ago + i),
            )
        )
    db.flush()


@pytest.mark.asyncio
async def test_a_repeatedly_failing_cron_alerts_an_admin(
    db, make_user, quiet_deps, monkeypatch
):
    """The live case: twelve failures of one job inside the lookback."""
    make_user(email="admin@test.local", role=UserRole.admin)
    _fail(db, "imap_poll", 12)
    db.commit()

    sent: list = []
    monkeypatch.setattr(
        ops, "dispatch", lambda db, *, user, category, payload, **kw: sent.append(payload)
    )

    result = await ops.ops_check({})
    assert sent, "a cron failed 12 times in an hour and nobody was told"
    assert any(p.get("reason") == "cron_failing" for p in sent)
    assert "imap_poll" in sent[0]["detail"]
    assert result["crons"] != "ok"


@pytest.mark.asyncio
async def test_one_transient_failure_does_not_alert(
    db, make_user, quiet_deps, monkeypatch
):
    """The control, and the reason this is a threshold. A single failure is what
    the retry exists for; alerting on it teaches operators to ignore alerts."""
    make_user(email="admin@test.local", role=UserRole.admin)
    _fail(db, "expire_files", 1)
    db.commit()

    sent: list = []
    monkeypatch.setattr(
        ops, "dispatch", lambda db, *, user, category, payload, **kw: sent.append(payload)
    )

    result = await ops.ops_check({})
    assert sent == []
    assert result["crons"] == "ok"


@pytest.mark.asyncio
async def test_old_failures_fall_out_of_the_window(
    db, make_user, quiet_deps, monkeypatch
):
    """A cron that failed yesterday and recovered must not alert forever."""
    make_user(email="admin@test.local", role=UserRole.admin)
    _fail(db, "imap_poll", 12, minutes_ago=60 * 24)
    db.commit()

    sent: list = []
    monkeypatch.setattr(
        ops, "dispatch", lambda db, *, user, category, payload, **kw: sent.append(payload)
    )

    result = await ops.ops_check({})
    assert sent == []
    assert result["crons"] == "ok"


@pytest.mark.asyncio
async def test_successful_runs_are_not_counted(db, make_user, quiet_deps, monkeypatch):
    """`imap_poll` on the live instance had 200 success rows alongside its 12
    failures - the successes are runs where the feature gate returned early.
    Counting them would have masked the failures."""
    make_user(email="admin@test.local", role=UserRole.admin)
    for i in range(50):
        db.add(
            CronRun(
                job_name="imap_poll",
                status=CronRunStatus.success,
                started_at=utc_now() - timedelta(minutes=10 + i),
            )
        )
    db.commit()

    sent: list = []
    monkeypatch.setattr(
        ops, "dispatch", lambda db, *, user, category, payload, **kw: sent.append(payload)
    )
    result = await ops.ops_check({})
    assert sent == []
    assert result["crons"] == "ok"


def test_the_docstring_no_longer_implies_coverage_it_lacks():
    """The sentence that made this invisible for as long as it was: ops_check
    said it ran after the other crons "and the cron_tracker has recorded any of
    their failures", which reads as consumption. Now it is consumption."""
    import inspect

    src = inspect.getsource(ops)
    assert "Crons that are failing repeatedly" in src
    assert "_check_failing_crons" in src
    # and it must actually be wired into the run, not merely defined
    body = inspect.getsource(ops.ops_check)
    assert "_check_failing_crons" in body
    assert 'reason="cron_failing"' in body
