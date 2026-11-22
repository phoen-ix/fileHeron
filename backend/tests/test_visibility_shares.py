"""Who gets a share's page and its bytes - asked at the routes.

`_visibility_world.py` says why this file exists. Each test is a table: the
people down the side, the ways to the content across the top
(`World.surfaces`: the share page, the download link and the download itself,
the ZIP link and the ZIP, the preview link and the preview), and the status
each of them is answered with. Adding a person or a way in is one row.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.share import ShareKind, ShareState
from app.services import settings as settings_svc

from ._visibility_world import ADMIN, CLIENT, EMPLOYEE, World, all_

K = settings_svc.Keys


@pytest.fixture
def world(db, client, monkeypatch, tmp_path) -> World:
    return World(db, client, monkeypatch, tmp_path)


# ---------------------------------------------------------------------------
# An outbound share, live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_outbound_share_is_for_its_sender_its_recipients_and_admins(world):
    w = world
    admin = w.person("admin", ADMIN)
    sender = w.person("sender", EMPLOYEE)
    direct = w.person("direct", CLIENT)
    member = w.person("member", CLIENT)
    joins_later = w.person("joins_later", CLIENT)
    leaves = w.person("leaves", CLIENT)
    w.person("colleague", EMPLOYEE)  # an employee nobody addressed
    w.person("stranger", CLIENT)
    project = w.group("Project", member, leaves, by=admin)
    share = w.share(sender, to=(direct,), groups=(project,))
    file = w.file(share)

    # Membership is read when asked, not when the share was sent.
    w.join(project, joins_later, by=admin)
    w.leave(project, leaves, by=admin)

    allowed, refused = all_(200), all_(403)
    expected = {
        "sender": allowed,
        "admin": allowed,
        "direct": allowed,
        "member": allowed,
        "joins_later": allowed,
        "leaves": refused,
        "colleague": refused,
        "stranger": refused,
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected

    received = {who: await w.lists(who, share) for who in expected}
    assert received == {
        "sender": False,
        "admin": False,
        "direct": True,
        "member": True,
        "joins_later": True,
        "leaves": False,
        "colleague": False,
        "stranger": False,
    }
    sent = {who: await w.lists(who, share, "outbox") for who in expected}
    assert sent == {who: who == "sender" for who in expected}


@pytest.mark.asyncio
async def test_a_refusal_says_forbidden_for_a_real_share_and_not_found_for_none(world):
    """Pinned as it is (decided 2026-11-22): a share's id is a random UUID
    nobody can guess, so telling the two apart gives nothing away."""
    w = world
    sender, stranger = w.person("sender", EMPLOYEE), w.person("stranger", CLIENT)
    share = w.share(sender)
    w.file(share)

    assert await w.code(stranger, f"/api/shares/{share.id}") == "FORBIDDEN"
    assert await w.code(stranger, "/api/shares/00000000-0000-0000-0000-000000000000") == "SHARE_NOT_FOUND"


# ---------------------------------------------------------------------------
# A share that is held, refused, ended
# ---------------------------------------------------------------------------


def _approval(w: World, *, reviewers: list[int], content_review: bool) -> None:
    w.setting(K.SHARE_APPROVAL_ENABLED, "true")
    w.setting(K.SHARE_APPROVAL_APPROVER_MODE, "admins_only")
    w.setting(K.SHARE_APPROVAL_SCOPE, "all")
    w.setting(K.SHARE_APPROVAL_APPROVER_USERS, str(reviewers))
    w.setting(K.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW, "true" if content_review else "false")


def _cast(w: World):
    return (
        w.person("admin", ADMIN),
        w.person("sender", EMPLOYEE),
        # An approver who is NOT an admin: every approval test used an admin,
        # who passes most checks on their role alone.
        w.person("reviewer", EMPLOYEE),
        w.person("colleague", EMPLOYEE),
        w.person("recipient", CLIENT),
        w.person("stranger", CLIENT),
    )


def _bytes(status: int, *, page: int) -> dict[str, int]:
    return {**all_(status), "page": page}


@pytest.mark.asyncio
@pytest.mark.parametrize("content_review", [True, False])
async def test_a_held_share_is_for_its_sender_and_the_approvers_only(world, content_review):
    w = world
    admin, sender, reviewer, colleague, recipient, stranger = _cast(w)
    _approval(w, reviewers=[reviewer.id], content_review=content_review)
    share = w.share(sender, to=(recipient,), state=ShareState.pending_approval, approval_was_required=True)
    file = w.file(share)

    # Whether an approver may read the FILES is the admin's switch; the page
    # they decide on is theirs either way. Without the switch an admin is told
    # the share is not live and another approver that it is not theirs.
    expected = {
        "sender": _bytes(410, page=200),
        "admin": all_(200) if content_review else _bytes(410, page=200),
        "reviewer": all_(200) if content_review else _bytes(403, page=200),
        "colleague": all_(403),
        # Addressed, so "not yours" would be wrong - but nothing of it is theirs yet.
        "recipient": _bytes(410, page=403),
        "stranger": all_(403),
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected
    assert {who: await w.lists(who, share) for who in expected} == dict.fromkeys(expected, False)

    queue = {
        who: share.id
        in [row["id"] for row in (await w.get(who, "/api/shares/pending-approval")).json()["items"]]
        for who in expected
    }
    assert queue == {**dict.fromkeys(expected, False), "admin": True, "reviewer": True}


@pytest.mark.asyncio
async def test_a_rejected_share_is_for_its_sender_and_admins_only(world):
    w = world
    admin, sender, reviewer, colleague, recipient, stranger = _cast(w)
    _approval(w, reviewers=[reviewer.id], content_review=True)
    share = w.share(sender, to=(recipient,), state=ShareState.rejected, approval_was_required=True)
    file = w.file(share)

    expected = {
        "sender": _bytes(410, page=200),
        "admin": _bytes(410, page=200),
        "reviewer": all_(403),
        "colleague": all_(403),
        "recipient": _bytes(410, page=403),
        "stranger": all_(403),
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected
    assert {who: await w.lists(who, share) for who in expected} == dict.fromkeys(expected, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ended", "code"),
    [
        (ShareState.revoked, "SHARE_NOT_ACTIVE"),
        (ShareState.expired, "SHARE_NOT_ACTIVE"),
        (ShareState.deleted, "SHARE_NOT_ACTIVE"),
        (ShareState.failed, "SHARE_NOT_ACTIVE"),
        ("past its time", "SHARE_EXPIRED"),
    ],
)
async def test_an_ended_share_keeps_its_page_and_gives_no_bytes(world, ended, code):
    """A recipient still sees that the share existed (the lists keep it, under
    its state); nobody gets a byte of it, by any of the six ways."""
    w = world
    admin, sender, reviewer, colleague, recipient, stranger = _cast(w)
    if ended == "past its time":
        share = w.share(sender, to=(recipient,), expires_in=timedelta(minutes=-1))
    else:
        share = w.share(sender, to=(recipient,), state=ended)
    file = w.file(share)

    expected = {
        "sender": _bytes(410, page=200),
        "admin": _bytes(410, page=200),
        "recipient": _bytes(410, page=200),
        "colleague": all_(403),
        "stranger": all_(403),
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected
    assert await w.lists(recipient, share) is True
    assert await w.lists(stranger, share) is False
    for url in (
        f"/api/files/{file.id}/download-url",
        f"/api/files/{file.id}/download",
        f"/api/files/{share.id}/download-zip-url",
        f"/api/files/{file.id}/preview-url",
    ):
        assert await w.code(recipient, url) == code, url


# ---------------------------------------------------------------------------
# A link minted earlier; an account switched off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_download_link_minted_earlier_does_not_outlive_the_right_to_it(world):
    """The `?dt=` link stands in for the person who minted it. The route that
    serves it asks again whether they may have the file - so leaving the group,
    the share ending or the account being switched off ends the link too."""
    w = world
    admin = w.person("admin", ADMIN)
    sender = w.person("sender", EMPLOYEE)
    leaves, stays, disabled_later = (w.person(n, CLIENT) for n in ("leaves", "stays", "disabled_later"))
    project = w.group("Project", leaves, stays, disabled_later, by=admin)
    share = w.share(sender, groups=(project,))
    file = w.file(share)

    async def links(who) -> list[str]:
        return [
            (await w.get(who, url)).json()["url"]
            for url in (
                f"/api/files/{file.id}/download-url",
                f"/api/files/{share.id}/download-zip-url",
                f"/api/files/{file.id}/preview-url",
            )
        ]

    async def follow(urls: list[str]) -> list[str | int]:
        return [await w.code(None, u) for u in urls]

    of_leaver, of_stayer, of_disabled = await links(leaves), await links(stays), await links(disabled_later)
    assert await follow(of_leaver) == [200, 200, 200]

    w.leave(project, leaves, by=admin)
    assert await follow(of_leaver) == ["FORBIDDEN"] * 3
    assert await follow(of_stayer) == [200, 200, 200]

    disabled_later.is_disabled = True
    w.db.commit()
    assert await follow(of_disabled) == ["INVALID_DOWNLOAD_TOKEN"] * 3

    share.state = ShareState.revoked
    w.db.commit()
    assert await follow(of_stayer) == ["SHARE_NOT_ACTIVE"] * 3


@pytest.mark.asyncio
async def test_a_switched_off_account_reaches_nothing(world):
    """Its session is over (401, so the app signs out) and its API tokens are
    refused by name - on the routes that serve shares and files, not only on
    `/api/account/me`, the one place this was checked."""
    from app.services import api_token as api_token_svc

    w = world
    sender = w.person("sender", EMPLOYEE)
    recipient = w.person("recipient", CLIENT)
    share = w.share(sender, to=(recipient,))
    file = w.file(share)
    _, api_token = api_token_svc.create_token(w.db, owner=recipient, name="script")
    w.db.commit()
    by_token = {"Authorization": f"Bearer {api_token}"}
    urls = (
        f"/api/shares/{share.id}",
        "/api/shares?box=inbox",
        f"/api/files/{file.id}/download-url",
        f"/api/files/{file.id}/download",
        f"/api/files/{share.id}/download-zip",
    )
    assert await w.surfaces(recipient, share, file) == all_(200)
    assert [(await w.http.get(u, headers=by_token)).status_code for u in urls] == [200] * len(urls)

    recipient.is_disabled = True
    w.db.commit()

    assert await w.surfaces(recipient, share, file) == all_(401)
    refused = [await w.http.get(u, headers=by_token) for u in urls]
    assert [(r.status_code, r.json()["code"]) for r in refused] == [(403, "ACCOUNT_DISABLED")] * len(urls)


# ---------------------------------------------------------------------------
# What a client hands in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clients_submission_is_for_staff_and_for_clients_in_a_group_with_them(world):
    """By design, and in the user guide: a client sends to the organisation, so
    every employee and admin has it - and so does a client who shares a group
    with the sender, a company inbox included (group membership is what makes
    two clients colleagues here). They see who sent it. A client outside those
    groups sees nothing; a client who LEFT the group stops seeing it."""
    w = world
    admin = w.person("admin", ADMIN)
    w.person("employee", EMPLOYEE)
    submitter = w.person("submitter", CLIENT)
    teammate = w.person("teammate", CLIENT)
    at_the_desk = w.person("at_the_desk", CLIENT)
    left_the_team = w.person("left_the_team", CLIENT)
    outsider = w.person("outsider", CLIENT)
    team = w.group("Team", submitter, teammate, left_the_team, by=admin)
    w.group("Front desk", submitter, at_the_desk, by=admin, inbox=True)
    w.group("Elsewhere", outsider, by=admin)
    w.leave(team, left_the_team, by=admin)
    share = w.share(submitter, kind=ShareKind.inbound, subject="Signed contract")
    file = w.file(share)

    allowed, refused = all_(200), all_(403)
    expected = {
        "submitter": allowed,
        "admin": allowed,
        "employee": allowed,
        "teammate": allowed,
        "at_the_desk": allowed,
        "left_the_team": refused,
        "outsider": refused,
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected

    received = {who: await w.lists(who, share) for who in expected}
    assert received == {
        "submitter": False,  # their own: under Sent
        "admin": True,
        "employee": True,
        "teammate": True,
        "at_the_desk": True,
        "left_the_team": False,
        "outsider": False,
    }
    assert await w.lists(submitter, share, "outbox") is True

    # Who it is from: the name and address of the submitter, to those who have it.
    row = next(r for r in await w.listed(teammate) if r["id"] == share.id)
    assert row["sender"] == {
        "id": submitter.id,
        "display_name": "Submitter",
        "email": "submitter@test.local",
    }
    # ... and it is addressed to "the company", never to a list of people.
    assert [(r["kind"], r["label"]) for r in row["recipients"]] == [("company", "Company")]
    for who in ("left_the_team", "outsider"):
        assert "submitter" not in str(await w.listed(who)).lower(), who


@pytest.mark.asyncio
async def test_a_held_submission_is_not_shown_to_staff_who_do_not_approve(world):
    w = world
    admin = w.person("admin", ADMIN)
    w.person("employee", EMPLOYEE)
    submitter = w.person("submitter", CLIENT)
    teammate = w.person("teammate", CLIENT)
    w.group("Team", submitter, teammate, by=admin)
    _approval(w, reviewers=[], content_review=True)
    share = w.share(
        submitter, kind=ShareKind.inbound, state=ShareState.pending_approval, approval_was_required=True
    )
    file = w.file(share)

    expected = {
        "admin": all_(200),
        "employee": _bytes(410, page=403),
        "teammate": _bytes(410, page=403),
        "submitter": _bytes(410, page=200),
    }
    assert {who: await w.surfaces(who, share, file) for who in expected} == expected
    assert {who: await w.lists(who, share) for who in expected} == dict.fromkeys(expected, False)
