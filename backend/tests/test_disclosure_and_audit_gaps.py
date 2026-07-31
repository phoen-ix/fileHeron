"""Four hand-applied specs: what leaks, what is not recorded, and how long it takes.

comms-2   `/api/public/<token>/...` and the 180-day manage-notifications token
          carry a live bearer secret in the PATH. The error middleware already
          excluded the query string for that reason, then stored the path
          verbatim - into `error_log.path`, browsable at /admin/error-log,
          streamed by the CSV export, and rendered into the server_error alert
          mail, which under recipients_mode=custom goes to arbitrary addresses.
          The token outlived the link it opened.
admin-4   deleting an inbound message unlinks every attachment and drops the
          row, recording nothing. The IMAP post-fetch action can delete from the
          server after ingest, so that row is frequently the only copy of a
          client's correspondence - and it was the one irreversible admin action
          in the codebase that left no trace.
authn-6   /forgot-password promises in its own docstring never to reveal whether
          an address exists. The body was identical either way; the LATENCY was
          not, because the known-address branch awaited a full SMTP connect
          inline. An account-existence oracle on the endpoint whose entire
          purpose is not to be one.
crypto-9  the backup KDF used scrypt n=2^14 - scrypt's original "interactive
          login" setting - on a file that with include_env carries JWT_SECRET,
          DB_PASSWORD, every password hash and every decrypted TOTP secret, is
          meant to live off-site, and ships its salt and cost params in
          cleartext for offline grinding.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.middleware.errors import _redact_path
from app.models.audit_log import AuditEventType
from app.utils import crypto

# --- comms-2 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/public/AbC123secret/files/9/download", "/api/public/:token/files/9/download"),
        ("/api/public/AbC123secret", "/api/public/:token"),
        (
            "/api/notification-subscriptions/LIVE180DAY/manage",
            "/api/notification-subscriptions/:token/manage",
        ),
    ],
)
def test_secret_path_segments_are_collapsed(path, expected):
    assert _redact_path(path) == expected


@pytest.mark.parametrize(
    "path",
    ["/api/shares/abc-123", "/api/health", "/.env", "/api/public", "/"],
)
def test_ordinary_paths_are_untouched(path):
    """Control: over-redacting would destroy the route shape that error triage
    and the `signature` grouping depend on."""
    assert _redact_path(path) == path


def test_the_middleware_actually_uses_it():
    from app.middleware import errors

    src = inspect.getsource(errors._maybe_enqueue_error_event)
    assert "_redact_path(request.url.path)" in src, (
        "a live public-link token still reaches the browsable error log"
    )


# --- admin-4 ----------------------------------------------------------------


def test_an_audit_event_exists_for_the_deletion():
    assert hasattr(AuditEventType, "inbound_message_deleted")


def test_the_handler_records_who_destroyed_what():
    from app.routers.admin import imap as imap_router

    src = inspect.getsource(imap_router.delete_inbox_message)
    assert "record_audit_event" in src
    assert "inbound_message_deleted" in src
    # The attachment filenames are the part that cannot be reconstructed
    # afterwards - the rows and bytes are both gone.
    assert "attachments" in src


# --- authn-6 ----------------------------------------------------------------


def test_the_reset_email_is_not_awaited_inline():
    """The oracle is the await, not the send. Sending in the request means the
    exists branch pays an SMTP round-trip the not-exists branch does not."""
    from app.routers import auth as auth_router

    src = inspect.getsource(auth_router.forgot_password)
    assert "background.add_task" in src
    assert "await email_svc.send_password_reset_email" not in src


def test_both_branches_still_return_the_same_body():
    """Control: the response must stay identical, or the fix trades a timing
    oracle for a content one."""
    from app.routers import auth as auth_router

    src = inspect.getsource(auth_router.forgot_password)
    assert src.count('return {"ok": True}') == 1


# --- crypto-9 ---------------------------------------------------------------


def test_the_backup_kdf_meets_the_current_floor():
    assert crypto.SCRYPT_N >= 2**17, (
        "2^14 is scrypt's interactive-login setting, not a setting for a file "
        "that carries JWT_SECRET and every password hash off-site"
    )


def test_the_new_default_survives_its_own_import_bound():
    """The export params travel in the envelope and come back through
    validate_scrypt_params on import. A default above its own ceiling would
    produce backups this instance refuses to read."""
    crypto.validate_scrypt_params(
        n=crypto.SCRYPT_N, r=crypto.SCRYPT_R, p=crypto.SCRYPT_P
    )


def test_old_backups_still_open():
    """The whole point of carrying the params in the envelope: raising the
    default must not orphan every backup written before it. Encryption always
    uses the CURRENT constants, so an old file is simulated by deriving at the
    old cost directly - which is exactly what the import path does with the
    values it reads out of the envelope."""
    from cryptography.fernet import Fernet

    salt = b"0" * 16
    old_key = crypto.derive_backup_key("correct horse battery", salt, n=2**14, r=8, p=1)
    token = Fernet(old_key).encrypt(b"payload").decode("ascii")

    assert (
        crypto.decrypt_with_passphrase(
            token, "correct horse battery", salt, n=2**14, r=8, p=1
        )
        == b"payload"
    )


def test_a_new_backup_round_trips_at_the_new_cost():
    """Control: the raised default has to actually work."""
    salt = b"1" * 16
    token = crypto.encrypt_with_passphrase(b"payload", "correct horse battery", salt)
    assert (
        crypto.decrypt_with_passphrase(
            token, "correct horse battery", salt,
            n=crypto.SCRYPT_N, r=crypto.SCRYPT_R, p=crypto.SCRYPT_P,
        )
        == b"payload"
    )
