"""Two Tier 3 findings where the guard could simply be stepped around.

authn-2: the new-device alert bailed out when `ua_fingerprint_hash` returned
falsy, and it returns "" for an absent User-Agent. So an attacker who omitted
the header recorded no known_devices row, fired no alert to the account owner,
and stamped `new_device: False` into the audit row - on every login path, since
they all funnel through finalize_successful_login.

authz-5: GET /api/shares/{id}/public-link was gated on `shares:read`, but it
returns the DECRYPTED plaintext URL and a QR of it - an anonymous, password-free
route to the file bytes. A token issued for metadata reading could therefore
exfiltrate every file its owner had shared, without files:download.
"""
from __future__ import annotations

from app.services.api_token import SCOPES
from app.utils.ua_fingerprint import ua_fingerprint_hash


def test_absent_user_agent_still_produces_a_fingerprint():
    """The absence of a UA is itself a fingerprint. The service substitutes a
    sentinel; what matters is that the hash is truthy so the caller's
    `if not ua_hash: return False` bail-out cannot be triggered by omission."""
    assert ua_fingerprint_hash("-")
    # And the empty case that used to reach the bail-out:
    assert ua_fingerprint_hash("") == ""


def test_device_recording_does_not_bail_on_a_missing_user_agent():
    import inspect

    from app.services import auth as auth_svc

    src = inspect.getsource(auth_svc)
    assert 'request.headers.get("user-agent", "") or "-"' in src, (
        "an absent User-Agent must be fingerprinted, not treated as unknown - "
        "otherwise omitting the header suppresses the new-device alert"
    )


def test_public_links_read_scope_exists():
    assert "public_links:read" in SCOPES


def test_reading_a_public_link_url_needs_its_own_scope():
    """shares:read must not be sufficient - that is the escalation."""
    import inspect

    from app.routers import public_links

    src = inspect.getsource(public_links.get_public_link)
    assert 'require_scope("public_links:read")' in src
    assert 'require_scope("shares:read")' not in src


def test_frontend_scope_list_is_in_lockstep():
    """CLAUDE.md requires utils/tokenScopes.ts to mirror the backend SCOPES; a
    scope missing there is un-grantable from the UI."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    ts = (repo / "frontend" / "src" / "utils" / "tokenScopes.ts").read_text()
    missing = sorted(s for s in SCOPES if f"'{s}'" not in ts)
    assert not missing, f"scopes absent from the frontend list: {missing}"
