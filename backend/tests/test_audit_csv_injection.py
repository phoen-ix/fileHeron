"""Regression: audit CSV export neutralises formula injection (finding L3)."""
from __future__ import annotations

from app.routers.admin.audit import _csv_safe


def test_csv_safe_neutralises_formula_prefixes():
    for danger in ("=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)", "\t=evil", "\r=evil"):
        out = _csv_safe(danger)
        assert out.startswith("'"), out
        # original payload preserved after the guard quote
        assert out[1:] == danger


def test_csv_safe_leaves_normal_values_untouched():
    assert _csv_safe("share_created") == "share_created"
    assert _csv_safe("user@example.com") == "user@example.com"  # @ not leading
    assert _csv_safe("") == ""
    assert _csv_safe(None) == ""
    assert _csv_safe(42) == "42"
