"""Stored secrets: what happens when one cannot be read, and what gets logged.

Three findings, one root cause between two of them.

crypto-6 + comms-3 are THE SAME defect at two call sites: a bare
`_get_fernet().decrypt(...)` with no guard. The trigger in production is
rotating JWT_SECRET without running scripts/rotate_jwt_secret.py, after which
every Fernet field in the database is unreadable. `services/settings.py`,
`oidc_admin.py`, `public_links.py` and `config_backup.py` all already caught
this and degraded deliberately; `totp.py` and `webhook_deliver.py` did not. So
a rotation 500'd every 2FA login and left every webhook delivery stuck
`pending` with no error recorded - a webhook that never fires and never fails.

comms-1: `job_queue` logged `%r` of the enqueue args on a Redis outage.
`send_email_job` is enqueued with the rendered body and a `list_unsubscribe`
header carrying a 180-day manage-subscriptions token, so an outage wrote the
whole email plus a live credential to container stdout.

All three found in the 2026-07-30 audit.
"""
from __future__ import annotations

import logging

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import job_queue
from app.services import totp as totp_svc
from app.utils.crypto import (
    SecretUndecryptableError,
    decrypt_setting,
    decrypt_totp_secret,
    encrypt_setting,
)

# --- the shared root cause -------------------------------------------------


def test_undecryptable_setting_raises_the_typed_error():
    with pytest.raises(SecretUndecryptableError):
        decrypt_setting("gAAAAABmnot-a-real-token")


def test_empty_setting_raises_rather_than_returning_garbage():
    """`Webhook.secret_encrypted` is `nullable=False, default=""`, so "" is a
    real stored state, not an impossible one."""
    with pytest.raises(SecretUndecryptableError):
        decrypt_setting("")


def test_undecryptable_totp_secret_raises_the_typed_error():
    with pytest.raises(SecretUndecryptableError):
        decrypt_totp_secret(b"not a fernet token at all")


def test_a_good_secret_still_round_trips():
    """Control: the guard must not break the normal path."""
    assert decrypt_setting(encrypt_setting("hunter2")) == "hunter2"


def test_the_typed_error_is_still_an_exception_subclass():
    """Four existing call sites catch bare `Exception` and degrade correctly.
    Narrowing the hierarchy would silently un-guard all of them."""
    assert issubclass(SecretUndecryptableError, Exception)


# --- crypto-6: the login path ----------------------------------------------


@pytest.fixture
def user_with_broken_totp(db, make_user):
    from app.models.user_totp import UserTOTP
    from app.utils.timeutil import utc_now

    u = make_user(email="tot@test.local", role=UserRole.employee)
    db.add(
        UserTOTP(
            user_id=u.id,
            secret_encrypted=b"corrupt-ciphertext-not-decryptable",
            enabled_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(u)
    return u


def test_login_with_an_undecryptable_secret_fails_cleanly(db, user_with_broken_totp):
    """Previously a raw InvalidToken escaped into the request and became a 500
    that named nothing. The operator's actual problem - a rotated JWT_SECRET -
    was nowhere in the response or the log line."""
    with pytest.raises(AppError) as exc:
        totp_svc.verify_at_login(db, user=user_with_broken_totp, code="123456")
    assert exc.value.status_code == 503
    assert exc.value.code == "TOTP_SECRET_UNAVAILABLE"
    assert "administrator" in exc.value.message.lower()


def test_enable_with_an_undecryptable_secret_fails_cleanly(db, user_with_broken_totp):
    user_with_broken_totp.totp.enabled_at = None
    db.commit()
    with pytest.raises(AppError) as exc:
        totp_svc.confirm_enable(
            db, user=user_with_broken_totp, code="123456", request=None
        )
    assert exc.value.code == "TOTP_SECRET_UNAVAILABLE"


def test_a_healthy_totp_still_verifies(db, make_user):
    """Control: the guard must not break 2FA for everyone else."""
    import pyotp

    from app.models.user_totp import UserTOTP
    from app.utils.crypto import encrypt_totp_secret
    from app.utils.timeutil import utc_now

    secret = pyotp.random_base32()
    u = make_user(email="ok@test.local", role=UserRole.employee)
    db.add(
        UserTOTP(
            user_id=u.id,
            secret_encrypted=encrypt_totp_secret(secret),
            enabled_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(u)
    assert totp_svc.verify_at_login(db, user=u, code=pyotp.TOTP(secret).now()) is True


# --- comms-1: what reaches the log ------------------------------------------


def test_enqueue_failure_never_logs_the_payload(caplog):
    secret_body = "Dear Dana, here is your reset link"
    unsubscribe = "<https://x.test/u/LIVE-MANAGE-TOKEN-abc123>"

    with caplog.at_level(logging.ERROR):
        line = job_queue._redact(
            ("send_email_job",),
            {
                "to": "dana@example.com",
                "subject": "Password reset",
                "text_body": secret_body,
                "html_body": f"<p>{secret_body}</p>",
                "list_unsubscribe": unsubscribe,
                "email_log_id": 42,
            },
        )

    assert secret_body not in line
    assert "LIVE-MANAGE-TOKEN" not in line
    assert "dana@example.com" not in line, "the recipient address is PII too"
    # Still useful for diagnosis: which job, and which keys it carried.
    assert "email_log_id=42" in line
    assert "text_body" in line and "withheld" in line


def test_redaction_keeps_enough_to_identify_the_job():
    """Control: over-redacting turns an outage into an unsolvable mystery."""
    line = job_queue._redact((1, 2), {"file_id": "abc", "list_unsubscribe": "x"})
    assert "2 positional" in line
    assert "file_id='abc'" in line


def test_no_call_site_still_logs_raw_args():
    """No enqueue logger may render `args`/`kwargs` except through `_redact`.

    This used to assert `src.count("_redact(args, kwargs)") == 2`, which is a
    proxy for the invariant and not the invariant: it fails when a THIRD logger
    is added correctly (the batch path, v2.6.0) and would keep passing if
    someone added a fourth that logged raw values. Stated properly, the rule is
    about what reaches a log line, so that is what is checked."""
    import ast
    import inspect

    src = inspect.getsource(job_queue)
    assert "%r,%r" not in src, "an enqueue logger still formats raw args"

    secret_bearing = {"args", "kwargs"}
    log_methods = {"debug", "info", "warning", "error", "exception", "critical"}

    def leaked(node) -> set[str]:
        """Names this expression would put into a string. `_redact(...)` is the
        sanctioned exit, so its subtree is pruned rather than walked."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_redact"
        ):
            return set()
        found = set()
        if isinstance(node, ast.Name) and node.id in secret_bearing:
            found.add(node.id)
        for child in ast.iter_child_nodes(node):
            found |= leaked(child)
        return found

    tree = ast.parse(src)
    # `_redact` is the redactor: touching args is its whole job, and the two
    # tests above assert on what it actually produces. Scanning its body here
    # would only flag `len(args)` - the arity, which is the safe part.
    body = [
        n
        for n in tree.body
        if not (isinstance(n, ast.FunctionDef) and n.name == "_redact")
    ]
    for node in [x for n in body for x in ast.walk(n)]:
        # Anything interpolated into a string is a log line waiting to happen.
        if isinstance(node, ast.JoinedStr):
            assert not leaked(node), "an f-string interpolates raw enqueue args"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in log_methods
        ):
            for arg in node.args:
                assert not leaked(arg), (
                    f"logger.{node.func.attr} receives raw enqueue args"
                )

    # And the sanctioned exit must actually be in use - a module that stopped
    # logging failures altogether would pass everything above.
    assert "_redact(" in src
