"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script."""
from __future__ import annotations

import logging
import sys

# Absolute imports — when PyInstaller runs this file as the entry
# script it loses package context, so `from .config import ...` would
# raise `ImportError: attempted relative import with no known parent
# package`. Absolute imports work both for that and for the canonical
# `python -m fileheron_client` invocation.
from fileheron_client.config import load_config
from fileheron_client.ui.app import build_app
from fileheron_client.ui.login_window import LoginWindow
from fileheron_client.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = build_app(argv or sys.argv)
    cfg = load_config()

    main_window: MainWindow | None = None

    def _on_signin(api, me):
        nonlocal main_window
        main_window = MainWindow(api, me)
        main_window.show()

    login = LoginWindow(cfg)
    login.accepted_with_client.connect(_on_signin)
    if not login.exec():
        return 0
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
