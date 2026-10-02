"""Shared setup for the secrets tests (v2.24.0).

`enable` turns the feature on (it ships off); `send` creates a secret through
the SERVICE, the way the router does, and commits; `group_with` builds a group;
`connect` links a client to an employee. The HTTP tests use the same helpers for
setup and the API for the thing under test.
"""
from __future__ import annotations

from datetime import timedelta

from app.models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.secret import SecretViewScope
from app.models.user import User
from app.services import secret as secret_svc
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now

K = settings_svc.Keys
PW = "Pass12345678!"
CONTENT = "correct horse battery staple"


def setting(db, key: str, value: str | None) -> None:
    settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


def enable(db, **extra: str) -> None:
    setting(db, K.SECRETS_ENABLED, "true")
    for key, value in extra.items():
        setting(db, key, value)


def group_with(db, owner: User, *members: User, name: str = "Ops", inbox: bool = False) -> Group:
    g = Group(
        name=name,
        name_normalized=name.lower(),
        created_by_id=owner.id,
        is_company_inbox=inbox,
    )
    db.add(g)
    db.flush()
    for m in members:
        db.add(GroupMember(group_id=g.id, user_id=m.id))
    db.commit()
    return g


def connect(db, client: User, employee: User) -> None:
    db.add(
        ClientEmployeeConnection(
            client_user_id=client.id,
            employee_user_id=employee.id,
            source=ConnectionSource.invite,
        )
    )
    db.commit()


def send(
    db,
    sender: User,
    *,
    users: tuple[User, ...] = (),
    groups: tuple[Group, ...] = (),
    emails: tuple[str, ...] = (),
    link: bool = False,
    content: str = CONTENT,
    label: str | None = "VPN",
    passphrase: str | None = None,
    max_views: int | None = 1,
    scope: SecretViewScope = SecretViewScope.per_person,
    expires_in: timedelta | None = timedelta(days=7),
    notify: bool = False,
    burn: bool = False,
) -> secret_svc.CreatedSecret:
    created = secret_svc.create_secret(
        db,
        sender=sender,
        content=content,
        label=label,
        passphrase=passphrase,
        max_views=max_views,
        view_scope=scope,
        expires_at=(utc_now() + expires_in) if expires_in is not None else None,
        user_ids=[u.id for u in users],
        group_ids=[g.id for g in groups],
        emails=list(emails),
        create_link=link,
        notify_on_view=notify,
        burn_on_failures=burn,
    )
    db.commit()
    return created


def ask(
    db,
    requester: User,
    *,
    users: tuple[User, ...] = (),
    groups: tuple[Group, ...] = (),
    emails: tuple[str, ...] = (),
    link: bool = False,
    label: str = "Router password",
    note: str | None = None,
    passphrase: str | None = None,
    views: int | None = 1,
    lifetime: timedelta | None = timedelta(days=7),
    open_for: timedelta = timedelta(days=7),
):
    """Create a secret request through the SERVICE, like the router, and commit."""
    from app.services import secret_request as request_svc

    created = request_svc.create_request(
        db,
        requester=requester,
        label=label,
        note=note,
        expires_at=utc_now() + open_for,
        answer_max_views=views,
        answer_expires_in_sec=int(lifetime.total_seconds()) if lifetime is not None else None,
        passphrase=passphrase,
        user_ids=[u.id for u in users],
        group_ids=[g.id for g in groups],
        emails=list(emails),
        create_link=link,
    )
    db.commit()
    return created


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_of(url: str) -> str:
    """The token in a secret link - after the `#`, never in the path."""
    assert "#" in url, url
    return url.rsplit("#", 1)[1]
