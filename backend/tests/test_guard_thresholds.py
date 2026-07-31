"""Three guards whose enforced value differed from their stated one.

download-8  the logo transcoder set `Image.MAX_IMAGE_PIXELS = 25_000_000` and a
            comment saying 25 MP is the bound. Pillow only WARNS between the
            cap and twice the cap, raising DecompressionBombError only ABOVE
            2x - so the enforced ceiling was 50 MP, which at 4 bytes per RGBA
            pixel is ~200 MB transient in a 1 GB container. Verified against the
            installed Pillow: a 1.8x image decodes with nothing but a warning.

config-5    `get_site_timezone` caught ZoneInfoNotFoundError only. A path-shaped
            or empty key raises ValueError, and that read sits on the anonymous
            /api/config-public route the LOGIN PAGE fetches - so one malformed
            setting took the front door down with a 500 instead of falling back
            to UTC. Same widening in cron_schedule, where it would have killed
            the whole dispatcher rather than one job.

schema-7    the OIDC binding uniqueness has lived in the Phase 10 migration
            since it was written but was never declared on the model, so the
            create_all test database had no uniqueness on (provider, subject)
            at all: a duplicate pair passed every test and became a 1062 on the
            live callback.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import io

import pytest

from app.middleware.errors import AppError
from app.models.user import User
from app.services.site import DEFAULT_TIMEZONE, get_site_timezone

# --- download-8 -------------------------------------------------------------


def _png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, format="PNG")
    return buf.getvalue()


def test_an_image_between_one_and_two_times_the_cap_is_refused(monkeypatch):
    """This is the gap. Pillow would have decoded it with a warning."""
    from app.services import image as image_svc

    monkeypatch.setattr(image_svc, "_MAX_PIXELS", 1000)
    with pytest.raises(AppError) as exc:
        image_svc.to_client_png(_png(60, 30))  # 1800 px = 1.8x
    assert exc.value.code == "IMAGE_TOO_LARGE"


def test_an_ordinary_logo_still_transcodes():
    """Control: the guard must not start rejecting real logos."""
    from app.services import image as image_svc

    out = image_svc.to_client_png(_png(200, 80), max_height=48)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_stated_bound_is_the_enforced_bound():
    """The defect was a comment claiming 25 MP over code enforcing 50."""
    from app.services import image as image_svc

    assert image_svc._MAX_PIXELS == 25_000_000


# --- config-5 ---------------------------------------------------------------


@pytest.mark.parametrize("bad", ["/etc/passwd", "..", "", "Nope/Nope"])
def test_an_unusable_timezone_degrades_to_utc(db, bad):
    """The login page cannot render if this raises."""
    from app.services import settings as settings_svc

    settings_svc.set_value(db, key=settings_svc.Keys.SITE_TIMEZONE, value=bad, actor=None)
    db.commit()
    assert get_site_timezone(db) == DEFAULT_TIMEZONE


def test_a_real_timezone_is_honoured(db):
    """Control: this is an admin-set display preference and must still work."""
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.SITE_TIMEZONE, value="Europe/Vienna", actor=None
    )
    db.commit()
    assert get_site_timezone(db) == "Europe/Vienna"


def test_the_cron_dispatcher_survives_the_same_input():
    """A bad key here would have taken down every scheduled job, not one."""
    from app.services.cron_schedule import _zone

    assert str(_zone("/etc/passwd")) == "UTC"
    assert str(_zone("")) == "UTC"


# --- schema-7 ---------------------------------------------------------------


def test_the_oidc_binding_uniqueness_is_declared_on_the_model():
    constraints = {
        c.name for c in User.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_users_provider_subject" in constraints, (
        "the test schema has no uniqueness on (provider, subject), so a "
        "duplicate binding passes every test and 1062s on the live callback"
    )


def test_it_covers_the_pair_not_either_column_alone():
    """Each provider assigns its own subject namespace, so uniqueness on
    subject alone would refuse legitimate users of two IdPs."""
    con = next(
        c for c in User.__table__.constraints
        if getattr(c, "name", None) == "uq_users_provider_subject"
    )
    assert {c.name for c in con.columns} == {"oidc_provider_id", "oidc_subject"}


# --- flow-selfupdate-12: two admins, one job file ---------------------------


def test_the_update_claim_is_serialised():
    """Reading the in-flight status and writing the new job were two steps with
    nothing between them, and os.replace overwrites unconditionally. Two admins
    clicking Update in the same second both passed the check and both wrote a
    job; the second overwrote the first, and the first admin then polled a job
    id that no longer existed - JOB_NOT_FOUND forever, while an update they did
    not recognise ran."""
    import inspect

    from app.services import release_apply

    src = inspect.getsource(release_apply.apply)
    assert "with _claim_lock():" in src
    lock_at = src.index("with _claim_lock():")
    check_at = src.index("existing = _read_state()")
    assert lock_at < check_at, "the check still happens outside the lock"


def test_the_lock_path_follows_state_dir(tmp_path, monkeypatch):
    """Bound at call time, not module import: the test fixture monkeypatches
    STATE_DIR onto tmp_path, and a module constant would leave every test
    trying to write the real /state."""
    from app.services import release_apply

    monkeypatch.setattr(release_apply, "STATE_DIR", tmp_path)
    with release_apply._claim_lock():
        pass
    assert (tmp_path / ".claim.lock").exists()


def test_an_unwritable_state_dir_does_not_block_the_update(monkeypatch):
    """Best-effort by design: /state being read-only is already fatal a few
    lines later with a clearer message, so the lock must not be the thing that
    reports it."""
    from pathlib import Path

    from app.services import release_apply

    monkeypatch.setattr(release_apply, "STATE_DIR", Path("/proc/nonexistent/nope"))
    with release_apply._claim_lock():
        pass  # must not raise


# --- flow-emailchange-7: an alert that described the wrong flow -------------


def test_the_applied_alert_does_not_promise_a_verification_link():
    """This branch runs when the change is ALREADY live. Telling the old address
    that a verification link was sent implies the change is still pending and
    could be stopped, at the one moment the reader needs to act."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "templates" / "email"
    for locale in ("en", "de"):
        body = (root / locale / "email_change_alert.txt.j2").read_text(encoding="utf-8")
        assert "verification link was sent" not in body
        assert "Bestätigungslink wurde dorthin gesendet" not in body
