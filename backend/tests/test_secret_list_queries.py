"""The secret and secret-request lists cost the same per table for a page of
two rows as for a page of eight.

`GET /api/secrets` ran `secret_reveal.standing` (three SELECTs) per received
row and `_recipients` per sent row; `GET /api/secret-requests?box=mine` ran
`_targets` + `answer_of` per row; and both lists lazy-loaded the sender or
requester per row although the list query already joined that table. Fifty
rows a page was 100-250 statements (audit 2026-11-08). The lookups are batched
per page now (`secret.recipients_many` / `account_reach_many` /
`user_states_many`, `secret_reveal.standings`, `secret_request.targets_many` /
`answers_many`) and the joined person is loaded with `contains_eager`.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

from ._secret_helpers import ask, enable, group_with, h, send

PW = "Pass12345678!"


def _selects(stmts, table: str) -> int:
    return len([s for s in stmts if s.lstrip().upper().startswith("SELECT") and table in s.lower()])


async def _cost(client, counting, url, headers) -> dict[str, int]:
    counting.clear()
    r = await client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"], "the page under measurement is empty"
    tables = ("secrets", "secret_recipients", "secret_user_states", "secret_group_members",
              "secret_requests", "secret_request_targets", "users")
    return {t: _selects(counting, t) for t in tables}


@pytest.mark.asyncio
@pytest.mark.parametrize("box", ["received", "sent"])
async def test_the_secret_list_does_not_grow_with_the_page(db, make_user, login_as, client, counting, box):
    enable(db)
    sender = make_user(email="sender@test.local", role=UserRole.employee, password=PW)
    reader = make_user(email="reader@test.local", role=UserRole.employee, password=PW)
    other = make_user(email="other@test.local", role=UserRole.employee, password=PW)
    grp = group_with(db, sender, sender, reader, other)  # the sender must be a member to target it
    for i in range(8):
        # Alternate direct and via-group so both reach paths are on the page.
        if i % 2:
            send(db, sender, users=(reader,), label=f"s{i}", max_views=3)
        else:
            send(db, sender, groups=(grp,), label=f"s{i}", max_views=None)
    who = "sender@test.local" if box == "sent" else "reader@test.local"
    token, _ = await login_as(who, PW)

    two = await _cost(client, counting, f"/api/secrets?box={box}&page_size=2", h(token))
    eight = await _cost(client, counting, f"/api/secrets?box={box}&page_size=8", h(token))

    assert two == eight, f"box={box} grows with the page: {two} vs {eight}"
    assert eight["secret_recipients"] <= 2, eight
    # The actor lookup, plus the list and count statements, which join users
    # (and so match here); no per-row sender load.
    assert eight["users"] <= 3, eight


@pytest.mark.asyncio
@pytest.mark.parametrize("box", ["mine", "asked"])
async def test_the_request_list_does_not_grow_with_the_page(db, make_user, login_as, client, counting, box):
    enable(db)
    requester = make_user(email="req@test.local", role=UserRole.employee, password=PW)
    target = make_user(email="target@test.local", role=UserRole.employee, password=PW)
    for i in range(8):
        ask(db, requester, users=(target,), label=f"r{i}")
    who = "req@test.local" if box == "mine" else "target@test.local"
    token, _ = await login_as(who, PW)

    two = await _cost(client, counting, f"/api/secret-requests?box={box}&page_size=2", h(token))
    eight = await _cost(client, counting, f"/api/secret-requests?box={box}&page_size=8", h(token))

    assert two == eight, f"box={box} grows with the page: {two} vs {eight}"
    assert eight["secret_request_targets"] <= 2, eight
    assert eight["users"] <= 3, eight  # actor lookup + the list and count statements' join
