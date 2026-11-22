"""One small organisation for the visibility tests (test_visibility_*.py).

Who may see a share, a file, a person or a group was tested where the rule is
WRITTEN - by calling `share.is_authorized_to_download` and friends - and rarely
where it is USED: the routes that hand over the page and the bytes were hardly
ever called by someone who must be refused, every "bystander" was a client, and
no test anywhere expected `FILE_INFECTED` (audit 2026-11-22).

So these tests go through HTTP, and put the person who must be refused beside
the one who is allowed. The `World` keeps that cheap: people, groups, shares
and files are rows made directly (the addressing rules have their own tests,
and must not decide who a visibility test can set up), files are real bytes on
disk, and a person's bearer token is minted without a sign-in.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from app.config import settings
from app.models.client_employee_connection import ClientEmployeeConnection, ConnectionSource
from app.models.file import File, FileApprovalState, FileState
from app.models.group import Group
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import User, UserRole
from app.services import group as group_svc
from app.services import jwt_session
from app.services import public_link as public_link_svc
from app.services import settings as settings_svc
from app.utils.crypto import argon2_hash
from app.utils.timeutil import utc_now

# One hash for everyone: nobody signs in with a password here.
_PASSWORD_HASH = argon2_hash("TestPassword123!")

ADMIN, EMPLOYEE, CLIENT = UserRole.admin, UserRole.employee, UserRole.client


class World:
    def __init__(self, db, client, monkeypatch, tmp_path: Path):
        self.db = db
        self.http = client
        self.root = tmp_path / "store"
        self.root.mkdir()
        monkeypatch.setattr(settings, "STORAGE_ROOT", str(self.root))
        self.people: dict[str, User] = {}

    # ---- people, groups ---------------------------------------------------

    def person(self, name: str, role: UserRole, *, disabled: bool = False) -> User:
        u = User(
            email=f"{name}@test.local",
            password_hash=_PASSWORD_HASH,
            display_name=name.replace("_", " ").title(),
            role=role,
            email_verified=True,
            is_disabled=disabled,
        )
        self.db.add(u)
        self.db.commit()
        self.people[name] = u
        return u

    def __getitem__(self, name: str) -> User:
        return self.people[name]

    # Groups go through the service: joining and leaving also make and unmake
    # the connections between a group's clients and its staff.

    def group(self, name: str, *members: User, by: User, inbox: bool = False) -> Group:
        g = group_svc.create_group(
            self.db, actor=by, name=name, description=None, is_company_inbox=inbox
        )
        for m in members:
            group_svc.add_member(self.db, actor=by, group=g, user=m)
        self.db.commit()
        return g

    def join(self, group: Group, user: User, *, by: User) -> None:
        group_svc.add_member(self.db, actor=by, group=group, user=user)
        self.db.commit()

    def leave(self, group: Group, user: User, *, by: User) -> None:
        group_svc.remove_member(self.db, actor=by, group=group, user=user)
        self.db.commit()

    def connect(self, client: User, employee: User) -> None:
        self.db.add(
            ClientEmployeeConnection(
                client_user_id=client.id,
                employee_user_id=employee.id,
                source=ConnectionSource.invite,
            )
        )
        self.db.commit()

    # ---- shares, files ----------------------------------------------------

    def share(
        self,
        sender: User,
        *,
        to: tuple[User, ...] = (),
        groups: tuple[Group, ...] = (),
        kind: ShareKind = ShareKind.outbound,
        state: ShareState = ShareState.active,
        subject: str | None = "Quarterly figures",
        expires_in: timedelta = timedelta(days=7),
        approval_was_required: bool = False,
    ) -> Share:
        s = Share(
            kind=kind,
            state=state,
            created_by_id=sender.id,
            subject=subject,
            expires_at=utc_now() + expires_in,
            approval_was_required=approval_was_required,
        )
        self.db.add(s)
        self.db.flush()
        for u in to:
            self.db.add(ShareRecipient(share_id=s.id, recipient_user_id=u.id))
        for g in groups:
            self.db.add(ShareRecipient(share_id=s.id, recipient_group_id=g.id))
        self.db.commit()
        return s

    def file(
        self,
        share: Share,
        name: str = "report.txt",
        content: bytes = b"the figures",
        *,
        state: FileState = FileState.clean,
        approval: FileApprovalState = FileApprovalState.approved,
        on_disk: bool = True,
    ) -> File:
        """A file of `share` with real bytes behind it (text/plain, so it can
        be previewed)."""
        fid = str(uuid.uuid4())
        path = self.root / f"{fid}.bin"
        if on_disk:
            path.write_bytes(content)
        f = File(
            id=fid,
            share_id=share.id,
            original_filename=name,
            mime_type="text/plain",
            size_bytes=len(content),
            storage_path=str(path) if on_disk else None,
            state=state,
            approval_state=approval,
            uploaded_by_id=share.created_by_id,
        )
        self.db.add(f)
        self.db.commit()
        return f

    def link(
        self,
        share: Share,
        *,
        by: User | None = None,
        password: str | None = None,
        limit: int | None = None,
    ) -> str:
        """The share's public link; returns its token. Made by the sender
        unless `by` says otherwise (a share that needed approval takes an
        admin). The share must be live at this moment: change its state after."""
        created = public_link_svc.create_link(
            self.db,
            actor=by or self.db.get(User, share.created_by_id),
            share=share,
            password=password,
            download_limit=limit,
            notify_on_download=False,
        )
        self.db.commit()
        return created.plaintext_token

    def setting(self, key: str, value: str | None) -> None:
        settings_svc.set_value(self.db, key=key, value=value, actor=None)
        self.db.commit()

    # ---- what a person is answered ----------------------------------------

    def as_(self, who: User | str) -> dict[str, str]:
        user = self.people[who] if isinstance(who, str) else who
        token, _ = jwt_session.create_access_token(user.id, settings)
        return {"Authorization": f"Bearer {token}"}

    async def get(self, who: User | str | None, url: str, **kw):
        headers = self.as_(who) if who is not None else {}
        return await self.http.get(url, headers=headers, **kw)

    async def post(self, who: User | str | None, url: str, **kw):
        headers = self.as_(who) if who is not None else {}
        return await self.http.post(url, headers=headers, **kw)

    async def listed(self, who: User | str, box: str = "inbox", **params) -> list[dict]:
        """Every row of a person's Received / Sent list, whatever its state -
        the SPA's default "active" filter is not the server's doing."""
        r = await self.get(who, "/api/shares", params={"box": box, "page_size": 200, **params})
        assert r.status_code == 200, r.text
        return r.json()["items"]

    async def lists(self, who: User | str, share: Share, box: str = "inbox") -> bool:
        return share.id in [row["id"] for row in await self.listed(who, box)]

    async def surfaces(self, who: User | str, share: Share, file: File) -> dict[str, int]:
        """The status each way to a share's content answers `who` with. A link
        that was minted is followed, so "200" means the bytes arrived."""
        out: dict[str, int] = {}
        out["page"] = (await self.get(who, f"/api/shares/{share.id}")).status_code
        out["link"] = await self._mint_and_follow(who, f"/api/files/{file.id}/download-url")
        out["bytes"] = (await self.get(who, f"/api/files/{file.id}/download")).status_code
        out["zip_link"] = await self._mint_and_follow(who, f"/api/files/{share.id}/download-zip-url")
        out["zip"] = (await self.get(who, f"/api/files/{share.id}/download-zip")).status_code
        out["preview_link"] = await self._mint_and_follow(who, f"/api/files/{file.id}/preview-url")
        out["preview"] = (await self.get(who, f"/api/files/{file.id}/preview")).status_code
        return out

    async def _mint_and_follow(self, who: User | str, mint_url: str) -> int:
        minted = await self.get(who, mint_url)
        if minted.status_code != 200:
            return minted.status_code
        return (await self.http.get(minted.json()["url"])).status_code

    async def code(self, who: User | str | None, url: str, **kw) -> str | int:
        """The error code a GET is refused with, or its status when it is not."""
        r = await self.get(who, url, **kw)
        return r.json().get("code", r.status_code) if r.status_code >= 400 else r.status_code


def ticketed(db, token: str, file_id: str | None = None) -> str:
    """The address a Download click on the public page opens, signed directly:
    what a visitor would hold if the ticket route had let them have one."""
    link = public_link_svc.get_link_by_token(db, token)
    ticket = public_link_svc.issue_download_ticket(link.id, file_id or public_link_svc.ZIP_TARGET, 900)
    path = "download-zip" if file_id is None else f"files/{file_id}/download"
    return f"/api/public/{token}/{path}?t={ticket}"


def zip_names(content: bytes) -> list[str]:
    return sorted(zipfile.ZipFile(io.BytesIO(content)).namelist())


EVERY_SURFACE = ("page", "link", "bytes", "zip_link", "zip", "preview_link", "preview")


def all_(status: int) -> dict[str, int]:
    return dict.fromkeys(EVERY_SURFACE, status)
