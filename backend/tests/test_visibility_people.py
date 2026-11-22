"""Whom a person can find, address and see named - asked at the routes.

The rules: a client finds the staff they are connected to and no one else,
never another client; an employee finds every colleague and the clients
connected to them; group names and the other recipients of a share are shown
only to those who sent it, decide on it or administer the instance. Most of
this was tested by calling the search function or reading connection rows
(audit 2026-11-22); `_visibility_world.py` says why that is not enough.
"""
from __future__ import annotations

import pytest

from app.models.file import FileApprovalState
from app.models.share import ShareState
from app.services import settings as settings_svc

from ._visibility_world import ADMIN, CLIENT, EMPLOYEE, World

K = settings_svc.Keys


@pytest.fixture
def world(db, client, monkeypatch, tmp_path) -> World:
    return World(db, client, monkeypatch, tmp_path)


@pytest.fixture
def org(world):
    """Two employees with a client each, a client nobody is connected to, and
    two accounts that must never turn up anywhere."""
    w = world
    w.person("admin", ADMIN)
    w.person("anna", EMPLOYEE)
    w.person("ben", EMPLOYEE)
    w.person("carl", CLIENT)
    w.person("dora", CLIENT)
    w.person("emil", CLIENT)
    w.person("fired", EMPLOYEE, disabled=True)
    gone = w.person("gone", CLIENT, disabled=True)
    gone.display_name = "[erased]"
    w.db.commit()
    w.connect(w["carl"], w["anna"])
    w.connect(w["dora"], w["ben"])
    return w


async def _found(w: World, who: str, q: str = "") -> set[str]:
    r = await w.get(who, "/api/users/search", params={"q": q})
    assert r.status_code == 200, r.text
    return {item["display_name"] for item in r.json()["items"]}


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_search_finds_only_the_people_one_may_address(org):
    w = org
    assert {who: await _found(w, who) for who in ("carl", "emil", "anna", "ben", "admin")} == {
        # A client: the staff connected to them. Never another client, never
        # an admin or employee they have nothing to do with.
        "carl": {"Anna"},
        "emil": set(),
        # An employee: every colleague, and their own clients only.
        "anna": {"Admin", "Ben", "Carl"},
        "ben": {"Admin", "Anna", "Dora"},
        # An admin: everyone who has an active account.
        "admin": {"Anna", "Ben", "Carl", "Dora", "Emil"},
    }
    # Typing a name one may not see finds nothing - it is not a way to check
    # that the account exists.
    assert await _found(w, "carl", "dora") == set()
    assert await _found(w, "carl", "ben@test.local") == set()
    assert await _found(w, "anna", "emil") == set()
    assert await _found(w, "admin", "fired") == set()
    assert await _found(w, "admin", "erased") == set()

    hit = (await w.get("carl", "/api/users/search", params={"q": "ann"})).json()["items"]
    assert hit == [
        {"user_id": w["anna"].id, "display_name": "Anna", "email": "anna@test.local", "role": "employee"}
    ]


@pytest.mark.asyncio
async def test_my_connections_are_mine_and_leave_out_closed_accounts(org):
    w = org

    async def connections(who: str) -> list[tuple[str, str]]:
        items = (await w.get(who, "/api/users/me/connections")).json()["items"]
        return sorted((i["display_name"], i["role"]) for i in items)

    assert await connections("carl") == [("Anna", "employee")]
    assert await connections("anna") == [("Carl", "client")]
    assert await connections("emil") == []
    assert await connections("admin") == []

    w["anna"].is_disabled = True
    w.db.commit()
    assert await connections("carl") == []


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_group_is_named_to_its_members_its_addressers_and_admins(org):
    w = org
    admin = w["admin"]
    project = w.group("Project", w["anna"], w["carl"], by=admin)
    w.group("Front desk", w["ben"], by=admin, inbox=True)
    w.group("Board", admin, by=admin)

    async def names(who: str, url: str) -> set[str]:
        r = await w.get(who, url)
        assert r.status_code == 200, r.text
        return {g["name"] for g in r.json()["items"]}

    everyone = ("anna", "ben", "carl", "emil", "admin")
    addressable = {who: await names(who, "/api/groups/recipient-targets") for who in everyone}
    assert addressable == {
        # Staff: their own groups, and every company inbox.
        "anna": {"Project", "Front desk"},
        "ben": {"Front desk"},
        # A client: company inboxes only - not even their own project group.
        "carl": {"Front desk"},
        "emil": {"Front desk"},
        "admin": {"Project", "Front desk", "Board"},
    }
    mine = {who: await names(who, "/api/groups/mine") for who in everyone}
    assert mine == {
        "anna": {"Project"},
        "ben": {"Front desk"},
        "carl": {"Project"},
        "emil": set(),
        "admin": {"Board"},
    }

    # Who is IN a group is the admin's to see: the member list carries every
    # member's address and role.
    for who in ("anna", "carl"):
        assert await w.code(who, "/api/groups") == "FORBIDDEN", who
        assert await w.code(who, f"/api/groups/{project.id}") == "FORBIDDEN", who
    members = (await w.get("admin", f"/api/groups/{project.id}")).json()["members"]
    assert {m["email"] for m in members} == {"anna@test.local", "carl@test.local"}


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_share_can_be_addressed_only_to_people_and_groups_one_may_reach(org):
    w = org
    admin = w["admin"]
    project = w.group("Project", w["anna"], w["carl"], by=admin)
    desk = w.group("Front desk", w["ben"], by=admin, inbox=True)
    board = w.group("Board", admin, by=admin)

    async def send(who: str, *, users=(), groups=()) -> str | int:
        r = await w.post(
            who,
            "/api/shares",
            json={
                "kind": "outbound",
                "recipients": {"user_ids": [w[u].id for u in users], "group_ids": [g.id for g in groups]},
                "expires_at": None,
            },
        )
        return r.json()["code"] if r.status_code >= 400 else r.status_code

    assert {
        "her own client": await send("anna", users=("carl",)),
        "a colleague": await send("anna", users=("ben",)),
        "someone else's client": await send("anna", users=("dora",)),
        "a client nobody is connected to": await send("anna", users=("emil",)),
        "her own group": await send("anna", groups=(project,)),
        "a company inbox she is not in": await send("anna", groups=(desk,)),
        "a group she is not in": await send("anna", groups=(board,)),
        "an admin, to anyone": await send("admin", users=("emil",), groups=(board,)),
    } == {
        "her own client": 201,
        "a colleague": 201,
        "someone else's client": "RECIPIENT_NOT_CONNECTED",
        "a client nobody is connected to": "RECIPIENT_NOT_CONNECTED",
        "her own group": 201,
        "a company inbox she is not in": 201,
        "a group she is not in": "GROUP_NOT_MEMBER",
        "an admin, to anyone": 201,
    }

    # A client names no recipients: whatever they send goes to the company.
    r = await w.post(
        "carl",
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [w["dora"].id], "group_ids": [board.id]},
            "expires_at": None,
        },
    )
    assert r.status_code == 201, r.text
    made = r.json()
    assert (made["kind"], made["recipient_user_ids"], made["recipient_groups"]) == ("inbound", [], [])
    assert made["id"] not in [row["id"] for row in await w.listed("dora")]


# ---------------------------------------------------------------------------
# The other recipients of a share
# ---------------------------------------------------------------------------


def _roster(page: dict) -> dict:
    return {
        "ids": page["recipient_user_ids"],
        "people": [p["label"] for p in page["recipient_users"]],
        "groups": [g["name"] for g in page["recipient_groups"]],
    }


@pytest.mark.asyncio
async def test_a_recipient_sees_neither_the_other_people_nor_a_group_they_are_not_in(org):
    """Group names are often customer names. The page and the Received list
    filter them like the people - only the approvals queue was tested."""
    w = org
    admin = w["admin"]
    west = w.group("Clients West", w["dora"], by=admin)
    share = w.share(w["anna"], to=(w["carl"],), groups=(west,), subject="Price list")
    w.file(share)

    async def page(who: str) -> dict:
        return (await w.get(who, f"/api/shares/{share.id}")).json()

    carl = w["carl"].id
    full = {"ids": [carl], "people": ["Carl"], "groups": ["Clients West"]}
    assert {who: _roster(await page(who)) for who in ("anna", "admin", "carl", "dora")} == {
        "anna": full,
        "admin": full,
        "carl": {"ids": [carl], "people": [], "groups": []},
        "dora": {"ids": [], "people": [], "groups": ["Clients West"]},
    }
    assert "Clients West" not in str(await page("carl"))
    assert "Carl" not in str(await page("dora"))

    async def row(who: str, box: str = "inbox") -> list[tuple[str, str]]:
        (hit,) = [r for r in await w.listed(who, box) if r["id"] == share.id]
        return sorted((r["kind"], r["label"]) for r in hit["recipients"])

    assert await row("carl") == [("user", "Carl")]
    assert await row("dora") == [("group", "Clients West")]
    assert await row("anna", "outbox") == [("group", "Clients West"), ("user", "Carl")]


@pytest.mark.asyncio
async def test_an_approver_sees_the_roster_of_what_they_must_approve_and_no_other(org):
    w = org
    admin = w["admin"]
    w.setting(K.SHARE_APPROVAL_ENABLED, "true")
    w.setting(K.SHARE_APPROVAL_APPROVER_MODE, "admins_only")
    w.setting(K.SHARE_APPROVAL_SCOPE, "all")
    w.setting(K.SHARE_APPROVAL_APPROVER_USERS, str([w["ben"].id]))
    west = w.group("Clients West", w["dora"], by=admin)
    held = w.share(
        w["anna"], to=(w["carl"],), groups=(west,), state=ShareState.pending_approval, approval_was_required=True
    )
    w.file(held)
    live = w.share(w["anna"], to=(w["carl"],), groups=(west,), approval_was_required=True)
    w.file(live, "approved.txt")
    w.file(live, "added.txt", approval=FileApprovalState.pending_review)

    async def roster(share) -> dict:
        r = await w.get("ben", f"/api/shares/{share.id}")
        assert r.status_code == 200, r.text
        return _roster(r.json())

    # Deciding whether a share may go out means seeing where it goes.
    assert await roster(held) == {"ids": [w["carl"].id], "people": ["Carl"], "groups": ["Clients West"]}
    # A file added to a share that is already out: the approver decides on the
    # file, and the roster of the live share is not theirs to read.
    assert await roster(live) == {"ids": [], "people": [], "groups": []}


@pytest.mark.asyncio
async def test_the_received_filters_do_not_reach_past_what_is_mine(org):
    w = org
    admin = w["admin"]
    project = w.group("Project", w["anna"], w["carl"], by=admin)
    west = w.group("Clients West", w["dora"], by=admin)
    to_project = w.share(w["ben"], groups=(project,), subject="For the project")
    to_west = w.share(w["ben"], groups=(west,), subject="For West")
    direct = w.share(w["anna"], to=(w["carl"],), subject="For Carl")
    for s in (to_project, to_west, direct):
        w.file(s)

    async def ids(who: str, **params) -> set[str]:
        return {r["id"] for r in await w.listed(who, **params)}

    assert await ids("carl") == {to_project.id, direct.id}
    assert await ids("carl", via_group_id=project.id) == {to_project.id}
    # A group I am not in: nothing - not "the shares sent to that group".
    assert await ids("carl", via_group_id=west.id) == set()
    assert await ids("carl", sender_user_id=w["ben"].id) == {to_project.id}
    assert await ids("emil", sender_user_id=w["ben"].id) == set()
    assert await ids("emil", via_group_id=west.id) == set()


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_secret_is_addressed_like_a_share_and_its_roster_is_the_senders(org):
    from datetime import timedelta

    from app.utils.timeutil import utc_now

    from ._secret_helpers import send

    w = org
    w.setting(K.SECRETS_ENABLED, "true")
    desk = w.group("Front desk", w["ben"], w["dora"], by=w["admin"], inbox=True)

    async def post(who: str, *, users=(), groups=()) -> str | int:
        r = await w.post(
            who,
            "/api/secrets",
            json={
                "content": "correct horse battery staple",
                "label": "VPN",
                "max_views": 1,
                "view_scope": "per_person",
                "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
                "recipients": {
                    "user_ids": [w[u].id for u in users],
                    "group_ids": [g.id for g in groups],
                    "emails": [],
                },
                "create_link": False,
            },
        )
        return r.json()["code"] if r.status_code >= 400 else r.status_code

    assert {
        "a client to their employee": await post("carl", users=("anna",)),
        "a client to another client": await post("carl", users=("dora",)),
        "a client to someone else's employee": await post("carl", users=("ben",)),
        "a client to a group": await post("carl", groups=(desk,)),
        "an employee to someone else's client": await post("anna", users=("dora",)),
        "an employee to a company inbox": await post("anna", groups=(desk,)),
    } == {
        "a client to their employee": 201,
        "a client to another client": "RECIPIENT_NOT_CONNECTED",
        "a client to someone else's employee": "RECIPIENT_NOT_CONNECTED",
        "a client to a group": "SECRET_RECIPIENT_NOT_ALLOWED",
        "an employee to someone else's client": "RECIPIENT_NOT_CONNECTED",
        "an employee to a company inbox": 201,
    }

    # The sender follows who has read it, so they see the group's members -
    # also a member they could not have addressed on their own. A member sees
    # no one else.
    sid = send(w.db, w["anna"], groups=(desk,)).secret.id
    (entry,) = (await w.get("anna", f"/api/secrets/{sid}")).json()["recipients"]
    assert sorted(m["user"]["display_name"] for m in entry["members"]) == ["Ben", "Dora"]
    for member, the_other in (("ben", "Dora"), ("dora", "Ben")):
        seen = (await w.get(member, f"/api/secrets/{sid}")).json()
        assert seen["recipients"] == [], member
        assert the_other not in str(seen) and f"{the_other.lower()}@test.local" not in str(seen), member
    for outsider in ("carl", "emil"):
        assert (await w.get(outsider, f"/api/secrets/{sid}")).status_code == 404, outsider


# ---------------------------------------------------------------------------
# Inviting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_invite_says_when_the_address_already_has_an_account(org):
    """Pinned as it is (decided 2026-11-22). The recipient field never looks an
    address up, so an employee cannot learn there that a client exists - but
    whoever invites has to be told why no invitation went out, and staff
    learning that an address has an account is a small thing to give away."""
    w = org

    async def invite(who: str, email: str):
        r = await w.post(who, "/api/account/invite", json={"email": email, "display_name_hint": "X"})
        return r.status_code, r.json().get("code")

    assert await invite("anna", "dora@test.local") == (409, "USER_EXISTS")
    assert (await invite("anna", "new.person@test.local"))[0] in (200, 201)
    assert await invite("ben", "new.person@test.local") == (409, "INVITE_PENDING")
    # A client invites nobody.
    assert (await invite("carl", "someone@test.local"))[0] == 403
