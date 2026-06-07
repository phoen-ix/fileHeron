"""M6: a malformed-but-parseable config backup is rejected up front (before the
irreversible active-share invalidation), not applied half-way."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services.config_backup import _validate_backup_payload


def test_rejects_bad_notification_category(make_user, db):
    actor = make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as e:
        _validate_backup_payload(
            {
                "users": {
                    "users": [{"email": "a@b.c"}],
                    "user_notification_preferences": [
                        {"user_id": 1, "category": "NOT_A_CATEGORY", "channel": "both"}
                    ],
                }
            },
            actor,
        )
    assert e.value.code == "BACKUP_CORRUPT"


def test_rejects_user_missing_email(make_user, db):
    actor = make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as e:
        _validate_backup_payload({"users": {"users": [{"display_name": "x"}]}}, actor)
    assert e.value.code == "BACKUP_CORRUPT"


def test_accepts_minimal_valid_payload(make_user, db):
    actor = make_user(email="admin@test.local", role=UserRole.admin)
    # Should not raise.
    _validate_backup_payload(
        {
            "users": {
                "users": [{"email": "a@b.c"}],
                "user_notification_preferences": [
                    {"user_id": 1, "category": "share_created", "channel": "both"}
                ],
            },
            "groups": {"groups": [{"id": 1, "name": "G", "name_normalized": "g"}]},
        },
        actor,
    )
