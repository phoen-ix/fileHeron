"""Structural guards for the v0.6.1 "End share" collapse.

v0.6.1 collapsed the separate Revoke + Expire-now buttons into a
single "End share" button that calls expire-now (state → expired,
files hard-deleted). The user feedback was that the two-button
distinction was unintuitive. These AST-level tests pin the
simplification — if a future change re-introduces a second
destructive button on the share detail, these fail loudly."""
from __future__ import annotations

import ast
from pathlib import Path

VIEW = (
    Path(__file__).resolve().parent.parent
    / "src" / "fileheron_client" / "ui" / "share_detail_view.py"
)


def _tree() -> ast.Module:
    return ast.parse(VIEW.read_text(encoding="utf-8"))


def _share_detail_view_class() -> ast.ClassDef:
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == "ShareDetailView":
            return node
    raise AssertionError("class ShareDetailView not found in share_detail_view.py")


def test_no_revoke_method() -> None:
    """The old ``_revoke`` method must be gone — its semantics fold
    into ``_end_share`` which calls expire-now."""
    cls = _share_detail_view_class()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            assert node.name != "_revoke", (
                "ShareDetailView._revoke should be removed in v0.6.1 — "
                "use the single _end_share action instead."
            )


def test_end_share_method_present() -> None:
    """The single destructive manager action."""
    cls = _share_detail_view_class()
    names = {
        n.name for n in cls.body
        if isinstance(n, ast.FunctionDef)
    }
    assert "_end_share" in names, (
        "ShareDetailView must declare _end_share (the v0.6.1 single "
        "destructive manager action)."
    )


def test_no_revoke_btn_or_expire_now_btn_assignments() -> None:
    """The old widget attributes ``self.revoke_btn`` and
    ``self.expire_now_btn`` should both be gone — replaced by
    ``self.end_share_btn``."""
    src = VIEW.read_text(encoding="utf-8")
    assert "self.revoke_btn" not in src, (
        "self.revoke_btn must not survive v0.6.1 — only end_share_btn."
    )
    assert "self.expire_now_btn" not in src, (
        "self.expire_now_btn renamed to self.end_share_btn in v0.6.1."
    )
    assert "self.end_share_btn" in src, (
        "ShareDetailView must construct self.end_share_btn."
    )
