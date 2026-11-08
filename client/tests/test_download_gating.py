"""Download and Save-all are offered for ``clean`` files only, and a session
expiry is never swallowed on the way to the UI.

Every byte route answers ``425 SCAN_IN_PROGRESS`` for a ``ready_unscanned``
file (``services/file.assert_state_servable`` on the server), so a Download
button offered for one always failed, and Save-all over a fresh share toasted
one "download failed" per unscanned file. The SPA's FileRow gates on ``clean``;
this half did not until 2026-11-08. ``api/shares.get_public_link`` likewise
swallowed every exception including ``SessionExpiredError`` - the one the UI
layer is built to intercept - so an expired session showed a share page that
simply never had a link."""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"
VIEW = SRC / "ui" / "share_detail_view.py"
SHARES_API = SRC / "api" / "shares.py"


def _string_literals(tree: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_the_share_detail_view_never_offers_an_unscanned_file() -> None:
    tree = ast.parse(VIEW.read_text(encoding="utf-8"))
    assert "ready_unscanned" not in _string_literals(tree), (
        "share_detail_view.py names `ready_unscanned` again - the server refuses "
        "every byte route for that state (425), so it must not be downloadable here"
    )
    assert "clean" in _string_literals(tree), "the `clean` gate is gone entirely"


def test_the_downloadable_flag_is_the_clean_state_alone() -> None:
    """`"downloadable": f.state == "clean"` - a Compare against the one literal,
    not a membership test over a tuple that could grow a second state back."""
    tree = ast.parse(VIEW.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "downloadable":
                found.append(value)
    assert found, "the per-row `downloadable` flag is no longer built here"
    for value in found:
        assert isinstance(value, ast.Compare) and len(value.ops) == 1, ast.dump(value)
        assert isinstance(value.ops[0], ast.Eq), "downloadable must be an equality, not a membership"
        (right,) = value.comparators
        assert isinstance(right, ast.Constant) and right.value == "clean", ast.dump(value)


def test_get_public_link_lets_a_session_expiry_through() -> None:
    tree = ast.parse(SHARES_API.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "get_public_link"
    )
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries, "get_public_link no longer has a try/except"
    handlers = tries[0].handlers
    names = [getattr(h.type, "id", None) if h.type is not None else None for h in handlers]
    assert "SessionExpiredError" in names, (
        f"get_public_link catches {names}: a bare `except Exception` swallows the "
        "session expiry the UI must see"
    )
    expiry = handlers[names.index("SessionExpiredError")]
    assert any(isinstance(s, ast.Raise) for s in expiry.body), "the SessionExpiredError handler must re-raise"
    assert names.index("SessionExpiredError") < names.index("Exception"), (
        "the specific handler must come before the broad one, or it is unreachable"
    )
