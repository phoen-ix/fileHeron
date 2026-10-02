"""The rules behind the secret forms: blockers, presets, addresses, generator.

Pure Python (``secret_rules`` imports no Tk), so the Linux leg covers what the
compose views decide. Each blocker is asserted by its i18n key - the view shows
exactly that list and disables Send while it is non-empty.
"""
from __future__ import annotations

import random

import pytest

from fileheron_client import secret_rules as r
from fileheron_client.models import MeResponse, SecretLimitsResponse

LIMITS = SecretLimitsResponse(max_views=5, max_expiry_days=30, max_lifetime_days=90,
                              passphrase_failure_mode="lock", passphrase_max_failures=10)


def keys(blockers):
    return [k for k, _ in blockers]


def ok_secret(**over) -> r.SecretForm:
    form = r.SecretForm(content="hunter2", has_picked=True)
    for k, v in over.items():
        setattr(form, k, v)
    return form


def ok_request(**over) -> r.RequestForm:
    form = r.RequestForm(label="Router password", has_picked=True)
    for k, v in over.items():
        setattr(form, k, v)
    return form


# ---- presets ----------------------------------------------------------------------


def test_expiry_presets_stop_at_the_admin_ceiling_and_end_with_never():
    assert [k for k, _ in r.secret_expiry_presets(LIMITS)] == ["1h", "1d", "7d", "30d", "never"]
    assert r.secret_expiry_presets(LIMITS)[-1] == ("never", None)


def test_a_one_day_ceiling_still_offers_an_hour_and_a_day():
    tight = LIMITS.model_copy(update={"max_expiry_days": 1})
    assert [k for k, _ in r.secret_expiry_presets(tight)] == ["1h", "1d", "never"]
    assert r.default_preset(r.secret_expiry_presets(tight)) == "1d"
    assert [k for k, _ in r.request_open_presets(tight)] == ["1d"]


def test_a_request_always_closes():
    assert all(s is not None for _, s in r.request_open_presets(LIMITS))
    assert r.default_preset(r.request_open_presets(LIMITS)) == "7d"


def test_answer_lifetime_offers_no_time_limit_last():
    opts = r.answer_lifetime_options(LIMITS)
    assert opts[-1] == ("never", None)
    assert r.preset_seconds(opts, "7d") == 7 * 86400


def test_scope_choices_follow_the_picks():
    assert r.scope_options(False) == ["per_person", "total"]
    assert r.scope_options(True) == ["per_person", "per_recipient", "total"]


def test_limits_fall_back_when_me_has_none():
    me = MeResponse.model_validate({"id": 1, "email": "a@x.io", "display_name": "A",
                                    "role": "employee", "locale": "en"})
    assert r.limits_of(me) is r.DEFAULT_LIMITS


# ---- addresses ----------------------------------------------------------------------


def test_addresses_split_on_commas_semicolons_spaces_and_lines():
    valid, invalid = r.parse_addresses("a@example.com, b@example.com;c@example.com\nd@example.com")
    assert valid == ["a@example.com", "b@example.com", "c@example.com", "d@example.com"]
    assert invalid == []


def test_addresses_are_deduplicated_case_insensitively_and_kept_as_typed():
    valid, _ = r.parse_addresses("Guest@Example.com guest@example.com")
    assert valid == ["Guest@Example.com"]


def test_invalid_addresses_are_reported():
    valid, invalid = r.parse_addresses("ok@example.com, nope, @x.io, a@b")
    assert valid == ["ok@example.com"]
    assert invalid == ["nope", "@x.io", "a@b"]


# ---- secret blockers ------------------------------------------------------------------


def test_a_complete_form_has_no_blockers():
    assert r.secret_blockers(ok_secret(), LIMITS) == []


@pytest.mark.parametrize(("over", "key"), [
    ({"content": ""}, "secrets.blockers.no_content"),
    ({"content": "x" * (r.MAX_CONTENT + 1)}, "secrets.blockers.too_long"),
    ({"label": "x" * (r.MAX_LABEL + 1)}, "secrets.blockers.label_too_long"),
    ({"has_picked": False}, "secrets.blockers.no_recipient"),
    ({"has_picked": False, "can_external": True}, "secrets.blockers.no_recipient_or_link"),
    ({"addresses": "nope", "can_external": True}, "secrets.blockers.address_invalid"),
    ({"addresses": ",".join(f"u{i}@example.com" for i in range(21)), "can_external": True},
     "secrets.blockers.too_many_addresses"),
    ({"limit_views": False, "expiry_key": "never"}, "secrets.blockers.no_limit"),
    ({"max_views": "0"}, "secrets.blockers.views_invalid"),
    ({"max_views": "two"}, "secrets.blockers.views_invalid"),
    ({"max_views": "6"}, "secrets.blockers.views_too_many"),
    ({"passphrase": "short", "passphrase_repeat": "short"}, "secrets.blockers.passphrase_short"),
    ({"passphrase": "long enough", "passphrase_repeat": "different!"},
     "secrets.blockers.passphrase_mismatch"),
])
def test_each_secret_blocker(over, key):
    assert key in keys(r.secret_blockers(ok_secret(**over), LIMITS))


def test_an_address_or_a_link_is_enough_when_allowed():
    assert r.secret_blockers(ok_secret(has_picked=False, can_external=True,
                                       addresses="a@example.com"), LIMITS) == []
    assert r.secret_blockers(ok_secret(has_picked=False, can_external=True,
                                       create_link=True), LIMITS) == []


def test_addresses_and_links_count_for_nothing_without_the_permission():
    form = ok_secret(has_picked=False, can_external=False, addresses="a@example.com",
                     create_link=True)
    assert keys(r.secret_blockers(form, LIMITS)) == ["secrets.blockers.no_recipient"]


def test_never_is_fine_with_a_view_limit_and_views_off_is_fine_with_an_expiry():
    assert r.secret_blockers(ok_secret(expiry_key="never"), LIMITS) == []
    assert r.secret_blockers(ok_secret(limit_views=False, max_views="garbage"), LIMITS) == []


def test_the_blocker_arguments_fill_the_message():
    (key, kwargs), = r.secret_blockers(ok_secret(max_views="6"), LIMITS)
    assert key == "secrets.blockers.views_too_many" and kwargs == {"max": 5}


def test_views_value():
    assert r.views_value(True, " 3 ") == 3
    assert r.views_value(False, "3") is None


# ---- request + answer blockers -----------------------------------------------------------


def test_a_complete_request_has_no_blockers():
    assert r.request_blockers(ok_request(), LIMITS) == []


@pytest.mark.parametrize(("over", "key"), [
    ({"label": "   "}, "secrets.blockers.no_label"),
    ({"label": "x" * (r.MAX_LABEL + 1)}, "secrets.blockers.label_too_long"),
    ({"has_picked": False}, "secrets.blockers.no_target"),
    ({"has_picked": False, "can_external": True}, "secrets.blockers.no_target_or_link"),
    ({"limit_views": False, "lifetime_key": "never"}, "secrets.blockers.no_answer_limit"),
    ({"max_views": "99"}, "secrets.blockers.views_too_many"),
    ({"passphrase": "short", "passphrase_repeat": "short"}, "secrets.blockers.passphrase_short"),
    ({"passphrase": "long enough", "passphrase_repeat": "nope nope"},
     "secrets.blockers.passphrase_mismatch"),
])
def test_each_request_blocker(over, key):
    assert key in keys(r.request_blockers(ok_request(**over), LIMITS))


def test_answer_blockers():
    assert r.answer_blockers("s3cret", "", "") == []
    assert keys(r.answer_blockers("", "", "")) == ["secrets.blockers.no_content"]
    assert keys(r.answer_blockers("x", "long enough", "other one")) == [
        "secrets.blockers.passphrase_mismatch"]


# ---- generator --------------------------------------------------------------------------


def test_the_generator_uses_every_class_and_no_lookalikes():
    for _ in range(200):
        pw = r.generate_password()
        assert len(pw) == r.PASSWORD_LENGTH
        assert not set(pw) & set("0O1lI|`'\"")
        for cls in r.PASSWORD_CLASSES:
            assert any(c in cls for c in pw), (pw, cls)


def test_the_generator_clamps_its_length():
    assert len(r.generate_password(2)) == r.MIN_PASSPHRASE
    assert len(r.generate_password(500)) == 64


def test_the_generator_redraws_rather_than_planting_a_class():
    # A source that only ever offers lowercase on the first draw: the result
    # must still contain every class, which only a redraw can achieve.
    class Scripted(random.Random):
        calls = 0

        def choice(self, seq):
            Scripted.calls += 1
            return "a" if Scripted.calls <= r.PASSWORD_LENGTH else super().choice(seq)

    pw = r.generate_password(rng=Scripted(1))
    assert Scripted.calls > r.PASSWORD_LENGTH
    assert all(any(c in cls for c in pw) for cls in r.PASSWORD_CLASSES)


def test_the_app_draws_from_the_os_csprng():
    import secrets

    assert isinstance(r._SYSTEM_RANDOM, secrets.SystemRandom)
