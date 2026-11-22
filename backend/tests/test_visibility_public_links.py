"""What a public link hands over, and to whom - asked at its routes.

A link is a bearer credential for ONE share. The checks that keep it to that
share, and that close it when the share or the link ends, were tested in the
service (`public_link.assert_link_usable`) and on the page; the routes that
serve bytes were mostly not asked (audit 2026-11-22).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.public_link import PublicLink
from app.models.share import ShareState
from app.utils.timeutil import utc_now

from ._visibility_world import CLIENT, EMPLOYEE, World, ticketed, zip_names


@pytest.fixture
def world(db, client, monkeypatch, tmp_path) -> World:
    return World(db, client, monkeypatch, tmp_path)


async def _answers(w: World, token: str, file_id: str) -> dict[str, str | int]:
    """Every way into a link's content: the page, the ticket for one file and
    for the archive, each address a ticket would open, and the preview."""
    ticket = await w.post(None, f"/api/public/{token}/download-ticket", json={"file_id": file_id})
    zip_ticket = await w.post(None, f"/api/public/{token}/download-ticket", json={"file_id": None})

    def told(r) -> str | int:
        return r.json()["code"] if r.status_code >= 400 else r.status_code

    return {
        "page": await w.code(None, f"/api/public/{token}"),
        "ticket": told(ticket),
        "zip_ticket": told(zip_ticket),
        "download": await w.code(None, ticketed(w.db, token, file_id)),
        "zip": await w.code(None, ticketed(w.db, token)),
        "preview": await w.code(None, f"/api/public/{token}/files/{file_id}/preview"),
    }


WAYS = ("page", "ticket", "zip_ticket", "download", "zip", "preview")


def _link_row(w: World, share) -> PublicLink:
    return w.db.query(PublicLink).filter(PublicLink.share_id == share.id).one()


@pytest.mark.asyncio
async def test_a_link_reaches_only_its_own_share(world):
    """The worst one to lose: without `file.share_id != link.share_id`, whoever
    holds ANY link fetches any file on the instance by its id."""
    w = world
    sender, other = w.person("sender", EMPLOYEE), w.person("other", EMPLOYEE)
    mine = w.share(sender)
    own_file = w.file(mine, "mine.txt", b"for the link's holder")
    theirs = w.share(other, to=(w.person("recipient", CLIENT),))
    foreign = w.file(theirs, "theirs.txt", b"nobody gave this out")
    token = w.link(mine)

    assert (await w.get(None, ticketed(w.db, token, own_file.id))).content == b"for the link's holder"

    minted = await w.post(None, f"/api/public/{token}/download-ticket", json={"file_id": foreign.id})
    assert (minted.status_code, minted.json()["code"]) == (404, "FILE_NOT_FOUND")
    # ... and an address signed for that file anyway opens nothing.
    assert await w.code(None, ticketed(w.db, token, foreign.id)) == "FILE_NOT_FOUND"
    assert await w.code(None, f"/api/public/{token}/files/{foreign.id}/preview") == "FILE_NOT_FOUND"
    assert await w.code(None, f"/api/public/{token}/files/no-such-file/preview") == "FILE_NOT_FOUND"

    landing = (await w.get(None, f"/api/public/{token}")).json()
    assert [f["original_filename"] for f in landing["files"]] == ["mine.txt"]
    assert zip_names((await w.get(None, ticketed(w.db, token))).content) == ["mine.txt"]


@pytest.mark.asyncio
async def test_a_password_closes_every_way_in_until_it_is_given(world):
    w = world
    share = w.share(w.person("sender", EMPLOYEE), subject="Offer for Acme")
    file = w.file(share, "offer.txt", b"the offer")
    token = w.link(share, password="Correct-Horse-9")

    before = await _answers(w, token, file.id)
    assert before == {**dict.fromkeys(WAYS, "UNLOCK_REQUIRED"), "page": 200}
    locked_page = (await w.get(None, f"/api/public/{token}")).json()
    assert (locked_page["subject"], locked_page["files"]) == (None, [])
    assert "offer" not in str(locked_page).lower()

    wrong = await w.post(None, f"/api/public/{token}/unlock", json={"password": "not-it"})
    assert wrong.status_code == 401
    assert (await _answers(w, token, file.id))["download"] == "UNLOCK_REQUIRED"

    opened = await w.post(None, f"/api/public/{token}/unlock", json={"password": "Correct-Horse-9"})
    assert opened.status_code == 200, opened.text
    assert await _answers(w, token, file.id) == dict.fromkeys(WAYS, 200)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("what_happened", "told"),
    [
        ("the link was revoked", "PUBLIC_LINK_REVOKED"),
        ("the link is locked after wrong passwords", "PUBLIC_LINK_LOCKED"),
        ("the link's downloads are used up", "PUBLIC_LINK_EXHAUSTED"),
        ("the share is held for approval", "SHARE_NOT_ACTIVE"),
        ("the share was revoked", "SHARE_NOT_ACTIVE"),
        ("the share was deleted", "SHARE_NOT_ACTIVE"),
        ("the share is past its time", "SHARE_EXPIRED"),
    ],
)
async def test_a_link_that_ended_opens_nothing(world, what_happened, told):
    w = world
    share = w.share(w.person("sender", EMPLOYEE))
    file = w.file(share)
    token = w.link(share, limit=3)
    assert await _answers(w, token, file.id) == dict.fromkeys(WAYS, 200)

    link = _link_row(w, share)
    if what_happened == "the link was revoked":
        link.revoked_at = utc_now()
    elif what_happened == "the link is locked after wrong passwords":
        link.locked_until = utc_now() + timedelta(minutes=10)
    elif what_happened == "the link's downloads are used up":
        link.downloads_remaining = 0
    elif what_happened == "the share is held for approval":
        share.state = ShareState.pending_approval
    elif what_happened == "the share was revoked":
        share.state = ShareState.revoked
    elif what_happened == "the share was deleted":
        share.state = ShareState.deleted
    else:
        share.expires_at = utc_now() - timedelta(minutes=1)
    w.db.commit()

    assert await _answers(w, token, file.id) == dict.fromkeys(WAYS, told)
