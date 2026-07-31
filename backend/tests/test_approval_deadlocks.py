"""Two places where the approval workflow told the user to do the impossible.

flow-approval-9  `create_pending` attaches an upload to a pending share on
                 purpose - the owner keeps assembling while it waits for
                 review. But `register_files_added`, the batch-complete signal,
                 refused any share that was not `active`. So the files were
                 already on the share and the caller was told the batch had
                 failed: the SPA showed an error, the owner re-uploaded, and
                 the bytes and quota charge doubled, with no share-level audit
                 row for either attempt.

flow-approval-6  `approve_share` refuses a share whose expiry has already
                 passed and told the approver to ask the sender to resubmit.
                 `update_share_expiry` then refused the sender, because the
                 share is not `active`. The instruction and the code
                 contradicted each other and the only way out was to discard
                 the share and rebuild it.

From the 2026-07-30 audit.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import share as share_svc
from app.utils.timeutil import utc_now


@pytest.fixture
def pending(db, make_user):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval, expires_at=utc_now() + timedelta(days=1),
    )
    db.add(sh)
    db.commit()
    return sh, owner


# --- flow-approval-9 --------------------------------------------------------


def test_the_batch_signal_is_accepted_on_a_pending_share(db, pending):
    sh, owner = pending
    share_svc.register_files_added(
        db, user=owner, share=sh, file_ids=[], notify=False
    )
    db.commit()


def test_a_terminal_share_is_still_refused(db, make_user):
    """Control: this must not become a way to mutate a revoked share."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.revoked
    )
    db.add(sh)
    db.commit()
    with pytest.raises(AppError) as exc:
        share_svc.register_files_added(
            db, user=owner, share=sh, file_ids=[], notify=False
        )
    assert exc.value.code == "SHARE_NOT_ACTIVE"


def test_recipients_are_not_notified_about_a_share_they_cannot_see(db, pending):
    """A pending share is invisible to its recipients. Telling them files were
    added both confuses them and discloses that the share exists."""
    import inspect

    src = inspect.getsource(share_svc.register_files_added)
    assert "share.state == ShareState.active" in src


# --- flow-approval-6 --------------------------------------------------------


def test_the_sender_can_extend_a_pending_share(db, pending):
    """This is what the approval error instructs them to do."""
    sh, owner = pending
    share_svc.update_share_expiry(
        db, user=owner, share=sh, new_expires_at=utc_now() + timedelta(days=7)
    )
    db.commit()
    assert sh.expires_at > utc_now()


def test_the_two_messages_agree(db):
    """The defect was not either message alone - it was that following one hit
    the other."""
    import inspect

    approve = inspect.getsource(share_svc.approve_share)
    assert "ask the sender to extend it" in approve
    extend = inspect.getsource(share_svc.update_share_expiry)
    assert "pending_approval" in extend


def test_a_revoked_share_still_cannot_be_extended(db, make_user):
    """Control: the reason the guard exists is that the bytes may be gone."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.revoked)
    db.add(sh)
    db.commit()
    with pytest.raises(AppError) as exc:
        share_svc.update_share_expiry(
            db, user=owner, share=sh, new_expires_at=utc_now() + timedelta(days=1)
        )
    assert exc.value.code == "SHARE_NOT_ACTIVE"
