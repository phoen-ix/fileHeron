"""Reading a secret (v2.24.0): who may, how each view scope counts, and what a
wrong passphrase does.

The three scopes, as the sender chose them:
- each person: everyone who can read it gets X - including each group member;
- each recipient: every entry the sender added gets X; a group's members share
  theirs, so the first member's view can burn it for the rest of the group;
- all together: X in total, across everyone.

A group member may read only if they were a member when it was sent AND still
are. Neither the sender nor an admin ever reads it. A wrong passphrase never
costs a view and is never a 401.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.group_member import GroupMember
from app.models.secret import (
    Secret,
    SecretAccessEvent,
    SecretAccessOutcome,
    SecretRecipient,
    SecretRecipientKind,
    SecretState,
    SecretUserState,
    SecretViewScope,
)
from app.models.user import UserRole
from app.services import secret as secret_svc
from app.services import secret_reveal
from app.utils.timeutil import utc_now

from ._secret_helpers import CONTENT, PW, K, enable, group_with, h, send, token_of

PASS = "the long passphrase"


@pytest.fixture
def people(make_user):
    a = make_user(email="a@test.local", role=UserRole.employee, password=PW, display_name="Ann")
    b = make_user(email="b@test.local", role=UserRole.employee, password=PW, display_name="Ben")
    c = make_user(email="c@test.local", role=UserRole.employee, password=PW, display_name="Cat")
    return a, b, c


@pytest.fixture
def reveal(client, login_as):
    tokens: dict[str, str] = {}

    async def _reveal(user, secret_id, passphrase=None):
        if user.email not in tokens:
            tokens[user.email], _ = await login_as(user.email, PW)
        return await client.post(
            f"/api/secrets/{secret_id}/reveal",
            json={"passphrase": passphrase},
            headers=h(tokens[user.email]),
        )

    return _reveal


async def _public(client, path, **body):
    return await client.post(f"/api/public/secrets/{path}", json=body)


def _fresh(db, secret_id) -> Secret:
    db.expire_all()
    return db.get(Secret, secret_id)


# ---- a view, and the last one -------------------------------------------------


@pytest.mark.asyncio
async def test_a_recipient_reads_it_and_the_last_view_destroys_it(db, people, reveal):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,)).secret.id

    resp = await reveal(b, sid)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"content": CONTENT, "views_left": 0, "ended": True}
    assert resp.headers["cache-control"].startswith("no-store")

    s = _fresh(db, sid)
    assert s.state == SecretState.burned
    assert s.ciphertext is None and s.key_encrypted is None, "an ended secret must be shredded"

    again = await reveal(b, sid)
    assert again.status_code == 410, again.text
    assert again.json()["code"] == "SECRET_ENDED"


@pytest.mark.asyncio
async def test_each_person_scope_gives_every_member_their_own_views(db, people, reveal):
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b, c)
    sid = send(db, a, groups=(g,), scope=SecretViewScope.per_person).secret.id

    first = await reveal(b, sid)
    assert first.status_code == 200 and first.json()["ended"] is False
    twice = await reveal(b, sid)
    assert twice.status_code == 410 and twice.json()["code"] == "SECRET_VIEWS_EXHAUSTED"
    last = await reveal(c, sid)
    assert last.status_code == 200 and last.json()["ended"] is True


@pytest.mark.asyncio
async def test_each_recipient_scope_lets_a_group_share_its_views(db, people, reveal):
    """The first member's view burns it for everyone else in the group."""
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b, c)
    sid = send(db, a, groups=(g,), scope=SecretViewScope.per_recipient).secret.id

    first = await reveal(b, sid)
    assert first.status_code == 200 and first.json()["ended"] is True
    late = await reveal(c, sid)
    assert late.status_code == 410 and late.json()["code"] == "SECRET_ENDED"


@pytest.mark.asyncio
async def test_a_direct_recipient_never_draws_on_a_group_pool(db, people, reveal):
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b, c)
    sid = send(db, a, users=(b,), groups=(g,), scope=SecretViewScope.per_recipient).secret.id

    assert (await reveal(b, sid)).status_code == 200
    again = await reveal(b, sid)
    assert again.status_code == 410 and again.json()["code"] == "SECRET_VIEWS_EXHAUSTED"
    group_view = await reveal(c, sid)
    assert group_view.status_code == 200 and group_view.json()["ended"] is True


@pytest.mark.asyncio
async def test_all_together_scope_burns_for_everyone(db, people, reveal, client):
    enable(db)
    a, b, c = people
    created = send(db, a, users=(b, c), link=True, max_views=2, scope=SecretViewScope.total)
    sid = created.secret.id

    one = await reveal(b, sid)
    assert one.status_code == 200 and one.json() == {"content": CONTENT, "views_left": 1, "ended": False}
    two = await _public(client, "reveal", token=created.link_token)
    assert two.status_code == 200 and two.json()["ended"] is True
    late = await reveal(c, sid)
    assert late.status_code == 410 and late.json()["code"] == "SECRET_ENDED"


@pytest.mark.asyncio
async def test_unlimited_views_last_until_expiry(db, people, reveal):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,), max_views=None, expires_in=timedelta(days=1)).secret.id
    for _ in range(3):
        resp = await reveal(b, sid)
        assert resp.status_code == 200
        assert resp.json() == {"content": CONTENT, "views_left": None, "ended": False}
    assert _fresh(db, sid).state == SecretState.active


# ---- who may ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_member_who_left_the_group_can_no_longer_read_it(db, people, reveal):
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b, c)
    sid = send(db, a, groups=(g,)).secret.id
    db.query(GroupMember).filter_by(group_id=g.id, user_id=b.id).delete()
    db.commit()

    resp = await reveal(b, sid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRET_NOT_A_RECIPIENT"


@pytest.mark.asyncio
async def test_someone_who_joined_later_cannot_read_an_older_secret(
    db, people, reveal, client, login_as
):
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b)
    sid = send(db, a, groups=(g,)).secret.id
    db.add(GroupMember(group_id=g.id, user_id=c.id))
    db.commit()

    resp = await reveal(c, sid)
    assert resp.status_code == 404, resp.text
    token, _ = await login_as(c.email, PW)
    detail = await client.get(f"/api/secrets/{sid}", headers=h(token))
    assert detail.status_code == 404, "a secret's existence is not theirs to learn"


@pytest.mark.asyncio
async def test_the_sender_and_admins_never_read_it(db, people, make_user, reveal):
    enable(db)
    a, b, _ = people
    admin = make_user(email="adm@test.local", role=UserRole.admin, password=PW)
    outsider = make_user(email="out@test.local", role=UserRole.employee, password=PW)
    sid = send(db, a, users=(b,)).secret.id

    for who in (a, admin):
        resp = await reveal(who, sid)
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "SECRET_REVEAL_FORBIDDEN"
    assert (await reveal(outsider, sid)).status_code == 404
    assert _fresh(db, sid).views_used == 0


@pytest.mark.asyncio
async def test_an_expired_secret_ends_when_read_without_waiting_for_the_sweep(db, people, reveal):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,)).secret.id
    db.get(Secret, sid).expires_at = utc_now() - timedelta(minutes=1)
    db.commit()

    resp = await reveal(b, sid)
    assert resp.status_code == 410, resp.text
    assert resp.json()["code"] == "SECRET_ENDED"
    assert resp.json()["details"]["reason"] == "expired"
    s = _fresh(db, sid)
    assert s.state == SecretState.expired and s.ciphertext is None


# ---- passphrases ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wrong_passphrase_costs_no_view_and_is_never_a_401(db, people, reveal):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,), passphrase=PASS).secret.id

    wrong = await reveal(b, sid, "not it")
    assert wrong.status_code == 403, wrong.text
    assert wrong.json()["code"] == "SECRET_PASSPHRASE_INVALID"

    db.expire_all()
    assert db.get(Secret, sid).views_used == 0
    state = db.get(SecretUserState, (sid, b.id))
    assert state.views_used == 0 and state.failed_attempts == 1
    events = db.query(SecretAccessEvent).filter_by(secret_id=sid).all()
    assert [e.outcome for e in events] == [SecretAccessOutcome.wrong_passphrase], (
        "the attempt must be committed before the error is raised"
    )

    right = await reveal(b, sid, PASS)
    assert right.status_code == 200 and right.json()["content"] == CONTENT


@pytest.mark.asyncio
async def test_a_missing_passphrase_is_asked_for_and_not_counted(db, people, reveal):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,), passphrase=PASS).secret.id
    resp = await reveal(b, sid)
    assert resp.status_code == 400 and resp.json()["code"] == "SECRET_PASSPHRASE_REQUIRED"
    assert db.query(SecretAccessEvent).count() == 0


@pytest.mark.asyncio
async def test_burn_mode_destroys_it_for_that_person_after_n_wrong(db, people, reveal):
    enable(db, **{K.SECRETS_PASSPHRASE_MAX_FAILURES: "3"})
    a, b, c = people
    sid = send(db, a, users=(b, c), passphrase=PASS, burn=True).secret.id

    for left in (2, 1):
        resp = await reveal(b, sid, "nope")
        assert resp.status_code == 403
        assert resp.json()["details"]["attempts_left"] == left
    burned = await reveal(b, sid, "nope")
    assert burned.status_code == 410 and burned.json()["code"] == "SECRET_RECIPIENT_BURNED"
    after = await reveal(b, sid, PASS)
    assert after.status_code == 410 and after.json()["code"] == "SECRET_RECIPIENT_BURNED"

    last = await reveal(c, sid, PASS)
    assert last.status_code == 200 and last.json()["ended"] is True


@pytest.mark.asyncio
async def test_the_account_throttle_answers_429_even_to_the_right_passphrase(db, people, reveal):
    enable(db, **{K.SECRETS_PASSPHRASE_RATE_LIMIT: "2"})
    a, b, _ = people
    sid = send(db, a, users=(b,), passphrase=PASS).secret.id
    for _ in range(2):
        assert (await reveal(b, sid, "nope")).status_code == 403
    resp = await reveal(b, sid, PASS)
    assert resp.status_code == 429 and resp.json()["code"] == "SECRET_RATE_LIMITED"


# ---- addresses and the link (anonymous) --------------------------------------------


@pytest.mark.asyncio
async def test_peek_costs_nothing_and_hides_what_a_passphrase_protects(db, people, client):
    enable(db)
    a, _, _ = people
    gated = send(db, a, link=True, passphrase=PASS)
    open_ = send(db, a, link=True)

    for _ in range(2):
        resp = await _public(client, "peek", token=gated.link_token)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requires_passphrase"] is True
        assert body["label"] is None and body["sender_name"] is None
        assert body["views_left"] == 1
    resp = await _public(client, "peek", token=open_.link_token)
    assert resp.json()["label"] == "VPN" and resp.json()["sender_name"] == "Ann"
    assert _fresh(db, gated.secret.id).views_used == 0


@pytest.mark.asyncio
async def test_a_link_reveals_once(db, people, client):
    enable(db)
    a, _, _ = people
    created = send(db, a, link=True)
    first = await _public(client, "reveal", token=created.link_token)
    assert first.status_code == 200 and first.json()["content"] == CONTENT
    assert first.headers["cache-control"].startswith("no-store")
    again = await _public(client, "reveal", token=created.link_token)
    assert again.status_code == 410 and again.json()["code"] == "SECRET_ENDED"


@pytest.mark.asyncio
async def test_an_unknown_token_is_a_404(db, client):
    resp = await _public(client, "peek", token="x" * 43)
    assert resp.status_code == 404 and resp.json()["code"] == "SECRET_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_replaced_link_stops_working(db, people, client):
    enable(db)
    a, _, _ = people
    created = send(db, a, link=True)
    _rec, new_token = secret_svc.replace_link(db, created.secret, actor=a)
    db.commit()
    old = await _public(client, "peek", token=created.link_token)
    assert old.status_code == 410 and old.json()["code"] == "SECRET_LINK_REVOKED"
    new = await _public(client, "reveal", token=new_token)
    assert new.status_code == 200 and new.json()["content"] == CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["peek", "reveal"])
async def test_the_public_routes_answer_no_get(client, path):
    """A GET that cost a view would be spent by every mail gateway that
    prefetches links before the recipient clicks."""
    resp = await client.get(f"/api/public/secrets/{path}")
    assert resp.status_code == 405


def _token_reveal(db, token, passphrase, ip):
    return secret_reveal.reveal_by_token(db, token=token, passphrase=passphrase, ip=ip)


def test_one_source_is_throttled_and_cannot_lock_the_link(db, people):
    enable(db, **{K.SECRETS_PASSPHRASE_RATE_LIMIT: "3"})
    a, _, _ = people
    created = send(db, a, link=True, passphrase=PASS)
    for _ in range(3):
        with pytest.raises(AppError) as e:
            _token_reveal(db, created.link_token, "nope", "198.51.100.1")
        assert e.value.code == "SECRET_PASSPHRASE_INVALID"
    with pytest.raises(AppError) as e:
        _token_reveal(db, created.link_token, PASS, "198.51.100.1")
    assert e.value.status_code == 429
    rec = db.query(SecretRecipient).filter_by(kind=SecretRecipientKind.link).one()
    assert rec.locked_until is None, "one address must not be able to lock everyone out"
    assert _token_reveal(db, created.link_token, PASS, "203.0.113.9").content == CONTENT


def test_failures_from_several_sources_lock_the_link_for_everyone(db, people):
    enable(db, **{K.SECRETS_PASSPHRASE_RATE_LIMIT: "3"})
    a, _, _ = people
    created = send(db, a, link=True, passphrase=PASS)
    for i in range(3):
        with pytest.raises(AppError):
            _token_reveal(db, created.link_token, "nope", f"198.51.100.{i + 1}")
    with pytest.raises(AppError) as e:
        _token_reveal(db, created.link_token, PASS, "203.0.113.9")
    assert e.value.status_code == 423 and e.value.code == "SECRET_LOCKED"
    assert _fresh(db, created.secret.id).views_used == 0


def test_burn_mode_through_a_link_ends_the_secret_when_it_was_the_only_way_in(db, people):
    enable(db, **{K.SECRETS_PASSPHRASE_MAX_FAILURES: "2"})
    a, _, _ = people
    created = send(db, a, link=True, passphrase=PASS, burn=True)
    with pytest.raises(AppError):
        _token_reveal(db, created.link_token, "nope", "198.51.100.1")
    with pytest.raises(AppError) as e:
        _token_reveal(db, created.link_token, "nope", "198.51.100.2")
    assert e.value.code == "SECRET_RECIPIENT_BURNED"
    s = _fresh(db, created.secret.id)
    assert s.state == SecretState.burned and s.ciphertext is None


def test_each_address_counts_on_its_own_link(db, people, monkeypatch):
    """Every address gets its own link; in "each person" scope one address
    using its view does not touch the other's."""
    from app.services import job_queue

    mails: list[dict] = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, *_a, **kw: mails.append(kw))
    enable(db)
    a, _, _ = people
    created = send(db, a, emails=("x@example.com", "y@example.com"))
    tokens = [
        token_of(next(w for w in m["text_body"].split() if "/s#" in w))
        for m in mails
        if m.get("to") in ("x@example.com", "y@example.com")
    ]
    assert len(tokens) == 2
    assert _token_reveal(db, tokens[0], None, "198.51.100.1").ended is False
    with pytest.raises(AppError) as e:
        _token_reveal(db, tokens[0], None, "198.51.100.1")
    assert e.value.code == "SECRET_VIEWS_EXHAUSTED"
    assert _token_reveal(db, tokens[1], None, "198.51.100.2").ended is True
    assert _fresh(db, created.secret.id).state == SecretState.burned


# ---- the claim itself ---------------------------------------------------------------


@pytest.mark.parametrize(
    "scope", [SecretViewScope.per_person, SecretViewScope.per_recipient, SecretViewScope.total]
)
def test_the_claim_itself_refuses_past_the_limit(db, people, scope):
    """The views-left check before a claim is not the only guard: every claim is
    a conditional UPDATE, so a stale read - a second worker, or the row lock
    SQLite compiles away - still cannot take a view that is not there. Driven
    with the in-memory rows deliberately left stale after the first claim."""
    enable(db)
    a, b, _ = people
    s = send(db, a, users=(b,), scope=scope).secret
    state = db.get(SecretUserState, (s.id, b.id))
    reach = secret_svc.account_reach(db, s, b.id)
    assert secret_reveal._claim_for_user(db, s, state, reach) is not None
    assert secret_reveal._claim_for_user(db, s, state, reach) is None


@pytest.mark.parametrize("scope", [SecretViewScope.per_person, SecretViewScope.total])
def test_a_links_claim_refuses_past_the_limit(db, people, scope):
    enable(db)
    a, _, _ = people
    s = send(db, a, link=True, scope=scope).secret
    rec = db.query(SecretRecipient).filter_by(secret_id=s.id).one()
    assert secret_reveal._claim_for_recipient(db, s, rec) is True
    assert secret_reveal._claim_for_recipient(db, s, rec) is False
