"""One secret (server v2.24.0): reveal it, or follow who has.

A recipient - or the requester reading the answer to their request - sees the
terms, the passphrase field(s) the secret needs and Reveal. The text lands in
a ``RevealCard``: masked until Show, Copy at any time. The card stays on screen
after the last view (the metadata reload then says the secret is gone, and the
text the user just spent their view on must not vanish with it), and it drops
the text when the view is left.

A sender sees the state, the roster, the activity log, their links again and
Burn now. Nobody but a recipient ever receives the text.

A wrong passphrase is a 403 that spends no view; it shows under the field.
"""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..formatters import format_datetime, format_expiry
from ..i18n import t
from ..models import MeResponse, SecretRecipientStatus, SecretResponse
from ..secret_format import from_text, label_or_placeholder, secret_state, view_limit_text
from . import _messagebox as mb
from ._async import run_in_background
from .secret_widgets import (
    ERROR,
    MUTED,
    LinkRow,
    RevealCard,
    back_button,
    danger_button,
    heading,
    help_label,
)
from .widgets import PillLabel, alive


class SecretDetailView(ctk.CTkFrame):
    def __init__(
        self,
        master,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        secret_id: str,
        *,
        nav,
        on_back: Callable[[], None],
        back_text: Optional[str] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._back_text = back_text or t("secrets.detail.back")
        self._app_root = root
        self._api = api
        self._me = me
        self._secret_id = secret_id
        self._nav = nav
        self._on_back = on_back
        self._secret: Optional[SecretResponse] = None
        # Set once THIS view revealed the text: keeps the card mounted after
        # the last view, when the reloaded metadata says it can no longer be
        # revealed.
        self._revealed_here = False
        self._busy = False
        self.passphrase_var = ctk.StringVar()
        self.request_passphrase_var = ctk.StringVar()
        self.reveal_error_var = ctk.StringVar()
        self._reveal_btn: Optional[ctk.CTkButton] = None
        self._build()
        self._load()
        self._top = self.winfo_toplevel()
        self._esc_funcid = self._top.bind("<Escape>", lambda _e: self._leave(), add="+")
        self.bind("<Destroy>", self._on_destroy_unbind, add="+")

    def _on_destroy_unbind(self, _event) -> None:
        try:
            self._top.unbind("<Escape>", self._esc_funcid)
        except Exception:
            pass

    def _leave(self) -> None:
        self._card.clear()
        self._on_back()

    def _toast(self, text: str, kind: str = "info") -> None:
        self._nav.toast(text, kind=kind)

    # ---- skeleton ------------------------------------------------------------

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        back_button(header, self._back_text, self._leave).pack(side="left")

        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=16, pady=(8, 12))
        self._body.grid_columnconfigure(0, weight=1)
        # Fixed slots in a fixed order; an empty slot is grid_remove'd so it
        # takes no space.
        self._head_box = self._slot(0)
        self._note_box = self._slot(1)
        self._status_box = self._slot(2)
        # The card sits above the form, so the text is in view the moment it
        # arrives (a secret with views left keeps its form below).
        self._card_box = self._slot(3)
        self._form_box = self._slot(4)
        self._sender_box = self._slot(5)
        self._actions_box = self._slot(6)
        self._card = RevealCard(
            self._card_box,
            on_copy_failed=lambda: self._toast(t("secrets.copy_failed"), kind="error"),
        )
        self._card.pack(fill="x")
        self.title_var = ctk.StringVar(value=t("common.loading"))
        ctk.CTkLabel(
            self._head_box, textvariable=self.title_var, anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x")
        self._head_box.grid()

    def _slot(self, row: int) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._body, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        frame.grid_remove()
        return frame

    @staticmethod
    def _clear(box: ctk.CTkFrame) -> None:
        for child in box.winfo_children():
            child.destroy()

    @staticmethod
    def _show(box: ctk.CTkFrame, visible: bool) -> None:
        if visible:
            box.grid()
        else:
            box.grid_remove()

    # ---- load + render -----------------------------------------------------------

    def _load(self) -> None:
        def _fetch():
            return api_pkg.get_secret(self._api, self._secret_id)

        def _done(secret):
            if not alive(self):
                return
            self._secret = secret
            self._render()

        def _failed(exc):
            if not alive(self):
                return
            msg = exc.localized() if hasattr(exc, "localized") else str(exc)
            if self._secret is None:
                self._toast(f"{t('secrets.detail.could_not_load')}: {msg}", kind="error")
                self._leave()
            else:
                self._toast(msg, kind="error")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _is_reader(self) -> bool:
        return self._secret is not None and self._secret.viewer_role == "recipient"

    def _render(self) -> None:
        s = self._secret
        if s is None:
            return
        self._render_head(s)
        self._render_note(s)
        if self._is_reader():
            self._render_status(s)
            self._render_form(s)
            self._show(self._sender_box, False)
        else:
            self._show(self._status_box, False)
            self._show(self._form_box, False)
            self._render_sender(s)
        self._show(self._card_box, self._revealed_here)
        self._render_actions(s)

    def _render_head(self, s: SecretResponse) -> None:
        self.title_var.set(label_or_placeholder(s.label))
        for child in self._head_box.winfo_children()[1:]:
            child.destroy()
        state_text, tone = secret_state(s.state)
        PillLabel(self._head_box, text=state_text, state=tone, width=110).pack(anchor="w", pady=(4, 6))
        bits: list[str] = []
        if self._is_reader():
            bits.append(t("secrets.detail.from_line", who=from_text(s)))
        bits.append(t("secrets.detail.sent_line", d=format_datetime(s.created_at)))
        bits.append(t("secrets.detail.expires_line", d=format_expiry(s.expires_at)))
        bits.append(t("secrets.detail.views_line", v=view_limit_text(s.max_views, s.view_scope)))
        if not self._is_reader() and s.views_used is not None:
            bits.append(t("secrets.detail.viewed_line", n=s.views_used))
        if s.has_passphrase:
            bits.append(
                t("secrets.detail.passphrase_burn", n=s.burn_after_failures)
                if s.burn_after_failures else t("secrets.detail.passphrase_yes")
            )
        if s.ended_at is not None:
            bits.append(t("secrets.detail.ended_line", d=format_datetime(s.ended_at)))
        help_label(self._head_box, "  ·  ".join(bits), wrap=860).pack(fill="x")

    def _render_note(self, s: SecretResponse) -> None:
        box = self._note_box
        self._clear(box)
        text = ""
        if s.is_answer and self._is_reader():
            text = t("secrets.detail.answer_to_yours")
        elif s.is_answer:
            text = t("secrets.detail.answer_by_you")
        elif s.viewer_role == "sender":
            text = t("secrets.detail.sender_note")
        elif s.viewer_role == "admin":
            text = t("secrets.detail.admin_note")
        if not text:
            self._show(box, False)
            return
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x")
        help_label(row, text).pack(side="left", fill="x", expand=True)
        if s.is_answer and s.request_id:
            rid = s.request_id
            ctk.CTkButton(
                row, text=t("secrets.detail.see_request"), width=150,
                command=lambda: self._nav.open_request(rid),
            ).pack(side="right")
        self._show(box, True)

    def _reader_status(self, s: SecretResponse) -> str:
        """Why a reader cannot reveal it (empty when they can)."""
        if s.can_reveal:
            return ""
        if s.state != "active":
            return {
                "burned": t("secrets.detail.ended_burned"),
                "expired": t("secrets.detail.ended_expired"),
                "revoked": t("secrets.detail.ended_revoked"),
            }.get(s.state, t("secrets.detail.ended_expired"))
        if s.burned_for_me:
            return t("secrets.detail.burned_for_me")
        if not s.still_recipient:
            return t("secrets.detail.not_recipient")
        return t("secrets.detail.no_views_left")

    def _render_status(self, s: SecretResponse) -> None:
        box = self._status_box
        self._clear(box)
        # After the last view this view's own card says what happened.
        text = "" if self._revealed_here else self._reader_status(s)
        if text:
            help_label(box, text).pack(fill="x")
        self._show(box, bool(text))

    def _render_form(self, s: SecretResponse) -> None:
        box = self._form_box
        self._clear(box)
        self._reveal_btn = None
        if not s.can_reveal:
            self._show(box, False)
            return
        if s.my_views_left == 1:
            help_label(box, t("secrets.reveal.last_view_warning"), text_color=("#92400e", "#fcd34d")).pack(
                fill="x", pady=(0, 6),
            )
        if s.has_request_passphrase:
            ctk.CTkLabel(box, text=t("secrets.reveal.request_passphrase_label"), anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(box, textvariable=self.request_passphrase_var, show="•", width=320)
            entry.pack(anchor="w")
            entry.bind("<Return>", lambda _e: self._reveal())
            help_label(box, t("secrets.reveal.request_passphrase_help")).pack(fill="x", pady=(0, 6))
        if s.has_passphrase:
            ctk.CTkLabel(box, text=t("secrets.reveal.passphrase_label"), anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(box, textvariable=self.passphrase_var, show="•", width=320)
            entry.pack(anchor="w")
            entry.bind("<Return>", lambda _e: self._reveal())
            if s.burn_after_failures:
                left = max(0, s.burn_after_failures - s.my_failed_attempts)
                hint = t("secrets.reveal.passphrase_help_burn", n=left)
            else:
                hint = t("secrets.reveal.passphrase_help")
            help_label(box, hint).pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(box, textvariable=self.reveal_error_var, anchor="w", text_color=ERROR,
                     justify="left", wraplength=640).pack(fill="x")
        self._reveal_btn = ctk.CTkButton(box, text=t("secrets.reveal.button"), width=160,
                                         command=self._reveal)
        self._reveal_btn.pack(anchor="w", pady=(4, 0))
        self._show(box, True)

    # ---- sender view ---------------------------------------------------------

    def _render_sender(self, s: SecretResponse) -> None:
        box = self._sender_box
        self._clear(box)
        heading(box, t("secrets.detail.recipients_title")).pack(fill="x", pady=(0, 4))
        table = ctk.CTkFrame(box, fg_color="transparent")
        table.pack(fill="x")
        for col, weight in enumerate((4, 2, 3)):
            table.grid_columnconfigure(col, weight=weight, uniform="rcp")
        for col, key in enumerate(("secrets.detail.col_recipient", "secrets.detail.col_views",
                                   "secrets.detail.col_status")):
            ctk.CTkLabel(table, text=t(key), anchor="w", text_color=MUTED,
                         font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=col, sticky="ew", padx=4)
        row = 1
        for r in s.recipients:
            for col, text in enumerate((self._recipient_name(r), self._recipient_views(r),
                                        self._recipient_status(r))):
                ctk.CTkLabel(table, text=text, anchor="w", justify="left").grid(
                    row=row, column=col, sticky="ew", padx=4,
                )
            row += 1
            for m in r.members:
                name = f"    {m.user.display_name}"
                if not m.eligible:
                    name += f" ({t('secrets.detail.left_group')})"
                status = t("secrets.detail.status_burned") if m.burned else ""
                for col, text in enumerate((name, t("secrets.detail.views_used", n=m.views_used), status)):
                    ctk.CTkLabel(table, text=text, anchor="w", text_color=MUTED).grid(
                        row=row, column=col, sticky="ew", padx=4,
                    )
                row += 1

        heading(box, t("secrets.detail.activity_title")).pack(fill="x", pady=(12, 4))
        if not s.events:
            help_label(box, t("secrets.detail.no_activity")).pack(fill="x")
        else:
            log = ctk.CTkFrame(box, fg_color="transparent")
            log.pack(fill="x")
            for col, weight in enumerate((2, 2, 3, 2)):
                log.grid_columnconfigure(col, weight=weight, uniform="act")
            for col, key in enumerate(("secrets.detail.col_when", "secrets.detail.col_what",
                                       "secrets.detail.col_who", "secrets.detail.col_ip")):
                ctk.CTkLabel(log, text=t(key), anchor="w", text_color=MUTED,
                             font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=col, sticky="ew", padx=4)
            for i, ev in enumerate(s.events, start=1):
                who = (ev.user.display_name if ev.user else ev.email
                       or (t("secrets.detail.the_link") if ev.kind == "link" else "-"))
                for col, text in enumerate((format_datetime(ev.at), self._outcome(ev.outcome),
                                            who, ev.ip or "-")):
                    ctk.CTkLabel(log, text=text, anchor="w").grid(row=i, column=col, sticky="ew", padx=4)

        if s.viewer_role == "sender" and any(r.kind in ("email", "link") for r in s.recipients):
            heading(box, t("secrets.detail.links_title")).pack(fill="x", pady=(12, 4))
            help_label(box, t("secrets.detail.links_help")).pack(fill="x")
            self._links_host = ctk.CTkFrame(box, fg_color="transparent")
            self._links_host.pack(fill="x", pady=(4, 0))
            ctk.CTkButton(self._links_host, text=t("secrets.detail.show_links"), width=150,
                          command=self._show_links).pack(anchor="w")
        self._show(box, True)

    @staticmethod
    def _outcome(outcome: str) -> str:
        key = {
            "viewed": "secrets.outcome.viewed",
            "wrong_passphrase": "secrets.outcome.wrong_passphrase",
            "locked": "secrets.outcome.locked",
            "burned": "secrets.outcome.burned",
        }.get(outcome)
        return t(key) if key else outcome

    @staticmethod
    def _recipient_name(r: SecretRecipientStatus) -> str:
        if r.user is not None:
            return r.user.display_name
        if r.group is not None:
            return t("secrets.detail.group_name", name=r.group.name)
        if r.email:
            return r.email
        return t("secrets.detail.the_link")

    @staticmethod
    def _recipient_views(r: SecretRecipientStatus) -> str:
        if r.views_left is None:
            return t("secrets.detail.views_used", n=r.views_used)
        return t("secrets.detail.views_used_left", n=r.views_used, left=r.views_left)

    @staticmethod
    def _recipient_status(r: SecretRecipientStatus) -> str:
        if r.revoked:
            return t("secrets.detail.status_revoked")
        if r.burned:
            return t("secrets.detail.status_burned")
        if r.locked_until is not None:
            return t("secrets.detail.status_locked", d=format_datetime(r.locked_until))
        if r.kind == "email":
            return (t("secrets.detail.status_emailed", d=format_datetime(r.emailed_at))
                    if r.emailed_at else t("secrets.detail.status_not_emailed"))
        return ""

    def _show_links(self) -> None:
        host = self._links_host

        def _fetch():
            return api_pkg.get_secret_links(self._api, self._secret_id)

        def _done(resp):
            if not alive(self) or not alive(host):
                return
            self._clear(host)
            if not resp.items:
                help_label(host, t("secrets.detail.no_links")).pack(fill="x")
            for item in resp.items:
                caption = item.email or t("secrets.detail.the_link")
                if item.url:
                    LinkRow(host, item.url, caption=caption,
                            on_copy_failed=lambda: self._toast(t("secrets.copy_failed"), kind="error"),
                            ).pack(fill="x", pady=(0, 6))
                else:
                    help_label(host, f"{caption}: {t('secrets.detail.link_unavailable')}").pack(fill="x")
            help_label(host, t("secrets.detail.links_recorded")).pack(fill="x", pady=(2, 0))

        def _failed(exc):
            if alive(self):
                msg = exc.localized() if hasattr(exc, "localized") else str(exc)
                self._toast(msg, kind="error")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    # ---- actions -------------------------------------------------------------

    def _render_actions(self, s: SecretResponse) -> None:
        box = self._actions_box
        self._clear(box)
        if s.can_burn and s.state == "active":
            text = (t("secrets.detail.burn_answer") if s.is_answer and self._is_reader()
                    else t("secrets.detail.burn"))
            danger_button(box, text, self._burn, width=180).pack(anchor="w")
            self._show(box, True)
        else:
            self._show(box, False)

    def _reveal(self) -> None:
        s = self._secret
        if s is None or self._busy or not s.can_reveal:
            return
        passphrase = self.passphrase_var.get() if s.has_passphrase else None
        request_passphrase = self.request_passphrase_var.get() if s.has_request_passphrase else None
        self._busy = True
        self.reveal_error_var.set("")
        if self._reveal_btn is not None:
            self._reveal_btn.configure(state="disabled")

        def _fetch():
            return api_pkg.reveal_secret(
                self._api, self._secret_id,
                passphrase=passphrase, request_passphrase=request_passphrase,
            )

        def _done(resp):
            self._busy = False
            if not alive(self):
                return
            self.passphrase_var.set("")
            self.request_passphrase_var.set("")
            if resp.ended:
                note = t("secrets.reveal.destroyed")
            elif resp.views_left == 0:
                note = t("secrets.reveal.no_views_left")
            elif resp.views_left is not None:
                note = t("secrets.reveal.views_left", n=resp.views_left)
            else:
                note = ""
            self._revealed_here = True
            self._card.set_text(resp.content, note)
            self._show(self._card_box, True)
            try:
                self._body._parent_canvas.yview_moveto(0.0)
            except Exception:
                pass
            self._load()

        def _failed(exc):
            self._busy = False
            if not alive(self):
                return
            msg = exc.localized() if hasattr(exc, "localized") else str(exc)
            self.reveal_error_var.set(msg)
            if self._reveal_btn is not None and alive(self._reveal_btn):
                self._reveal_btn.configure(state="normal")
            # Attempts left, a lock or a burn may have changed.
            self._load()

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _burn(self) -> None:
        s = self._secret
        if s is None:
            return
        answer = s.is_answer and self._is_reader()
        if not mb.confirm(
            self,
            t("secrets.detail.burn_answer_title") if answer else t("secrets.detail.burn_title"),
            t("secrets.detail.burn_confirm"),
            ok_text=t("secrets.detail.burn_answer") if answer else t("secrets.detail.burn"),
        ):
            return

        def _fetch():
            return api_pkg.burn_secret(self._api, self._secret_id)

        def _done(secret):
            if not alive(self):
                return
            self._secret = secret
            self._toast(t("secrets.detail.burned_toast"), kind="success")
            self._render()

        def _failed(exc):
            if alive(self):
                msg = exc.localized() if hasattr(exc, "localized") else str(exc)
                self._toast(msg, kind="error")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def destroy(self) -> None:
        # The card drops the text in its own destroy; clearing first also
        # covers a teardown that never reaches the card.
        try:
            self._card.clear()
        except Exception:
            pass
        super().destroy()
