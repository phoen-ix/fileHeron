"""Asking for a secret and answering (v2.24.0): who may ask whom, who may
answer, first answer wins, expiry without the sweep, cancel, anonymous answers,
the requester's burn and both passphrase layers.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.group_member import GroupMember
from app.models.notification import Notification, NotificationCategory
from app.models.secret import Secret, SecretState
from app.models.secret_request import SecretRequest, SecretRequestState, SecretRequestTarget
from app.models.user import UserRole
from app.services import secret as secret_svc
from app.services import secret_request as request_svc
from app.services import secret_reveal
from app.utils.timeutil import utc_now

from ._secret_helpers import PW, K, ask, connect, enable, group_with, setting

ANSWER = "router admin: Hx9#qL2v!"


@pytest.fixture
def people(make_user):
    ann = make_user(email="ann@test.local", role=UserRole.employee, password=PW, display_name="Ann")
    ben = make_user(email="ben@test.local", role=UserRole.employee, password=PW, display_name="Ben")
    cat = make_user(email="cat@test.local", role=UserRole.employee, password=PW, display_name="Cat")
    return ann, ben, cat


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


def _answer(db, req_id, user=None, token=None, content=ANSWER, passphrase=None) -> Secret:
    secret = request_svc.answer_request(
        db,
        request_id=req_id,
        content=content,
        passphrase=passphrase,
        by_user=user,
        by_token=token,
    )
    db.commit()
    return secret


# ---- asking ----------------------------------------------------------------------


def test_asking_is_refused_while_the_switch_is_off(db, people):
    ann, ben, _ = people
    with pytest.raises(AppError) as exc:
        ask(db, ann, users=(ben,))
    assert _code(exc) == "SECRETS_DISABLED"


def test_a_client_asks_only_connected_employees_and_never_outside(db, people, make_user):
    enable(db)
    ann, ben, _ = people
    client = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    with pytest.raises(AppError) as exc:
        ask(db, client, users=(ann,))
    assert _code(exc) == "RECIPIENT_NOT_CONNECTED"
    connect(db, client, ann)
    assert ask(db, client, users=(ann,)).request.state == SecretRequestState.open
    for kw in ({"emails": ("x@example.com",)}, {"link": True}):
        with pytest.raises(AppError) as exc:
            ask(db, client, users=(ann,), **kw)
        assert _code(exc) == "SECRET_EXTERNAL_NOT_ALLOWED"


def test_the_ceilings_and_the_limits_are_checked(db, people):
    enable(db)
    ann, ben, _ = people
    with pytest.raises(AppError) as exc:
        ask(db, ann, users=(ben,), open_for=timedelta(days=91))
    assert _code(exc) == "SECRET_LIMIT_EXCEEDED"
    with pytest.raises(AppError) as exc:
        ask(db, ann, users=(ben,), views=None, lifetime=None)
    assert _code(exc) == "SECRET_NEEDS_LIMIT"
    with pytest.raises(AppError) as exc:
        ask(db, ann, users=(ben,), views=101)
    assert _code(exc) == "SECRET_LIMIT_EXCEEDED"
    with pytest.raises(AppError) as exc:
        ask(db, ann)
    assert _code(exc) == "SECRET_NO_RECIPIENTS"
    with pytest.raises(AppError) as exc:
        ask(db, ann, users=(ben,), label="   ")
    assert _code(exc) == "SECRET_REQUEST_LABEL_INVALID"


def test_account_targets_are_told(db, people):
    enable(db)
    ann, ben, cat = people
    g = group_with(db, ann, ann, cat)
    ask(db, ann, users=(ben,), groups=(g,))
    told = {
        n.user_id
        for n in db.query(Notification).filter(
            Notification.category == NotificationCategory.secret_requested
        )
    }
    assert told == {ben.id, cat.id}, "the requester is never asked their own request"


# ---- answering -------------------------------------------------------------------


def test_an_answer_is_a_secret_only_the_requester_can_read(db, people):
    enable(db)
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,), views=2).request
    secret = _answer(db, req.id, user=ben)
    assert secret.is_answer and secret.created_by_id == ben.id
    assert secret.label == "Router password" and secret.max_views == 2
    db.refresh(req)
    assert req.state == SecretRequestState.fulfilled and req.fulfilled_by_user_id == ben.id
    assert secret_svc.viewer_role(db, secret, ann) == "recipient"
    result = secret_reveal.reveal_for_user(db, secret_id=secret.id, user=ann, passphrase=None, ip=None)
    assert result.content == ANSWER
    with pytest.raises(AppError) as exc:
        secret_reveal.reveal_for_user(db, secret_id=secret.id, user=ben, passphrase=None, ip=None)
    assert _code(exc) == "SECRET_REVEAL_FORBIDDEN", "the answerer is the sender: never a reader"
    notes = db.query(Notification).filter(
        Notification.user_id == ann.id,
        Notification.category == NotificationCategory.secret_request_update,
    ).all()
    assert [n.payload_json["outcome"] for n in notes] == ["answered"]


def test_the_first_answer_wins(db, people):
    enable(db)
    ann, ben, cat = people
    req = ask(db, ann, users=(ben, cat), link=True).request
    _answer(db, req.id, user=ben)
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=cat)
    assert _code(exc) == "SECRET_REQUEST_CLOSED"
    assert db.query(Secret).filter(Secret.request_id == req.id).count() == 1
    link = db.query(SecretRequestTarget).filter(
        SecretRequestTarget.request_id == req.id, SecretRequestTarget.kind == "link"
    ).one()
    assert link.token_encrypted is None and link.token_hash, "the link closes, its hash stays"


def test_the_claim_itself_refuses_a_second_answer(db, people, monkeypatch):
    """The state check is not the guard: with it out of the way, the conditional
    UPDATE on `state = 'open'` still lets exactly one answer land."""
    enable(db)
    ann, ben, cat = people
    req = ask(db, ann, users=(ben, cat)).request
    _answer(db, req.id, user=ben)
    monkeypatch.setattr(request_svc, "_assert_open", lambda _req: None)
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=cat)
    assert _code(exc) == "SECRET_REQUEST_CLOSED"
    assert db.query(Secret).filter(Secret.request_id == req.id).count() == 1


def test_the_requester_cannot_answer_and_strangers_cannot_see(db, people, make_user):
    enable(db)
    ann, ben, cat = people
    req = ask(db, ann, users=(ben,)).request
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=ann)
    assert _code(exc) == "SECRET_REQUEST_SELF"
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=cat)
    assert _code(exc) == "SECRET_REQUEST_NOT_FOUND"


def test_the_requester_never_answers_even_through_a_planted_target_row(db, people):
    """Creation never makes the requester a target (SELF_SHARE, and group
    snapshots leave them out); the answer path refuses them on its own too."""
    enable(db)
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,)).request
    db.add(SecretRequestTarget(request_id=req.id, kind="user", target_user_id=ann.id))
    db.commit()
    assert request_svc.answering_target(db, req, ann) is None
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=ann)
    assert _code(exc) == "SECRET_REQUEST_SELF"


def test_group_members_answer_if_member_then_and_now(db, people, make_user):
    enable(db)
    ann, ben, cat = people
    dan = make_user(email="dan@test.local", role=UserRole.employee, password=PW)
    g = group_with(db, ann, ann, ben, cat)
    req = ask(db, ann, groups=(g,)).request
    # Joined after the request: was not a member then.
    db.add(GroupMember(group_id=g.id, user_id=dan.id))
    # Left since: is not a member now.
    db.query(GroupMember).filter(GroupMember.group_id == g.id, GroupMember.user_id == cat.id).delete()
    db.commit()
    assert request_svc.answering_target(db, req, dan) is None
    assert request_svc.answering_target(db, req, cat) is None
    assert request_svc.answering_target(db, req, ben) is not None
    _answer(db, req.id, user=ben)


def test_expiry_is_checked_in_the_answer_path_itself(db, people):
    enable(db)
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,)).request
    db.query(SecretRequest).filter(SecretRequest.id == req.id).update(
        {SecretRequest.expires_at: utc_now() - timedelta(seconds=1)}
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=ben)
    assert _code(exc) == "SECRET_REQUEST_CLOSED"
    assert exc.value.details == {"reason": "expired"}
    # The sweep then closes it and tells the requester.
    assert request_svc.expire_due(db) == 1
    db.commit()
    db.refresh(req)
    assert req.state == SecretRequestState.expired
    outcomes = [
        n.payload_json["outcome"]
        for n in db.query(Notification).filter(
            Notification.user_id == ann.id,
            Notification.category == NotificationCategory.secret_request_update,
        )
    ]
    assert outcomes == ["expired"]
    assert request_svc.expire_due(db) == 0, "the sweep must be idempotent"


def test_a_cancelled_request_takes_no_answer(db, people, make_user):
    enable(db)
    ann, ben, cat = people
    admin = make_user(email="adm@test.local", role=UserRole.admin, password=PW)
    req = ask(db, ann, users=(ben,)).request
    with pytest.raises(AppError) as exc:
        request_svc.cancel_request(db, req, actor=cat)
    assert _code(exc) == "FORBIDDEN"
    request_svc.cancel_request(db, req, actor=ann)
    db.commit()
    with pytest.raises(AppError) as exc:
        _answer(db, req.id, user=ben)
    assert exc.value.details == {"reason": "cancelled"}
    other = ask(db, ann, users=(ben,)).request
    request_svc.cancel_request(db, other, actor=admin)
    db.commit()
    db.refresh(other)
    assert other.state == SecretRequestState.cancelled


# ---- anonymous answers -------------------------------------------------------------


def test_an_answer_through_a_link_has_no_sender_and_says_how_it_came(db, people, client, login_as):
    enable(db)
    ann, *_ = people
    created = ask(db, ann, emails=("guest@example.com",), link=True)
    secret = _answer(db, created.request.id, token=created.link_token)
    assert secret.created_by_id is None and secret.answered_by_email is None
    rows, total = secret_svc.list_for_user(
        db, user=ann, box="received", states=None, q="", page=1, page_size=50
    )
    assert [s.id for s in rows] == [secret.id] and total == 1, "an answer without a sender is listed"


def test_an_answer_through_a_mailed_link_names_the_address(db, people, monkeypatch):
    from app.services import job_queue

    jobs: list[dict] = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, *_a, **kw: jobs.append(kw))
    enable(db)
    ann, *_ = people
    created = ask(db, ann, emails=("guest@example.com",))
    mail = next(j for j in jobs if j.get("to") == "guest@example.com")
    token = next(w for w in mail["text_body"].split() if "/r#" in w).rsplit("#", 1)[1]
    secret = _answer(db, created.request.id, token=token)
    assert secret.answered_by_email == "guest@example.com"


def test_a_token_answers_only_its_own_request(db, people):
    enable(db)
    ann, ben, _ = people
    one = ask(db, ann, link=True)
    other = ask(db, ann, users=(ben,))
    with pytest.raises(AppError) as exc:
        _answer(db, other.request.id, token=one.link_token)
    assert _code(exc) == "SECRET_REQUEST_NOT_FOUND"


# ---- the answer, afterwards ----------------------------------------------------------


def test_the_requester_may_burn_their_answer_unread(db, people):
    enable(db)
    ann, ben, cat = people
    req = ask(db, ann, users=(ben,)).request
    secret = _answer(db, req.id, user=ben)
    assert secret_svc.burn_reason(db, secret, cat) is None
    assert secret_svc.burn_reason(db, secret, ann) == "requester"
    secret_svc.burn_now(db, secret, actor=ann)
    db.commit()
    db.refresh(secret)
    assert secret.state == SecretState.revoked and secret.ciphertext is None
    # The answerer is not told - discarding your own answer is nobody's news.
    assert not db.query(Notification).filter(
        Notification.user_id == ben.id, Notification.category == NotificationCategory.secret_ended
    ).count()


def test_both_passphrases_guard_the_answer_and_a_wrong_one_costs_no_view(db, people):
    enable(db)
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,), passphrase="the requester's own").request
    secret = _answer(db, req.id, user=ben, passphrase="the answerer's own")
    assert secret.has_request_passphrase and secret.has_passphrase
    reveal = secret_reveal.reveal_for_user
    with pytest.raises(AppError) as exc:
        reveal(db, secret_id=secret.id, user=ann, passphrase="the answerer's own", ip=None)
    assert _code(exc) == "SECRET_REQUEST_PASSPHRASE_REQUIRED"
    with pytest.raises(AppError) as exc:
        reveal(
            db, secret_id=secret.id, user=ann, passphrase="the answerer's own",
            request_passphrase="wrong", ip=None,
        )
    assert _code(exc) == "SECRET_REQUEST_PASSPHRASE_INVALID" and exc.value.status_code == 403
    with pytest.raises(AppError) as exc:
        reveal(
            db, secret_id=secret.id, user=ann, passphrase="wrong",
            request_passphrase="the requester's own", ip=None,
        )
    assert _code(exc) == "SECRET_PASSPHRASE_INVALID"
    result = reveal(
        db, secret_id=secret.id, user=ann, passphrase="the answerer's own",
        request_passphrase="the requester's own", ip=None,
    )
    assert result.content == ANSWER and result.ended is True, "one view, and only the right one"


def test_the_answers_lifetime_starts_when_it_arrives_and_respects_the_ceiling(db, people):
    enable(db)
    setting(db, K.SECRETS_MAX_LIFETIME_DAYS, "2")
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,), views=None, lifetime=timedelta(days=5)).request
    before = utc_now()
    secret = _answer(db, req.id, user=ben)
    assert secret.expires_at is not None
    assert timedelta(days=2) - timedelta(minutes=1) <= secret.expires_at - before <= timedelta(days=2, minutes=1)
