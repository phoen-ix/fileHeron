"""Fixes for the 2026-08-15 audit backlog's recorded LOWs.

One file rather than nine, because these are one wave rather than one topic.
Each test names the item it pins and is written so that reverting the fix turns
it red — several of these exist precisely because the original evidence was a
test that passed against absent code.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"


# --- P4: email_verified was a cast, not a check ----------------------------


def _claims_verified(claims: dict) -> bool:
    from app.models.oidc_provider import OIDCPreset, OIDCProvider
    from app.services.oidc import _extract_email

    provider = OIDCProvider(preset=OIDCPreset.custom, issuer_url="https://idp.test")
    return _extract_email(claims, provider)[1]


def test_a_string_email_verified_claim_is_not_believed():
    """`bool("false")` is True. An IdP emitting this claim as a JSON string -
    out of spec, but real - therefore asserted the verification it was actively
    denying, and the auto-link gate believed it."""
    assert _claims_verified({"email": "a@x.test", "email_verified": "false"}) is False
    assert _claims_verified({"email": "a@x.test", "email_verified": "no"}) is False
    assert _claims_verified({"email": "a@x.test", "email_verified": ""}) is False
    # Positive control: the conformant shapes must still pass, or the fix has
    # simply broken SSO instead.
    assert _claims_verified({"email": "a@x.test", "email_verified": True}) is True
    assert _claims_verified({"email": "a@x.test", "email_verified": "true"}) is True
    assert _claims_verified({"email": "a@x.test"}) is False


# --- P9: cancel racing a confirm reported success --------------------------


def test_the_self_cancel_is_a_single_conditional_update():
    """Pins the mechanism, following the precedent this repo already set for the
    sibling branch (`test_supersede_is_a_single_conditional_update`): the race
    is a true interleaving and SQLite cannot exhibit it, so the thing worth
    asserting is that the write carries its own state predicates.

    The self/admin branch was a read-then-assign — SQLAlchemy emitted
    `UPDATE ... WHERE id = ?` with no state predicate — so a cancel whose SELECT
    landed just before `confirm_email_change` committed stamped `cancelled_at`
    on an already-USED row and returned 1, telling someone their account was
    safe moments after the address had changed and their sessions were revoked.

    `.all()` appears nowhere in this function, in code or comment (checked), so
    unlike the upload-guard tests this substring cannot be satisfied by prose."""
    import inspect

    from app.services import email_change as ec

    src = inspect.getsource(ec.cancel_email_change)
    assert ".all()" not in src, "read-then-assign is back"
    assert src.count("update(EmailChangeToken)") == 2, (
        "both branches must write through a conditional UPDATE"
    )
    assert "EmailChangeToken.used_at.is_(None)" in src


def test_the_self_cancel_leaves_other_users_alone(db, make_user):
    """Behavioural control for the same change: an over-broad WHERE would
    cancel everybody's pending change, which is the failure mode a hand-written
    UPDATE invites."""
    from app.models.email_change_token import EmailChangeToken
    from app.services import email_change as ec
    from app.utils.crypto import sha256_hex
    from app.utils.timeutil import utc_now

    mine = make_user(email="u@test.local", role=UserRole.employee, password=PW)
    other = make_user(email="o@test.local", role=UserRole.employee, password=PW)
    for owner, tag in ((mine, "a"), (other, "b")):
        db.add(
            EmailChangeToken(
                user_id=owner.id, new_email=f"new-{tag}@test.local",
                new_token_hash=sha256_hex(tag),
                cancel_token_hash=sha256_hex(tag + "c"),
                expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
            )
        )
    db.commit()

    assert ec.cancel_email_change(db, user=mine, request=None) == 1
    db.commit()

    still_live = (
        db.query(EmailChangeToken)
        .filter(
            EmailChangeToken.user_id == other.id,
            EmailChangeToken.cancelled_at.is_(None),
        )
        .count()
    )
    assert still_live == 1, "cancelling one user's change cancelled another's"

    # And a second cancel finds nothing left to do.
    assert ec.cancel_email_change(db, user=mine, request=None) == 0


# --- P17: a non-ASCII fingerprint 500'd instead of 409ing ------------------


def test_a_non_ascii_fingerprint_is_refused_by_the_schema():
    """`secrets.compare_digest` raises TypeError on non-ASCII str operands, so
    the value reached it and produced a 500 from the one route whose whole job
    is to answer 409 CONTENT_CHANGED."""
    from pydantic import ValidationError

    from app.schemas.share import ApproveShareRequest, DecideAddedFilesRequest

    for model, extra in (
        (ApproveShareRequest, {}),
        (DecideAddedFilesRequest, {"approve": True}),
    ):
        with pytest.raises(ValidationError):
            model(content_fingerprint="ü" * 8, **extra)
        with pytest.raises(ValidationError):
            model(content_fingerprint="zzzz", **extra)  # hex-only
        # Positive control: the real producer format still validates.
        assert model(content_fingerprint="0a1b2c3d4e5f6789", **extra)


# --- L13: disabled legal pages were served anyway --------------------------


@pytest.mark.asyncio
async def test_a_disabled_legal_page_serves_no_content(db, client):
    """`enabled` was computed and then not used as a gate — the only thing
    honouring it was the SPA. So an unpublished draft imprint, which is where a
    company address or a not-yet-agreed legal position lives, was one anonymous
    curl away."""
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_EN,
        value="<p>draft address</p>", actor=None,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_ENABLED, value="false", actor=None
    )
    db.commit()

    body = (await client.get("/api/legal/imprint")).json()
    assert body["enabled"] is False
    assert body["html_en"] == "", "a disabled page served its draft body"

    # Positive control: enabling it publishes, so this cannot pass by the route
    # simply being broken.
    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_ENABLED, value="true", actor=None
    )
    db.commit()
    body = (await client.get("/api/legal/imprint")).json()
    assert body["enabled"] is True
    assert "draft address" in body["html_en"]


# --- L14: the SPA 404 beacon had no aggregate ceiling ----------------------


@pytest.mark.asyncio
async def test_the_404_beacon_has_a_global_ceiling(client, monkeypatch):
    """The per-IP cap bounded one source at 10/min and nothing bounded the sum,
    so N sources wrote error_log rows and ARQ jobs without limit. The CSP sink
    beside it had both caps and a comment claiming it was the only entry point
    that had lacked one."""
    from app.services import error_log, job_queue, rate_limit

    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    seen: list[str] = []

    def _buckets(bucket, ip, limit, window_sec=900):
        seen.append(bucket)
        return bucket != "client_404_global"  # the global one is exhausted

    monkeypatch.setattr(rate_limit, "check_ip_allowed", _buckets)
    enqueued: list = []
    monkeypatch.setattr(job_queue, "enqueue", lambda *a, **kw: enqueued.append(a))

    resp = await client.post("/api/telemetry/page-404", json={"path": "/nope"})
    assert resp.status_code == 204
    assert "client_404_global" in seen, "no global ceiling is consulted"
    assert enqueued == [], "the global ceiling did not shed the event"


# --- P6: the inbound body cap counted characters, not bytes ----------------


def test_a_small_html_mail_cannot_blow_the_packet_limit():
    """nh3 escapes on the way out, so capping the INPUT cannot bound the row.

    4 M bare ampersands is ~4 MB on the wire - nowhere near MAX_MESSAGE_BYTES -
    and re-serialises to ~20 MB of `&amp;`, past MariaDB's 16 MB default
    `max_allowed_packet`. The INSERT then fails, imap_poll swallows it, and the
    UID highwater was already committed: the mail is permanently invisible to
    fileHeron while sitting unread on the server."""
    from email.message import EmailMessage

    from app.services.inbound_parse import _MAX_BODY_TOTAL, parse

    msg = EmailMessage()
    msg["From"] = "sender@test.local"
    msg["To"] = "inbox@test.local"
    msg["Subject"] = "amplification"
    msg.set_content("plain")
    msg.add_alternative("&" * 900_000, subtype="html")

    parsed = parse(msg.as_bytes())
    total = len((parsed.body_html or "").encode()) + len((parsed.body_text or "").encode())
    assert total <= _MAX_BODY_TOTAL, (
        f"stored body is {total} bytes against a {_MAX_BODY_TOTAL}-byte budget"
    )


def test_multibyte_text_is_measured_in_bytes():
    """`len()` on a str counts codepoints, so a limit named in bytes was four
    times too generous for astral-plane text."""
    from email.message import EmailMessage

    from app.services.inbound_parse import _MAX_BODY_TOTAL, parse

    msg = EmailMessage()
    msg["From"] = "sender@test.local"
    msg["Subject"] = "wide"
    msg.set_content("😀" * 1_200_000)  # 4 bytes each

    parsed = parse(msg.as_bytes())
    assert len((parsed.body_text or "").encode()) <= _MAX_BODY_TOTAL


# --- P10: the approver was locked out of the share they must decide --------


def _approval_on(db, *, mode: str, content_review: bool = True) -> None:
    from app.services import settings as settings_svc

    k = settings_svc.Keys
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_ENABLED, value="true", actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_APPROVER_MODE, value=mode, actor=None)
    settings_svc.set_value(
        db,
        key=k.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW,
        value="true" if content_review else "false",
        actor=None,
    )
    db.commit()


def _share_with_pending_file(db, owner):
    """An ACTIVE share carrying one file that still needs a decision."""
    from datetime import timedelta

    from app.models.file import File, FileApprovalState, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.utils.timeutil import utc_now

    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        subject="appendix", expires_at=utc_now() + timedelta(days=1),
    )
    db.add(share)
    db.flush()
    f = File(
        share_id=share.id, uploaded_by_id=owner.id, original_filename="late.pdf",
        size_bytes=10, state=FileState.clean,
        approval_state=FileApprovalState.pending_review,
    )
    db.add(f)
    db.commit()
    return share, f


def test_a_non_admin_approver_can_open_the_share_they_must_decide(db, make_user):
    """Every existing approval test uses an ADMIN approver, and admin
    short-circuits `is_authorized_to_download` — which is the check that was
    refusing everyone else. That is why this survived.

    With `approver_mode=employees_admins`, an employee approver was 403'd off
    the detail page and the file bytes of a share they had just been notified
    about, and whose `content_fingerprint` they must echo to decide. They could
    approve blind over the API and never see what they were approving."""
    from app.services import share as share_svc

    _approval_on(db, mode="employees_admins")
    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    approver = make_user(email="appr@test.local", role=UserRole.employee, password=PW)
    share, f = _share_with_pending_file(db, owner)

    assert share_svc.is_authorized_to_view(db, user=approver, share=share) is True
    # And the bytes of the very file awaiting their decision.
    share_svc.assert_share_file_access(db, user=approver, share=share, file=f)


def test_that_access_is_scoped_to_shares_actually_awaiting_review(db, make_user):
    """The control that keeps the fix from being a much larger grant: an
    approver must NOT gain a view of every active outbound share, only of the
    ones carrying a decision they owe."""
    from datetime import timedelta

    from app.models.share import Share, ShareKind, ShareState
    from app.services import share as share_svc
    from app.utils.timeutil import utc_now

    _approval_on(db, mode="employees_admins")
    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    approver = make_user(email="appr@test.local", role=UserRole.employee, password=PW)

    ordinary = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        subject="nothing to review", expires_at=utc_now() + timedelta(days=1),
    )
    db.add(ordinary)
    db.commit()

    assert share_svc.is_authorized_to_view(db, user=approver, share=ordinary) is False


def test_the_share_appears_in_the_approver_queue(db, make_user):
    """It was filtered on `state == pending_approval`, so an active share with
    appended files never showed up — while `schemas/share.py` promised the
    approvals view would offer it."""
    from app.services import share as share_svc

    _approval_on(db, mode="employees_admins")
    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    approver = make_user(email="appr@test.local", role=UserRole.employee, password=PW)
    share, _f = _share_with_pending_file(db, owner)

    rows, total = share_svc.list_pending_approvals(db, user=approver)
    assert total == 1 and rows[0].id == share.id

    # Control: the owner never reviews their own.
    own_rows, own_total = share_svc.list_pending_approvals(db, user=owner)
    assert own_total == 0


# --- Lead 32: assert_safe_host had no test at all --------------------------
#
# Its two call sites (the SMTP and IMAP test-connection host overrides) are the
# strongest SSRF primitive in the product - they connect and hand the caller the
# error text - and no-oping the whole function left the suite green. What
# follows pins the policy AND the deliberate fail-open, which is the part an
# unrelated tidy-up is most likely to "correct" into a raise, turning every
# admin typo into an opaque 400.


def _resolves_to(monkeypatch, addr: str) -> None:
    import socket as _socket

    monkeypatch.setattr(
        "app.utils.net.socket.getaddrinfo",
        lambda host, port, *a, **kw: [
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", (addr, port or 25))
        ],
        raising=True,
    )


@pytest.mark.parametrize("addr", ["127.0.0.1", "169.254.169.254", "0.0.0.0"])
def test_assert_safe_host_refuses_the_always_blocked_addresses(monkeypatch, addr):
    """Loopback, link-local (the cloud-metadata address) and unspecified are
    refused whatever `allow_private` says."""
    from app.middleware.errors import AppError
    from app.utils.net import assert_safe_host

    _resolves_to(monkeypatch, addr)
    with pytest.raises(AppError) as exc:
        assert_safe_host("mail.example", 25)
    assert exc.value.code in ("URL_BLOCKED", "URL_NOT_ALLOWED")


def test_assert_safe_host_allows_a_lan_mail_server_by_default(monkeypatch):
    """`allow_private` defaults True because a mail server on the same LAN is
    an ordinary deployment — and it must still be refusable explicitly."""
    from app.middleware.errors import AppError
    from app.utils.net import assert_safe_host

    _resolves_to(monkeypatch, "10.0.0.5")
    assert_safe_host("mail.corp.local", 25)  # default: permitted
    with pytest.raises(AppError):
        assert_safe_host("mail.corp.local", 25, allow_private=False)


def test_assert_safe_host_fails_open_on_an_unresolvable_name(monkeypatch):
    """Deliberate, and the opposite of `assert_public_http_url`. These endpoints
    exist to report a connection error legibly; a name that does not resolve
    cannot be an SSRF target, and raising here would turn every typo into an
    opaque 400 instead of the hint the admin needs."""
    import socket as _socket

    from app.utils.net import assert_safe_host

    def _boom(*a, **kw):
        raise _socket.gaierror("Name or service not known")

    monkeypatch.setattr("app.utils.net.socket.getaddrinfo", _boom, raising=True)
    assert assert_safe_host("nope.invalid", 25) is None


def test_assert_safe_host_refuses_an_empty_host():
    from app.middleware.errors import AppError
    from app.utils.net import assert_safe_host

    with pytest.raises(AppError):
        assert_safe_host("   ", 25)


# --- post-release review finding: the P10 budget regression -----------------
#
# Found by an adversarial review AFTER v2.13.1 shipped. P10 admitted a
# non-recipient approver to an ACTIVE share carrying files awaiting review;
# the download routes' budget branch keys on `state == active`, and their
# `is_review` flag only recognised `pending_approval`. So the approver's
# review download spent the recipients' budget, and a `download_limit=1`
# share was exhausted before a single recipient fetched anything.


def _approval_share(db, make_user, *, awaiting: bool):
    from datetime import timedelta

    from app.models.file import File, FileApprovalState, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.utils.timeutil import utc_now

    owner = make_user(email="own@t.local", role=UserRole.employee)
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=utc_now() + timedelta(days=1), download_limit=1, downloads_remaining=1,
    )
    db.add(share)
    db.flush()
    db.add(File(
        share_id=share.id, original_filename="appendix.bin",
        mime_type="application/octet-stream", size_bytes=10,
        state=FileState.clean, uploaded_by_id=owner.id,
        approval_state=(
            FileApprovalState.pending_review if awaiting else FileApprovalState.approved
        ),
    ))
    db.commit()
    return owner, share


def test_a_reviewing_approver_does_not_spend_the_recipients_budget(db, make_user):
    """The regression itself: an approver who is NOT a recipient reaches an
    active share only because it carries files awaiting their decision."""
    from app.models.user import UserRole
    from app.services import settings as settings_svc
    from app.services import share as share_svc

    settings_svc.set_value(db, key="share_approval.allow_content_review", value="true", actor=None)
    settings_svc.set_value(db, key="share_approval.enabled", value="true", actor=None)
    settings_svc.set_value(
        db, key="share_approval.approver_mode", value="employees_admins", actor=None
    )
    db.commit()

    _owner, share = _approval_share(db, make_user, awaiting=True)
    # An EMPLOYEE approver, not an admin. An admin passes
    # is_authorized_to_download outright, so they never exercised the branch
    # P10 changed - which is exactly why the defect survived: every existing
    # approval test uses an admin.
    approver = make_user(email="appr@t.local", role=UserRole.employee)

    assert share_svc.is_review_access(db, user=approver, share=share) is True


def test_an_approver_on_a_share_with_nothing_to_review_is_not_free(db, make_user):
    """The scoping matters both ways: with no file awaiting review there is no
    review to do, so nothing justifies a free download."""
    from app.models.user import UserRole
    from app.services import settings as settings_svc
    from app.services import share as share_svc

    settings_svc.set_value(db, key="share_approval.allow_content_review", value="true", actor=None)
    settings_svc.set_value(db, key="share_approval.enabled", value="true", actor=None)
    settings_svc.set_value(
        db, key="share_approval.approver_mode", value="employees_admins", actor=None
    )
    db.commit()

    _owner, share = _approval_share(db, make_user, awaiting=False)
    approver = make_user(email="appr2@t.local", role=UserRole.employee)

    assert share_svc.is_review_access(db, user=approver, share=share) is False


def test_the_budget_branches_consult_the_review_flag():
    """Both download routes, one definition. Pinned structurally because the
    alternative - driving two full download routes through an approval setup -
    is what made the original defect invisible."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "files.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    decrements = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "try_decrement_share_counter"
    ]
    assert len(decrements) == 2, f"expected both download routes, found {len(decrements)}"

    guarded = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        if "try_decrement_share_counter" not in test_src:
            continue
        # `not is_review` must be one of the conjuncts.
        if "'is_review'" in test_src and "Not()" in test_src:
            guarded += 1
    assert guarded == 2, (
        "a budget decrement is no longer gated on `not is_review`, so a "
        "reviewing approver spends the recipients' download budget"
    )


# --- v2.13.3: the queue offered a share the approver was 403'd from ---------
#
# The second P10 consequence. The queue and the notification gate on
# `can_approve`; `is_authorized_to_view` gated the active case on
# `can_review_this_share`, which also requires `allow_content_review`. Turn
# content review off and the approver is emailed a link to a page that refuses
# them - while `decide_added_files` accepted their vote all along.
#
# Every cell below uses a NON-ADMIN approver. An admin passes
# `is_authorized_to_download` outright and never reaches the branch, which is
# why the whole family kept surviving.


def _review_case(db, make_user, *, content_review: bool, suffix: str):
    from app.models.user import UserRole

    _approval_on(db, mode="employees_admins", content_review=content_review)
    owner = make_user(email=f"own-{suffix}@t.local", role=UserRole.employee)
    approver = make_user(email=f"appr-{suffix}@t.local", role=UserRole.employee)
    share, f = _share_with_pending_file(db, owner)
    return owner, approver, share, f


def test_content_review_off_still_lets_the_approver_open_the_share(db, make_user):
    """The regression: 403 before v2.13.3."""
    from app.services import share as share_svc

    _o, approver, share, _f = _review_case(db, make_user, content_review=False, suffix="v1")
    assert share_svc.is_authorized_to_view(db, user=approver, share=share) is True


def test_content_review_off_still_refuses_the_approver_the_bytes(db, make_user):
    """The counterweight. Without it the test above is satisfied by deleting the
    content-review gate outright, which is the opposite of the intent."""
    from app.middleware.errors import AppError
    from app.services import share as share_svc

    _o, approver, share, f = _review_case(db, make_user, content_review=False, suffix="v2")
    with pytest.raises(AppError) as exc:
        share_svc.assert_share_file_access(db, user=approver, share=share, file=f)
    assert exc.value.code == "FORBIDDEN"


def test_the_view_grant_and_the_decision_right_are_one_rule(db, make_user):
    """The drift pin. These diverged once; a mismatch in either direction is a
    bug - offered-but-refused, or openable-but-undecidable."""
    from app.services import share as share_svc
    from app.services import share_approval as approval_svc

    for i, cr in enumerate((True, False)):
        _o, approver, share, _f = _review_case(
            db, make_user, content_review=cr, suffix=f"v3{i}"
        )
        assert share_svc.is_authorized_to_view(
            db, user=approver, share=share
        ) is approval_svc.can_decide_added_files(db, approver, share)


def test_the_two_predicates_split_exactly_on_content_review(db, make_user):
    """One governs the page, the other the bytes. Pinning the split is what
    stops a future tidy-up folding them together."""
    from app.services import share_approval as approval_svc

    _o, approver, share, _f = _review_case(db, make_user, content_review=False, suffix="v4")
    assert approval_svc.can_decide_added_files(db, approver, share) is True
    assert approval_svc.can_review_this_share(db, approver, share) is False


def test_the_decide_predicate_refuses_the_shares_own_creator(db, make_user):
    """Creator is an ADMIN on purpose: an ordinary employee fails `can_approve`
    one check earlier and never exercises the self rule."""
    from app.middleware.errors import AppError
    from app.models.user import UserRole
    from app.services import share as share_svc
    from app.services import share_approval as approval_svc

    _approval_on(db, mode="admins_only")
    owner = make_user(email="own-v5@t.local", role=UserRole.admin)
    share, _f = _share_with_pending_file(db, owner)

    assert approval_svc.can_decide_added_files(db, owner, share) is False
    with pytest.raises(AppError) as exc:
        share_svc.decide_added_files(
            db, user=owner, share=share, approve=True, expect_fingerprint="x"
        )
    assert exc.value.code == "SELF_APPROVAL"


def test_the_decide_predicate_is_scoped_to_shares_awaiting_a_decision(db, make_user):
    """It now carries the scoping that keeps an approver out of every active
    outbound share in the instance."""
    from datetime import timedelta

    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.services import share as share_svc
    from app.services import share_approval as approval_svc
    from app.utils.timeutil import utc_now

    _approval_on(db, mode="employees_admins", content_review=False)
    owner = make_user(email="own-v6@t.local", role=UserRole.employee)
    approver = make_user(email="appr-v6@t.local", role=UserRole.employee)
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        subject="nothing pending", expires_at=utc_now() + timedelta(days=1),
    )
    db.add(share)
    db.commit()

    assert approval_svc.can_decide_added_files(db, approver, share) is False
    assert share_svc.is_authorized_to_view(db, user=approver, share=share) is False


def test_the_budget_predicate_is_unchanged_by_the_alignment(db, make_user):
    """`is_review_access` governs whether the RECIPIENTS' budget is charged and
    stays keyed to the BYTES predicate. With content review off the branch is
    unreachable anyway - the bytes 403 first - so an access that cannot happen
    must not also be free."""
    from app.services import share as share_svc

    _o, approver, share, _f = _review_case(db, make_user, content_review=False, suffix="v7")
    assert share_svc.is_review_access(db, user=approver, share=share) is False


def test_the_nav_finds_a_queue_stranded_by_the_off_switch(db, make_user):
    """`approval_was_required` is STORED and sticky. Turn the feature off after
    a share was approved under it, and a later upload into that still-active
    share is STILL marked `pending_review` - but `approver_user_ids` returns an
    empty set (nobody notified) and `has_pending_shares` counted only
    `pending_approval` (nav dark). The queue was quietly non-empty, the files
    gated from recipients forever, and no route in the UI reached the decision
    that would release them."""
    from app.models.user import UserRole
    from app.services import settings as settings_svc
    from app.services import share as share_svc
    from app.services import share_approval as approval_svc

    _approval_on(db, mode="employees_admins")
    owner = make_user(email="own-v8@t.local", role=UserRole.employee)
    admin = make_user(email="adm-v8@t.local", role=UserRole.admin)
    share, _f = _share_with_pending_file(db, owner)

    # The operator switches four-eyes off; the appended file stays gated.
    settings_svc.set_value(
        db, key=settings_svc.Keys.SHARE_APPROVAL_ENABLED, value="false", actor=None
    )
    db.commit()

    assert approval_svc.approver_user_ids(db) == set(), "premise: nobody is notified"
    rows, total = share_svc.list_pending_approvals(db, user=admin)
    assert total == 1, "premise: the queue is not empty"
    assert approval_svc.has_pending_shares(db) is True, (
        "the nav stayed dark over a non-empty queue, so the admin escape hatch "
        "had no door"
    )
