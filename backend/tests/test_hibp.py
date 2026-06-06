"""HIBP k-anonymity check - happy/sad paths + fail-open."""
from __future__ import annotations

import hashlib

import pytest

from app.services import hibp as hibp_svc


@pytest.mark.asyncio
async def test_breached_password_detected(monkeypatch):
    """When the API returns a body with our suffix + a positive count,
    is_password_breached returns True."""
    pw = "password123"
    sha1 = hashlib.sha1(pw.encode(), usedforsecurity=False).hexdigest().upper()
    suffix = sha1[5:]

    async def fake_fetch(prefix5):
        # API line format: <suffix>:<count>
        return f"{suffix}:1500\nABCDEABCDEABCDEABCDEABCDEABCDEABCDEAB:0\n"

    monkeypatch.setattr(hibp_svc, "_fetch_range", fake_fetch)
    assert await hibp_svc.is_password_breached(pw) is True


@pytest.mark.asyncio
async def test_unknown_password_clean(monkeypatch):
    pw = "very-unique-passphrase-no-one-uses-this-12345"

    async def fake_fetch(_prefix5):
        return "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:0\n"

    monkeypatch.setattr(hibp_svc, "_fetch_range", fake_fetch)
    assert await hibp_svc.is_password_breached(pw) is False


@pytest.mark.asyncio
async def test_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(hibp_svc.settings, "HIBP_ENABLED", False)
    assert await hibp_svc.is_password_breached("anything") is False


@pytest.mark.asyncio
async def test_upstream_failure_fails_open(monkeypatch):
    async def fake_fetch(_prefix5):
        return None  # simulate network error

    monkeypatch.setattr(hibp_svc, "_fetch_range", fake_fetch)
    # Even though the fetch returned None (network down), we don't
    # block the password change - fail-open by design.
    assert await hibp_svc.is_password_breached("password") is False


@pytest.mark.asyncio
async def test_zero_count_not_breached(monkeypatch):
    """Padded responses include zero-count entries; we must not
    flag those as breaches even though the suffix matches."""
    pw = "test-pw"
    sha1 = hashlib.sha1(pw.encode(), usedforsecurity=False).hexdigest().upper()
    suffix = sha1[5:]

    async def fake_fetch(_prefix5):
        return f"{suffix}:0\n"

    monkeypatch.setattr(hibp_svc, "_fetch_range", fake_fetch)
    assert await hibp_svc.is_password_breached(pw) is False
