"""Every operational alert has words of its own in the bell, in both languages.

`admin_alerts.OPS_REASONS` names every `reason` an ops alert is sent with; the
bell picks `notif_bell.headline.ops_alert.<reason>` and falls back to the
generic line. Until 2026-11-21 nothing tied the two together, and ten of the
sixteen reasons - ClamAV down, Redis down, mail failing, a task that keeps
failing, both storage alarms - read "Operational alert." and nothing else.

Reads both sides. The other direction - a reason sent but not registered - is
caught at run time: conftest turns `STRICT_REASONS` on, so any test that sends
an unregistered reason fails.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.models.user import UserRole
from app.services import admin_alerts

LOCALES = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"


def _headlines(locale: str) -> dict[str, str]:
    data = json.loads((LOCALES / f"{locale}.json").read_text())
    return data["notif_bell"]["headline"]["ops_alert"]


@pytest.mark.parametrize("locale", ["en", "de"])
def test_the_bell_words_every_reason_and_no_other(locale):
    words = _headlines(locale)
    assert set(words) - {"generic"} == set(admin_alerts.OPS_REASONS)
    assert all(v.strip() for v in words.values())
    # A reason's own line says more than the fallback.
    assert not [k for k, v in words.items() if k != "generic" and v == words["generic"]]


def test_an_unregistered_reason_fails_in_tests_and_is_still_sent_in_production(
    db, make_user, monkeypatch
):
    make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(ValueError, match="OPS_REASONS"):
        admin_alerts.notify_admins(db, payload={"reason": "brand_new_alarm"})

    monkeypatch.setattr(admin_alerts, "STRICT_REASONS", False)
    assert admin_alerts.notify_admins(db, payload={"reason": "brand_new_alarm"}).sent == 1


def test_one_alert_an_hour_per_reason(db, make_user):
    make_user(email="admin@test.local", role=UserRole.admin)
    sent = [
        admin_alerts.ops_alert(db, reason="av_scan_failed", detail=f"file {i}").sent
        for i in range(3)
    ]
    assert sent == [1, 0, 0]
    # Another reason has its own hour.
    assert admin_alerts.ops_alert(db, reason="av_unhealthy", detail="ping").sent == 1
