"""Login dialog: server URL + email/password (with optional TOTP) OR
API token. Returns the wired-up ApiClient on success."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..config import (
    ClientConfig,
    get_secret,
    save_config,
    set_secret,
)


class LoginWindow(QDialog):
    """One dialog with two stacked auth modes (password / api token).
    Emits ``accepted_with_client`` with the configured ApiClient on
    success; the caller wires up the main window."""

    accepted_with_client = Signal(object, object)  # ApiClient, MeResponse

    def __init__(self, cfg: ClientConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to file:Heron")
        self.setMinimumWidth(440)
        self._cfg = cfg
        self._build()
        self._wire()
        self._toggle_mode(self._cfg.auth_kind)

    # ---- layout ---------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)

        intro = QLabel(
            "Connect to a file:Heron server. Your credentials live in "
            "your OS credential store; only the server URL is saved on disk."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        outer.addLayout(form)

        self.server_url = QLineEdit(self._cfg.server_url)
        self.server_url.setPlaceholderText("https://files.example.com")
        form.addRow("Server URL", self.server_url)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # password page
        page_pw = QWidget()
        pw_form = QFormLayout(page_pw)
        self.email = QLineEdit(self._cfg.last_email or "")
        pw_form.addRow("Email", self.email)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        pw_form.addRow("Password", self.password)
        self.totp = QLineEdit()
        self.totp.setPlaceholderText("6-digit code (only if asked)")
        self.totp.setMaxLength(8)
        pw_form.addRow("TOTP", self.totp)
        self._stack.addWidget(page_pw)

        # api-token page
        page_tok = QWidget()
        tok_form = QFormLayout(page_tok)
        self.api_token = QLineEdit()
        self.api_token.setPlaceholderText("fh_xxxxxxxx_…  (from /account/api-tokens)")
        self.api_token.setEchoMode(QLineEdit.Password)
        tok_form.addRow("API token", self.api_token)
        self._stack.addWidget(page_tok)

        # mode toggle row
        toggle_row = QHBoxLayout()
        self.toggle_link = QLabel(
            "<a href='#'>Use API token instead</a>"
        )
        self.toggle_link.setTextFormat(Qt.RichText)
        self.toggle_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        toggle_row.addWidget(self.toggle_link)
        toggle_row.addStretch()
        outer.addLayout(toggle_row)

        # error label
        self.error = QLabel("")
        self.error.setStyleSheet("color: #991b1b;")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)

        # buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.cancel_btn)
        self.signin_btn = QPushButton("Sign in")
        self.signin_btn.setDefault(True)
        btn_row.addWidget(self.signin_btn)
        outer.addLayout(btn_row)

    def _wire(self) -> None:
        self.cancel_btn.clicked.connect(self.reject)
        self.signin_btn.clicked.connect(self._on_signin)
        self.toggle_link.linkActivated.connect(self._on_toggle_link)

    def _toggle_mode(self, kind: str) -> None:
        if kind == "api_token":
            self._stack.setCurrentIndex(1)
            self.toggle_link.setText("<a href='#'>Use email + password instead</a>")
            # Pre-fill from keyring if available.
            stored = get_secret("api_token", self.server_url.text().rstrip("/"))
            if stored:
                self.api_token.setText(stored)
        else:
            self._stack.setCurrentIndex(0)
            self.toggle_link.setText("<a href='#'>Use API token instead</a>")

    # ---- handlers -------------------------------------------------------

    def _on_toggle_link(self, _href: str) -> None:
        self._cfg.auth_kind = (
            "api_token" if self._stack.currentIndex() == 0 else "password"
        )
        self._toggle_mode(self._cfg.auth_kind)

    def _on_signin(self) -> None:
        self.error.hide()
        server = self.server_url.text().strip().rstrip("/")
        if not server:
            self._show_error("Server URL is required.")
            return
        kind = "api_token" if self._stack.currentIndex() == 1 else "password"
        try:
            if kind == "api_token":
                api = ApiClient(server, api_token=self.api_token.text().strip())
                me = api_pkg.me(api)
                set_secret("api_token", server, self.api_token.text().strip())
            else:
                api = ApiClient(server)
                api_pkg.login(
                    api,
                    email=self.email.text().strip(),
                    password=self.password.text(),
                    totp_code=self.totp.text().strip() or None,
                )
                me = api_pkg.me(api)
        except ApiError as exc:
            if exc.code == "TOTP_REQUIRED":
                self._show_error(
                    "Two-factor code required. Enter the 6-digit code from your authenticator."
                )
                self.totp.setFocus()
                return
            if exc.code == "INVALID_TOTP":
                self._show_error("Invalid TOTP code. Try again.")
                self.totp.clear()
                self.totp.setFocus()
                return
            self._show_error(exc.message or "Sign-in failed.")
            return
        except Exception as exc:  # network / TLS / DNS
            self._show_error(f"Could not reach server: {exc}")
            return

        # Persist non-secret bits.
        self._cfg.server_url = server
        self._cfg.auth_kind = kind
        if kind == "password":
            self._cfg.last_email = self.email.text().strip()
        save_config(self._cfg)

        self.accepted_with_client.emit(api, me)
        self.accept()

    def _show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.show()
