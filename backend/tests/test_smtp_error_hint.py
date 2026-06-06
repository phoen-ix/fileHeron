"""_smtp_error_hint(exc) - maps caught SMTP exceptions to an actionable
admin hint. Pure function; first-match-wins ordering matters."""
from __future__ import annotations

from aiosmtplib.errors import SMTPResponseException

from app.services.email import _smtp_error_hint


def test_auth_failure_535_gives_credentials_hint():
    hint = _smtp_error_hint(SMTPResponseException(535, "5.7.8 Authentication failed"))
    assert hint is not None
    assert "authentication" in hint.lower()


def test_client_rejected_554_gives_relay_hint():
    # The exact shape of the incident that prompted this feature.
    hint = _smtp_error_hint(
        SMTPResponseException(554, "5.7.1 Client host rejected: Access denied")
    )
    assert hint is not None
    assert "refused this client" in hint.lower()


def test_auth_checked_before_relay_when_text_contains_access_denied():
    # A 535 whose body also says "access denied" must still be classified as
    # an auth problem, not relay - rule order guards this.
    hint = _smtp_error_hint(
        SMTPResponseException(535, "5.7.8 access denied: authentication required")
    )
    assert hint is not None
    assert "authentication" in hint.lower()
    assert "refused this client" not in hint.lower()


def test_starttls_text_gives_tls_hint():
    hint = _smtp_error_hint(Exception("STARTTLS extension not supported by server"))
    assert hint is not None
    assert "tls" in hint.lower()


def test_oserror_gives_connection_hint():
    hint = _smtp_error_hint(OSError("Connection refused"))
    assert hint is not None
    assert "reach the smtp server" in hint.lower()


def test_unmapped_error_returns_none():
    assert _smtp_error_hint(Exception("totally unrelated boom")) is None


def test_coded_5xx_relay_not_misclassified_as_tls():
    # A coded 5xx mentioning neither tls keyword should land in the relay
    # bucket, not the TLS one (TLS rule requires code is None for ssl/tls text).
    hint = _smtp_error_hint(SMTPResponseException(554, "5.7.1 relay access denied"))
    assert hint is not None
    assert "refused this client" in hint.lower()
