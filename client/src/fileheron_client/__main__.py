"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script.

v0.4.0 architecture: one ``ctk.CTk`` root is created up-front and
shared across the login phase + main window. We hide the root during
login (the login dialog is a separate ``CTkToplevel``) and reveal it
after a successful sign-in."""
from __future__ import annotations

import logging
import sys

# Absolute imports — when PyInstaller runs this file as the entry
# script it loses package context, so `from .config import ...` would
# raise `ImportError: attempted relative import with no known parent
# package`. Absolute imports work both for that and for the canonical
# `python -m fileheron_client` invocation.
from fileheron_client.config import load_config
from fileheron_client.ui.app import build_root
from fileheron_client.ui.login_window import LoginWindow
from fileheron_client.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    root = build_root()
    # Hide the root during the login phase. The login dialog is a
    # Toplevel on top of (and modal to) the hidden root.
    root.withdraw()

    cfg = load_config()
    main_window: MainWindow | None = None

    def _on_signin(api, me) -> None:
        nonlocal main_window
        main_window = MainWindow(root, api, me)
        main_window.show()

    login = LoginWindow(root, cfg, on_signed_in=_on_signin)
    login.show_modal()

    # If login was cancelled (no MainWindow attached), exit cleanly.
    if main_window is None:
        root.destroy()
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
