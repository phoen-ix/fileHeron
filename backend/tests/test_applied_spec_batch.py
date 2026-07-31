"""Regression cover for the batch applied from the drafted fix specs.

Twenty-six findings landed together, so this covers the ones where a wrong fix
would be worst - disclosure, cascade behaviour, and the guards that decide what
an anonymous caller sees. The rest ride the existing 1313-test suite.

publiclink-9  /api/health is anonymous and the whole of /api/ is proxied from
              the internet. It returned `running_sha` - which maps one-to-one
              onto a public source tree, telling any passer-by exactly which
              fixes an instance is missing - plus pool stats and a `degraded`
              list that announces when Redis is down and the per-IP limiter has
              fallen back to the weaker in-process one. That is precisely when
              to start credential stuffing.
schema-6      the model declared `recipient_group_id` as a bare BigInteger while
              the Phase 4 migration created a real FK with ON DELETE CASCADE, so
              the create_all test schema behaved differently from production and
              no test could observe the cascade.
injection-4   header sanitisation missed the Unicode line separators.
injection-6   the CSP relaxation was justified by a library removed releases ago.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.models.share_recipient import ShareRecipient

# --- publiclink-9: what a stranger learns from /api/health ------------------


def _req(host: str | None):
    class _C:
        pass

    c = _C()
    c.host = host

    class _R:
        client = c

    return _R()


@pytest.mark.parametrize("host", ["127.0.0.1", "172.19.0.4", "10.1.2.3", "192.168.1.9"])
def test_operators_still_see_the_diagnostics(host):
    """Every consumer that needs the detail - the compose HEALTHCHECK, the
    updater's running_version poll, an operator on the box - arrives over
    loopback or the docker bridge."""
    from app.routers.health import _peer_is_operator

    assert _peer_is_operator(_req(host)) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", None, "not-an-ip"])
def test_the_public_path_does_not(host):
    from app.routers.health import _peer_is_operator

    assert _peer_is_operator(_req(host)) is False


def test_liveness_is_still_public():
    """Control: this endpoint is also a load balancer's readiness probe. Hiding
    the STATUS would break uptime monitoring, which is not the goal - only the
    build identifiers and the degraded list are operator information."""
    src = inspect.getsource(__import__("app.routers.health", fromlist=["health"]))
    assert '"status"' in src


# --- schema-6: the FK the model never declared ------------------------------


def test_the_group_recipient_column_is_a_real_foreign_key():
    fks = list(ShareRecipient.__table__.c.recipient_group_id.foreign_keys)
    assert fks, (
        "the model still declares a bare BigInteger, so the test schema cannot "
        "reproduce production's cascade"
    )
    assert fks[0].ondelete == "CASCADE"


def test_deleting_a_group_cascades_its_recipient_rows(fk_db):
    """Only observable with foreign keys ON - which the default fixture leaves
    off. That is exactly why this went unnoticed."""
    from app.models.group import Group
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import User, UserRole
    from app.utils.crypto import argon2_hash, normalize_email

    # `make_user` is bound to the `db` fixture and would write to the wrong
    # session, so build the row against the FK-enforcing one.
    owner = User(
        email=normalize_email("own@test.local"), password_hash=argon2_hash("x"),
        display_name="Owner", role=UserRole.employee,
    )
    fk_db.add(owner)
    fk_db.commit()
    g = Group(name="Team", name_normalized="team", created_by_id=owner.id)
    fk_db.add(g)
    fk_db.commit()
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    fk_db.add(sh)
    fk_db.commit()
    fk_db.add(ShareRecipient(share_id=sh.id, recipient_group_id=g.id))
    fk_db.commit()

    fk_db.delete(g)
    fk_db.commit()
    assert fk_db.query(ShareRecipient).count() == 0


# --- injection-4: header sanitisation ---------------------------------------


@pytest.mark.parametrize(
    "raw", ["Bob\nEvil", "Bob\rEvil", "Bob\x85Evil", "Bob Evil", "Bob Evil"]
)
def test_header_values_lose_every_line_break_form(raw):
    """A newline in a display name reaches the Subject of mail sent to OTHER
    users. The original guard covered C0 and DEL but not U+0085 / U+2028 /
    U+2029, which some MTAs and clients also treat as line breaks."""
    from app.utils.emailing import _header_safe

    out = _header_safe(raw)
    for ch in "\n\r\x85  ":
        assert ch not in out


def test_ordinary_names_are_untouched():
    """Control: over-stripping would mangle every non-ASCII display name."""
    from app.utils.emailing import _header_safe

    assert _header_safe("Zoë Müller-O'Brien") == "Zoë Müller-O'Brien"


# --- injection-6: the stale CSP justification -------------------------------


def test_the_csp_relaxation_is_not_justified_by_a_removed_library():
    """The fix keeps the name but demotes it from reason to history, which is
    more useful than deleting it: the next reader needs to know the old
    rationale is void AND why the directive still cannot simply be tightened."""
    from app.middleware import security_headers

    doc = security_headers.__doc__ or ""
    assert "to support Element Plus" not in doc
    assert "was removed long ago" in doc
