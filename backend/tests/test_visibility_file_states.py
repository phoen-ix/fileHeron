"""A file that is not ready, not clean or not released is never handed over.

Asked at every route that serves bytes, signed in and through a public link.
Before this file no test anywhere expected `FILE_INFECTED`, `STILL_UPLOADING`
or `FILE_DELETED`: the gate (`file.assert_state_servable`) had no test that a
route actually calls it (audit 2026-11-22).
"""
from __future__ import annotations

import pytest

from app.models.file import FileApprovalState, FileState
from app.services import settings as settings_svc

from ._visibility_world import ADMIN, CLIENT, EMPLOYEE, World, ticketed, zip_names

K = settings_svc.Keys


@pytest.fixture
def world(db, client, monkeypatch, tmp_path) -> World:
    return World(db, client, monkeypatch, tmp_path)


async def _refusal(response) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "refused_with"),
    [
        (FileState.uploading, (409, "STILL_UPLOADING")),
        (FileState.ready_unscanned, (425, "SCAN_IN_PROGRESS")),
        (FileState.infected, (410, "FILE_INFECTED")),
        (FileState.deleted, (410, "FILE_DELETED")),
    ],
)
async def test_only_a_clean_file_is_served(world, state, refused_with):
    """The share stays live here on purpose - a real quarantine also revokes
    it - so the FILE's state is the only thing standing between the recipient
    and the bytes."""
    w = world
    sender, recipient = w.person("sender", EMPLOYEE), w.person("recipient", CLIENT)
    share = w.share(sender, to=(recipient,))
    good = w.file(share, "good.txt", b"fine")
    bad = w.file(share, "bad.txt", b"not for anyone", state=state)
    token = w.link(share)

    signed_in = [
        f"/api/files/{bad.id}/download-url",
        f"/api/files/{bad.id}/download",
        f"/api/files/{bad.id}/preview-url",
        f"/api/files/{bad.id}/preview",
    ]
    for who in (recipient, sender):
        assert [await _refusal(await w.get(who, u)) for u in signed_in] == [refused_with] * 4, who.email

    # Through the public link: the ticket is not issued, and an address signed
    # for the file anyway is not honoured either.
    ticket = await w.post(None, f"/api/public/{token}/download-ticket", json={"file_id": bad.id})
    assert await _refusal(ticket) == refused_with
    assert await _refusal(await w.get(None, ticketed(w.db, token, bad.id))) == refused_with
    assert await _refusal(await w.get(None, f"/api/public/{token}/files/{bad.id}/preview")) == refused_with

    # The archive holds what can be had, and nothing else - open it and look.
    for archive in (
        await w.get(recipient, f"/api/files/{share.id}/download-zip"),
        await w.get(None, ticketed(w.db, token)),
    ):
        assert archive.status_code == 200, archive.text
        assert zip_names(archive.content) == ["good.txt"]
    assert (await w.get(recipient, f"/api/files/{good.id}/download")).content == b"fine"


# ---------------------------------------------------------------------------
# A file added after the share was approved
# ---------------------------------------------------------------------------


def _added_after_approval(w: World, *, content_review: bool):
    w.person("admin", ADMIN)
    sender = w.person("sender", EMPLOYEE)
    reviewer = w.person("reviewer", EMPLOYEE)  # an approver who is not an admin
    recipient = w.person("recipient", CLIENT)
    w.person("stranger", CLIENT)
    w.setting(K.SHARE_APPROVAL_ENABLED, "true")
    w.setting(K.SHARE_APPROVAL_APPROVER_MODE, "admins_only")
    w.setting(K.SHARE_APPROVAL_SCOPE, "all")
    w.setting(K.SHARE_APPROVAL_APPROVER_USERS, str([reviewer.id]))
    w.setting(K.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW, "true" if content_review else "false")
    share = w.share(sender, to=(recipient,), approval_was_required=True)
    w.file(share, "approved.txt", b"released")
    added = w.file(share, "payload.txt", b"unreviewed", approval=FileApprovalState.pending_review)
    return share, added


@pytest.mark.asyncio
@pytest.mark.parametrize("content_review", [True, False])
async def test_a_file_awaiting_review_is_withheld_at_every_route(world, content_review):
    """The share was approved; a file added later waits for its own decision.
    The service that says so was tested - whether each route passes the file
    into it was not, and that is the whole four-eyes bypass."""
    w = world
    share, added = _added_after_approval(w, content_review=content_review)
    urls = [
        f"/api/files/{added.id}/download-url",
        f"/api/files/{added.id}/download",
        f"/api/files/{added.id}/preview-url",
        f"/api/files/{added.id}/preview",
    ]

    async def answers(who) -> list[str | int]:
        return [await w.code(who, u) for u in urls]

    waiting = ["FILE_PENDING_APPROVAL"] * 4
    assert await answers("recipient") == waiting
    assert await answers("stranger") == ["FORBIDDEN"] * 4
    assert await answers("sender") == [200] * 4
    # The approver reads it if the admin lets approvers read content - an admin
    # included: the switch is about the role they act in, not their rank.
    assert await answers("reviewer") == ([200] * 4 if content_review else ["FORBIDDEN"] * 4)
    assert await answers("admin") == ([200] * 4 if content_review else waiting)

    # Nobody's archive carries it: one archive for everyone, or a resumed
    # download would not match the first half.
    for who in ("recipient", "sender", "admin"):
        archive = await w.get(who, f"/api/files/{share.id}/download-zip")
        assert zip_names(archive.content) == ["approved.txt"], who


@pytest.mark.asyncio
@pytest.mark.parametrize("content_review", [True, False])
async def test_a_recipient_is_not_told_a_file_is_awaiting_review(world, content_review):
    """Not its name - "a filename is content" - and not that it exists: the
    page carried the ids of the waiting files and a banner counting them for
    every viewer, and the Received list counted them in, so a recipient read
    "3 files" beside a page showing two, before anything was released and
    before the mail that announces added files. The people who must know do."""
    w = world
    share, added = _added_after_approval(w, content_review=content_review)

    async def page(who) -> dict:
        return (await w.get(who, f"/api/shares/{share.id}")).json()

    theirs = await page("recipient")
    assert [f["original_filename"] for f in theirs["files"]] == ["approved.txt"]
    assert theirs["files_awaiting_review"] == []
    assert theirs["content_fingerprint"] is None
    assert "payload" not in str(theirs)
    row = next(r for r in await w.listed("recipient") if r["id"] == share.id)
    assert (row["file_count"], row["total_size_bytes"]) == (1, len(b"released"))

    # The sender sees their own upload; whoever decides is told there is
    # something to decide - with its name only when they may read content.
    for who, sees_name in (("sender", True), ("admin", True), ("reviewer", content_review)):
        seen = await page(who)
        assert seen["files_awaiting_review"] == [added.id], who
        assert seen["content_fingerprint"], who
        assert ("payload.txt" in [f["original_filename"] for f in seen["files"]]) is sees_name, who
    sent = next(r for r in await w.listed("sender", "outbox") if r["id"] == share.id)
    assert sent["file_count"] == 2


@pytest.mark.asyncio
async def test_a_public_link_does_not_know_a_file_awaiting_review(world):
    w = world
    share, added = _added_after_approval(w, content_review=True)
    token = w.link(share, by=w["admin"])

    landing = (await w.get(None, f"/api/public/{token}")).json()
    assert [f["original_filename"] for f in landing["files"]] == ["approved.txt"]
    assert "payload" not in str(landing)
    # "Not found", not "awaiting approval": an anonymous visitor is neither the
    # owner nor an approver, so a distinct answer would only confirm it exists.
    assert await w.code(None, ticketed(w.db, token, added.id)) == "FILE_NOT_FOUND"
    assert await w.code(None, f"/api/public/{token}/files/{added.id}/preview") == "FILE_NOT_FOUND"
    minted = await w.post(None, f"/api/public/{token}/download-ticket", json={"file_id": added.id})
    assert await _refusal(minted) == (404, "FILE_NOT_FOUND")
    assert zip_names((await w.get(None, ticketed(w.db, token))).content) == ["approved.txt"]
