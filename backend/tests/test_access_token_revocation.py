"""Revoking sessions must invalidate the credential actually being presented.

Access JWTs are stateless and were checked only for signature/exp/type plus a
live `is_disabled` lookup. So every "revoke" path - logout-others, password
change, password reset, admin revoke-all, refresh-reuse detection,
config-backup import - dropped the REFRESH row and left the stolen access token
working for its full TTL. `users.sessions_invalidated_at` closes that.

The two tests that matter pull in opposite directions, which is why both are
here: a revoke must kill an OLD token, and must NOT kill the token minted by the
same request that did the revoking.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"


@pytest.mark.asyncio
async def test_revoking_sessions_kills_an_existing_access_token(
    make_user, db, client, login_as
):
    user = make_user(email="u@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("u@test.local", PW)
    headers = {"Authorization": f"Bearer {token}"}

    # The token works before the revoke.
    assert (await client.get("/api/account/me", headers=headers)).status_code == 200

    from datetime import timedelta

    from app.services import jwt_session
    from app.utils.timeutil import utc_now

    jwt_session.revoke_all_user_refresh_tokens(db, user.id)
    # `iat` is whole seconds and the mark is compared with `<`, so a token
    # minted in the SAME second as the revoke deliberately survives (see
    # `resolve_user_from_access_token`, and the window test below). A real
    # revoke seconds after a sign-in is the case that matters, so advance the
    # mark past the token's second rather than sleeping a real second here.
    db.refresh(user)
    user.sessions_invalidated_at = utc_now() + timedelta(seconds=2)
    db.commit()

    resp = await client.get("/api/account/me", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_the_same_second_window_is_a_known_property(
    make_user, db, client, login_as
):
    """Pins the one gap the second-granularity comparison leaves: a token minted
    in the same wall-clock second as the revoke still works. It exists so that
    `change_password`, which revokes and re-mints in one request, does not sign
    the user out - see the test above. Documented here so a future tightening to
    `<=` is a deliberate decision with a visible cost, not a silent one."""
    import datetime

    import jwt as pyjwt

    from app.config import settings as app_settings

    user = make_user(email="u@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("u@test.local", PW)

    # Pin the mark to EXACTLY the token's own second rather than calling
    # revoke_all_user_refresh_tokens and hoping the two land in the same one -
    # that raced, and a test for a one-second window must not itself depend on
    # winning a one-second race.
    iat = pyjwt.decode(
        token, app_settings.JWT_SECRET, algorithms=[app_settings.JWT_ALGORITHM]
    )["iat"]
    user.sessions_invalidated_at = datetime.datetime.fromtimestamp(
        iat, tz=datetime.timezone.utc
    ).replace(tzinfo=None)
    db.commit()

    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, (
        "if this now 401s, the same-second window was closed - check that "
        "change_password still leaves the user signed in"
    )


@pytest.mark.asyncio
async def test_changing_your_password_does_not_lock_you_out(
    make_user, client, login_as
):
    """`change_password` revokes and re-mints inside ONE request. A mark
    compared at sub-second precision would reject the token it just issued, so
    the user would change their password and be signed straight out - which is
    exactly the kind of regression a naive tightening of the comparison
    introduces."""
    make_user(email="u@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("u@test.local", PW)

    resp = await client.post(
        "/api/account/change-password",
        json={"current_password": PW, "new_password": "BrandNewPass9876!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 204), resp.text

    # Whatever token the response hands back must actually work.
    new_token = (resp.json() or {}).get("access_token") if resp.status_code == 200 else None
    if new_token:
        me = await client.get(
            "/api/account/me", headers={"Authorization": f"Bearer {new_token}"}
        )
        assert me.status_code == 200, me.text


@pytest.mark.asyncio
async def test_a_token_minted_after_the_revoke_is_accepted(
    make_user, db, client, login_as
):
    """The mark is a high-water line, not a ban: signing in again must work."""
    user = make_user(email="u@test.local", role=UserRole.employee, password=PW)
    from app.services import jwt_session

    jwt_session.revoke_all_user_refresh_tokens(db, user.id)
    db.commit()

    token, _ = await login_as("u@test.local", PW)
    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
