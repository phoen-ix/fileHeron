"""The two request-boundary contracts nothing asserted.

Both are narrow on purpose. The broad version of this claim - "the schemas
package has no systematic test" - does not survive contact with the code: 36
`422` assertions across 23 files already cover `min_length`/`max_length`/
`pattern`, two tests introspect model fields directly, and re-asserting that a
constraint declared in a model is present in that model is tautological.

What is NOT covered is the two places where the DEFAULT is the wrong one:

1. `APIBaseModel` sets no `extra=`, so Pydantic's `extra="ignore"` applies - and
   that is DELIBERATE. Clients are versioned separately from the backend, so
   `forbid` on the shared base would 422 every client one release behind;
   CLAUDE.md records it and `test_admin_scan_guard.py` pins it. Four models opt
   OUT of that, and nothing checked the opt-out works. Three are settings PATCHes
   with "absent means leave alone" semantics, where a typo'd key silently doing
   nothing is exactly the failure `forbid` exists to prevent.

2. `EmailLike`'s `max_length=254` exists because "a longer address passed
   validation and then died at flush() as an unhandled DataError - a 500 with a
   stack trace and an error_log row where the caller should have got a clean 422
   at the boundary". The conftest width guard now catches the WRITE, but as an
   AssertionError from the harness - not as the 422 the caller is owed.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.user import UserRole
from app.schemas.branding_settings import UpdateBrandingSettingsRequest
from app.schemas.quarantine import QuarantineActionRequest, UpdateQuarantineSettingsRequest
from app.schemas.site_settings import UpdateSiteSettingsRequest

_FORBID_MODELS = [
    UpdateBrandingSettingsRequest,
    QuarantineActionRequest,
    UpdateQuarantineSettingsRequest,
    UpdateSiteSettingsRequest,
]


@pytest.mark.parametrize("model", _FORBID_MODELS, ids=lambda m: m.__name__)
def test_a_forbid_model_rejects_an_unknown_key(model):
    """A typo'd field on a settings PATCH must 422, not silently do nothing."""
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"definitely_not_a_field": "x"})
    assert any(e["type"] == "extra_forbidden" for e in exc.value.errors()), exc.value.errors()


@pytest.mark.parametrize("model", _FORBID_MODELS, ids=lambda m: m.__name__)
def test_the_opt_out_is_actually_declared(model):
    """Guards the mechanism rather than one symptom: flipping `extra` back to
    the inherited default would make the test above pass for the wrong reason
    on a model whose every field happens to be optional."""
    assert model.model_config.get("extra") == "forbid"


def test_the_shared_base_still_ignores_extras():
    """The other side of the same contract, and the reason it must NOT be
    forbid: a client one release behind still sends a field the backend has
    removed, and must be ignored rather than 422'd."""
    from app.schemas.common import APIBaseModel

    assert APIBaseModel.model_config.get("extra") in (None, "ignore")


@pytest.mark.asyncio
async def test_an_overlong_email_is_a_422_not_a_500(client, make_user, login_as):
    """254 chars is the column width. Past it the request must be refused at the
    boundary; the failure this replaced was a DataError at flush()."""
    make_user(email="boss@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss@test.local", "TestPassword123!")

    r = await client.post(
        "/api/account/invite",
        json={
            "email": "a" * 250 + "@test.local",
            "display_name_hint": "Too Long",
            "initial_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, f"expected a boundary refusal, got {r.status_code}: {r.text}"
    # For the RIGHT reason: an earlier version of this test omitted a required
    # field and got a 422 about THAT, which would pass even with the bound gone.
    locs = [tuple(e.get("loc", [])) for e in r.json()["detail"]]
    assert any("email" in loc for loc in locs), locs


@pytest.mark.asyncio
async def test_an_email_at_the_limit_is_accepted(client, make_user, login_as):
    """The control: the bound must be 254, not 'refuse anything long'."""
    make_user(email="boss2@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss2@test.local", "TestPassword123!")

    addr = "b" * (254 - len("@test.local")) + "@test.local"
    assert len(addr) == 254
    r = await client.post(
        "/api/account/invite",
        json={
            "email": addr,
            "display_name_hint": "At The Limit",
            "initial_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code != 422, f"a 254-char address was refused: {r.text}"
