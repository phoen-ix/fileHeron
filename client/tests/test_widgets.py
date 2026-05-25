"""Regression guards for ``ui.widgets``.

Importing ``customtkinter`` requires the system ``tkinter`` extension,
which CI does not necessarily ship. We therefore inspect signatures
via the AST instead of importing the module — enough to catch the
kind of bug v0.5.5 fixed (``PillLabel.__init__`` rejected widget-level
kwargs like ``cursor=`` because it had no ``**kwargs``)."""
from __future__ import annotations

import ast
from pathlib import Path

WIDGETS_PY = (
    Path(__file__).resolve().parent.parent
    / "src" / "fileheron_client" / "ui" / "widgets.py"
)


def _find_class_init(source: str, class_name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == "__init__"
                ):
                    return child
    raise AssertionError(f"{class_name}.__init__ not found in widgets.py")


def test_pill_label_init_accepts_kwargs() -> None:
    """``PillLabel(..., cursor='hand2')`` must not raise TypeError.

    v0.5.4 added ``cursor='hand2'`` to the call site in
    ``share_list_panel.py::_render`` on the assumption that PillLabel
    transparently forwards CTk widget kwargs. It did not — the resulting
    TypeError crashed the row render mid-loop and the swallowed
    exception (logged by ``_async.py::_poll``) made the list look empty
    and made post-revoke refreshes silently fail. v0.5.5 added
    ``**kwargs`` to PillLabel; this test pins that contract."""
    init = _find_class_init(WIDGETS_PY.read_text(encoding="utf-8"), "PillLabel")
    assert init.args.kwarg is not None, (
        "PillLabel.__init__ must declare **kwargs so widget options "
        "(cursor, take_focus, …) pass through to CTkLabel."
    )
