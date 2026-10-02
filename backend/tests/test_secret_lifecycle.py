"""What happens to secrets between views (v2.24.0): the expiry sweep, retention,
right-to-erasure, a config import, a JWT_SECRET rotation, a deleted group.

The rules: whatever ends a secret shreds it in the same transaction; an unread
secret does not outlive its expiry on disk; a secret nobody can read any more
is burned; ended records go after `retention.secret_days`; erasure removes a
person from secrets entirely; an import burns every active secret, because its
recipient rows point at the identities and groups the import rewrites; and the
rotation script re-wraps every secret's key without knowing any passphrase.
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet

from app.database import Base
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.notification import Notification, NotificationCategory
from app.models.secret import (
    Secret,
    SecretAccessEvent,
    SecretRecipient,
    SecretState,
    SecretUserState,
)
from app.models.user import User, UserRole
from app.services import config_backup as cb
from app.services import erasure, secret_reveal
from app.services import secret as secret_svc
from app.services import settings as settings_svc
from app.utils import crypto
from app.utils.timeutil import utc_now

from ._secret_helpers import CONTENT, PW, K, enable, group_with, send, setting

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rotate_jwt_secret.py"


@pytest.fixture
def people(make_user):
    a = make_user(email="a@test.local", role=UserRole.employee, password=PW, display_name="Ann")
    b = make_user(email="b@test.local", role=UserRole.employee, password=PW, display_name="Ben")
    c = make_user(email="c@test.local", role=UserRole.employee, password=PW, display_name="Cat")
    return a, b, c


def _shredded(s: Secret) -> bool:
    return s.ciphertext is None and s.key_encrypted is None and s.kdf_salt is None


# ---- the sweep -------------------------------------------------------------------


def test_the_sweep_ends_an_unread_expired_secret_and_tells_a_sender_who_asked(db, people):
    enable(db)
    a, b, _ = people
    s = send(db, a, users=(b,), notify=True).secret
    quiet = send(db, a, users=(b,)).secret
    for x in (s, quiet):
        x.expires_at = utc_now() - timedelta(seconds=1)
    db.commit()

    assert secret_svc.expire_due(db) == 2
    db.commit()
    db.expire_all()
    for x in (s, quiet):
        fresh = db.get(Secret, x.id)
        assert fresh.state == SecretState.expired and _shredded(fresh)
    notes = db.query(Notification).filter(
        Notification.user_id == a.id, Notification.category == NotificationCategory.secret_ended
    ).all()
    assert [n.payload_json["reason"] for n in notes] == ["expired_unread"]
    assert secret_svc.expire_due(db) == 0, "the sweep must be idempotent"


def test_the_sweep_burns_a_secret_whose_readers_all_left(db, people):
    enable(db)
    a, b, c = people
    g = group_with(db, a, a, b, c)
    s = send(db, a, groups=(g,)).secret
    db.query(GroupMember).filter(GroupMember.group_id == g.id, GroupMember.user_id != a.id).delete()
    db.commit()
    assert secret_svc.sweep_exhausted(db) == 1
    db.commit()
    db.expire_all()
    assert db.get(Secret, s.id).state == SecretState.burned


def test_the_sweep_leaves_a_readable_secret_alone(db, people):
    enable(db)
    a, b, _ = people
    s = send(db, a, users=(b,)).secret
    assert secret_svc.sweep_exhausted(db) == 0
    assert db.get(Secret, s.id).state == SecretState.active


def test_a_disabled_reader_does_not_keep_a_secret_alive(db, people):
    enable(db)
    a, b, _ = people
    s = send(db, a, users=(b,)).secret
    b.is_disabled = True
    db.commit()
    assert secret_svc.sweep_exhausted(db) == 1
    assert db.get(Secret, s.id).state == SecretState.burned


def test_deleting_a_group_takes_its_entry_and_the_secret_with_it(db, people):
    enable(db)
    a, b, _ = people
    g = group_with(db, a, a, b)
    s = send(db, a, groups=(g,)).secret
    db.delete(db.get(Group, g.id))
    db.commit()
    assert db.query(SecretRecipient).filter_by(secret_id=s.id).count() == 0
    assert secret_svc.sweep_exhausted(db) == 1


# ---- retention ---------------------------------------------------------------------


def test_ended_records_are_pruned_after_the_retention_window(db, people):
    enable(db)
    a, b, _ = people
    old = send(db, a, users=(b,)).secret
    recent = send(db, a, users=(b,)).secret
    live = send(db, a, users=(b,)).secret
    secret_reveal.reveal_for_user(db, secret_id=old.id, user=b, passphrase=None, ip="198.51.100.1")
    secret_svc.burn_now(db, recent, actor=a)
    db.commit()
    db.get(Secret, old.id).ended_at = utc_now() - timedelta(days=91)
    db.commit()

    assert secret_svc.prune_ended(db, older_than_days=0) == 0, "0 keeps everything"
    assert secret_svc.prune_ended(db, older_than_days=90) == 1
    db.commit()
    assert db.get(Secret, old.id) is None
    assert db.query(SecretAccessEvent).filter_by(secret_id=old.id).count() == 0
    assert db.query(SecretUserState).filter_by(secret_id=old.id).count() == 0
    assert db.get(Secret, recent.id) is not None and db.get(Secret, live.id) is not None


# ---- erasure --------------------------------------------------------------------------


def test_erasure_removes_a_sender_and_a_reader_from_secrets(db, people, make_user):
    enable(db)
    a, b, c = people
    admin = make_user(email="adm@test.local", role=UserRole.admin)
    theirs = send(db, b, users=(a,)).secret
    to_them = send(db, a, users=(b,)).secret
    shared = send(db, a, users=(b, c)).secret
    secret_reveal.reveal_for_user(db, secret_id=shared.id, user=b, passphrase=None, ip="198.51.100.2")

    result = erasure.erase_user(db, actor=admin, target=b)
    db.commit()
    db.expire_all()

    assert result["pii_purged"]["secrets_deleted"] == 1
    assert db.get(Secret, theirs.id) is None
    assert db.query(SecretUserState).filter_by(user_id=b.id).count() == 0
    assert db.query(SecretAccessEvent).filter_by(user_id=b.id).count() == 0
    # Nobody left who can read it: burned, not left waiting for its expiry.
    assert db.get(Secret, to_them.id).state == SecretState.burned
    # Cat can still read hers.
    assert db.get(Secret, shared.id).state == SecretState.active


def test_the_erasure_preflight_counts_their_secrets(db, people):
    enable(db)
    a, b, _ = people
    send(db, a, users=(b,))
    assert erasure.compute_erasure_summary(db, target=a)["secrets_to_delete"] == 1


# ---- config import --------------------------------------------------------------------


def _fresh_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()


def _user(db, email, role=UserRole.employee) -> User:
    from app.utils.crypto import argon2_hash, normalize_email

    u = User(email=normalize_email(email), password_hash=argon2_hash("x"), display_name=email, role=role)
    db.add(u)
    db.commit()
    return u


def test_a_config_import_burns_every_active_secret(db):
    settings_svc.set_value(db, key=K.SMTP_HOST, value="mail.x", actor=None)
    db.commit()
    raw = cb.build_backup(
        db, categories=["settings_branding"], secret_mode="exclude", passphrase=None, include_env=False
    )
    tgt = _fresh_session()
    actor = _user(tgt, "admin@x", UserRole.admin)
    sender, reader = _user(tgt, "s@x"), _user(tgt, "r@x")
    enable(tgt)
    s = send(tgt, sender, users=(reader,)).secret

    preview = cb.preview_backup(tgt, cb.parse_backup(raw, passphrase=None))
    assert preview.secrets_to_burn == 1
    summary = cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor, request=None)
    assert summary.secrets_to_burn == 1
    tgt.expire_all()
    fresh = tgt.get(Secret, s.id)
    assert fresh.state == SecretState.revoked and _shredded(fresh)


def test_a_config_import_remaps_the_secret_allowlists(db):
    alice = _user(db, "alice@x")
    g = Group(name="Sales", name_normalized="sales", created_by_id=alice.id)
    db.add(g)
    db.commit()
    setting(db, K.SECRETS_SEND_ALLOWED_USERS, json.dumps([alice.id]))
    setting(db, K.SECRETS_EXTERNAL_ALLOWED_GROUPS, json.dumps([g.id]))
    raw = cb.build_backup(
        db, categories=["users", "groups", "settings_branding"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )
    tgt = _fresh_session()
    actor = _user(tgt, "admin@x", UserRole.admin)
    for i in range(4):  # burn ids so source and target cannot line up by accident
        _user(tgt, f"filler{i}@x")
    tgt.add(Group(name="Filler", name_normalized="filler", created_by_id=actor.id))
    tgt.commit()
    cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor, request=None)

    talice = tgt.query(User).filter(User.email == "alice@x").one()
    tg = tgt.query(Group).filter(Group.name_normalized == "sales").one()
    assert talice.id != alice.id and tg.id != g.id
    assert json.loads(settings_svc.get(tgt, K.SECRETS_SEND_ALLOWED_USERS)) == [talice.id]
    assert json.loads(settings_svc.get(tgt, K.SECRETS_EXTERNAL_ALLOWED_GROUPS)) == [tg.id]


# ---- JWT_SECRET rotation ----------------------------------------------------------------


def _load_script():
    import importlib

    # A package (`backend/scripts/__init__.py`), so a plain import - the script
    # declares dataclasses, which need their module in sys.modules.
    return importlib.import_module("scripts.rotate_jwt_secret")


def test_rotation_rewraps_secrets_and_their_links(db, people, monkeypatch):
    enable(db)
    a, b, _ = people
    created = send(db, a, users=(b,), link=True, passphrase="long enough pw", max_views=2)
    script = _load_script()
    old_f = script._build_fernet(crypto.settings.JWT_SECRET)
    new_secret = "a-brand-new-jwt-secret-of-sufficient-length-123"
    new_f = script._build_fernet(new_secret)
    for label, model, field in (
        ("secrets.key_encrypted", Secret, "key_encrypted"),
        ("secret_recipients.token_encrypted", SecretRecipient, "token_encrypted"),
    ):
        stats = script.rotate_table(
            db, label, db.query(model).all(),
            lambda r, f=field: getattr(r, f),
            lambda r, v, f=field: setattr(r, f, v),
            is_bytes=False, old=old_f, new=new_f,
        )
        assert stats.errors == 0 and stats.rotated >= 1
    db.commit()
    # The instance now runs on the new JWT_SECRET.
    monkeypatch.setattr(crypto, "_fernet_instance", Fernet(crypto._derive_fernet_key(new_secret)))

    result = secret_reveal.reveal_for_user(
        db, secret_id=created.secret.id, user=b, passphrase="long enough pw", ip=None
    )
    assert result.content == CONTENT
    (link,) = [r for r in db.query(SecretRecipient).all() if r.token_encrypted]
    assert secret_svc.stored_url(db, link).endswith("#" + created.link_token)


def test_the_rotation_script_rotates_every_encrypted_column():
    """CLAUDE.md recorded the script's hand-maintained table list as unpinned,
    with its own comment admitting it had missed one before. Every model column
    named `*_encrypted` (a flag like `app_settings.is_encrypted` aside) must
    appear in the script as the `<table>.<column>` label it rotates under."""
    import app.models  # noqa: F401 - registers every model

    src = _SCRIPT.read_text(encoding="utf-8")
    columns = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if c.name.endswith("_encrypted") and not c.name.startswith("is_")
    ]
    assert len(columns) >= 6, columns
    missing = [c for c in columns if f'"{c}"' not in src]
    assert not missing, f"rotate_jwt_secret.py does not rotate {missing}"
    # And each label is a real rotate_table call, not a comment.
    tree = ast.parse(src)
    labels = {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "rotate_table"
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
    }
    assert set(columns) <= labels, set(columns) - labels


# ---- secret requests (v2.24.0) ----------------------------------------------------------


def test_ended_requests_are_pruned_and_their_answers_stand_alone(db, people):
    from app.models.secret_request import SecretRequest, SecretRequestTarget
    from app.services import secret_request as request_svc

    from ._secret_helpers import ask

    enable(db)
    a, b, _ = people
    answered = ask(db, a, users=(b,), emails=("guest@example.com",)).request
    answer = request_svc.answer_request(
        db, request_id=answered.id, content=CONTENT, passphrase=None, by_user=b
    )
    open_one = ask(db, a, users=(b,)).request
    db.commit()
    db.get(SecretRequest, answered.id).ended_at = utc_now() - timedelta(days=91)
    db.commit()

    assert request_svc.prune_ended(db, older_than_days=0) == 0, "0 keeps everything"
    assert request_svc.prune_ended(db, older_than_days=90) == 1
    db.commit()
    db.expire_all()
    assert db.get(SecretRequest, answered.id) is None
    assert db.query(SecretRequestTarget).filter_by(request_id=answered.id).count() == 0
    kept = db.get(Secret, answer.id)
    assert kept.state == SecretState.active and kept.is_answer and kept.request_id is None
    assert kept.label == "Router password", "the answer keeps what it needs"
    assert secret_svc.burn_reason(db, kept, a) == "requester"
    assert db.get(SecretRequest, open_one.id) is not None


def test_erasure_removes_a_requester_and_a_target_from_requests(db, people, make_user):
    from app.models.secret_request import SecretRequest, SecretRequestTarget
    from app.services import secret_request as request_svc

    from ._secret_helpers import ask

    enable(db)
    a, b, c = people
    admin = make_user(email="adm@test.local", role=UserRole.admin)
    theirs = ask(db, b, users=(a,)).request
    asked_them = ask(db, a, users=(b, c)).request
    answered = ask(db, b, users=(c,)).request
    answer = request_svc.answer_request(
        db, request_id=answered.id, content=CONTENT, passphrase=None, by_user=c
    )
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=b)
    db.commit()
    db.expire_all()

    assert result["pii_purged"]["secret_requests_deleted"] == 2
    assert db.get(SecretRequest, theirs.id) is None and db.get(SecretRequest, answered.id) is None
    assert db.query(SecretRequestTarget).filter_by(target_user_id=b.id).count() == 0
    assert db.get(SecretRequest, asked_them.id) is not None, "Cat can still answer Ann"
    # Nobody left who can read Ben's answer: burned, shredded.
    gone = db.get(Secret, answer.id)
    assert gone.state == SecretState.burned and _shredded(gone)


def test_a_config_import_cancels_every_open_request(db):
    from app.models.secret_request import SecretRequest, SecretRequestState

    from ._secret_helpers import ask

    settings_svc.set_value(db, key=K.SMTP_HOST, value="mail.x", actor=None)
    db.commit()
    raw = cb.build_backup(
        db, categories=["settings_branding"], secret_mode="exclude", passphrase=None, include_env=False
    )
    tgt = _fresh_session()
    actor = _user(tgt, "admin@x", UserRole.admin)
    asker, asked = _user(tgt, "s@x"), _user(tgt, "r@x")
    enable(tgt)
    req = ask(tgt, asker, users=(asked,), link=True).request

    preview = cb.preview_backup(tgt, cb.parse_backup(raw, passphrase=None))
    assert preview.secret_requests_to_cancel == 1
    summary = cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor, request=None)
    assert summary.secret_requests_to_cancel == 1
    tgt.expire_all()
    assert tgt.get(SecretRequest, req.id).state == SecretRequestState.cancelled
