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


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found in ShareDetailView")


def _calls_self_method(fn: ast.FunctionDef, method: str) -> bool:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            return True
    return False


def test_init_calls_load_and_binds_destroy() -> None:
    """Regression guard (client-v0.9.4 bug): __init__ MUST call self._load(),
    otherwise the share detail never loads and renders only placeholders. It
    also wires the <Destroy> unbind here."""
    cls = _share_detail_view_class()
    init = _method(cls, "__init__")
    assert _calls_self_method(init, "_load"), (
        "ShareDetailView.__init__ must call self._load() — otherwise the detail "
        "view never loads the share (regression shipped in client-v0.9.4)."
    )
    init_src = ast.get_source_segment(VIEW.read_text(encoding="utf-8"), init) or ""
    assert "<Destroy>" in init_src, (
        "ShareDetailView.__init__ must bind <Destroy> (esc-unbind setup)."
    )


def test_toast_does_not_call_load() -> None:
    """The _load() call must live in __init__, never in _toast — the latter is
    exactly the client-v0.9.4 boundary bug that broke share-detail loading."""
    cls = _share_detail_view_class()
    toast = _method(cls, "_toast")
    assert not _calls_self_method(toast, "_load"), (
        "ShareDetailView._toast must not call self._load(); that orphaned the "
        "load out of __init__ and left the detail on placeholders."
    )


def test_done_swaps_to_open_actions_not_redownload() -> None:
    """client-v0.9.15: a successful download must replace the row button with
    Open/Folder actions, not flip back to Download. Guard that the open-actions
    helpers exist and the row tracks its action cell."""
    cls = _share_detail_view_class()
    names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert {"_show_open_actions", "_open_path", "_reveal_path"} <= names, (
        "ShareDetailView must declare _show_open_actions / _open_path / "
        "_reveal_path for the post-download Open + Folder buttons."
    )
    src = VIEW.read_text(encoding="utf-8")
    assert '"action_cell"' in src, (
        "_render_files must store the row's action_cell so _show_open_actions "
        "can swap its buttons after a successful save."
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
