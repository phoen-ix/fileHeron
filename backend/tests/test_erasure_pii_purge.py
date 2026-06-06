"""Regression: GDPR erasure must purge plaintext PII outside the users row.

Audit finding M2 - `erase_user` anonymised the users row but left plaintext
email in `login_attempts` / `invite_tokens`, device fingerprints in
`known_devices`, and the user's own `notifications`, because anonymise-by-
UPDATE never fires the FK CASCADEs. After erasure, querying for the original
email must return nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.invite_token import InviteToken
from app.models.known_device import KnownDevice
from app.models.login_attempt import LoginAttempt
from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.services import erasure as erasure_svc


def _naive_utc():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def test_erasure_purges_plaintext_pii(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="victim@test.local", role=UserRole.client)
    victim_email = victim.email

    # Seed PII-bearing rows across tables that erasure used to miss.
    db.add(LoginAttempt(email=victim_email, ip="203.0.113.9", outcome="success", attempted_at=_naive_utc()))
    db.add(LoginAttempt(email=victim_email, ip="203.0.113.9", outcome="bad_password", attempted_at=_naive_utc()))
    db.add(
        InviteToken(
            token_hash="a" * 64,
            email=victim_email,  # invite sent TO the victim
            target_role=UserRole.client,
            created_by_id=admin.id,
            created_at=_naive_utc(),
            expires_at=_naive_utc() + timedelta(days=1),
        )
    )
    db.add(
        InviteToken(
            token_hash="b" * 64,
            email="thirdparty@test.local",  # invite the victim CREATED
            target_role=UserRole.client,
            created_by_id=victim.id,
            created_at=_naive_utc(),
            expires_at=_naive_utc() + timedelta(days=1),
        )
    )
    db.add(KnownDevice(user_id=victim.id, ua_fingerprint_hash="f" * 64, ip_geohash="u4pruyd"))
    db.add(
        Notification(
            user_id=victim.id,
            category=NotificationCategory.share_created,
            payload_json={"sender_name": "Someone", "filename": "secret.pdf"},
        )
    )
    db.commit()

    erasure_svc.erase_user(db, actor=admin, target=victim)
    db.commit()

    # No trace of the original email anywhere.
    assert db.query(LoginAttempt).filter(LoginAttempt.email == victim_email).count() == 0
    assert db.query(InviteToken).filter(InviteToken.email == victim_email).count() == 0
    # Invites the victim created are gone too (carried third-party emails).
    assert db.query(InviteToken).filter(InviteToken.created_by_id == victim.id).count() == 0
    # Device fingerprints + own notifications purged.
    assert db.query(KnownDevice).filter(KnownDevice.user_id == victim.id).count() == 0
    assert db.query(Notification).filter(Notification.user_id == victim.id).count() == 0
