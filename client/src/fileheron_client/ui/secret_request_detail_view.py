"""One secret request (server v2.24.0).

The requester sees whom they asked, its state, their links again, Cancel, and
- once it is answered - Open the answer (an ordinary secret, revealed in
``SecretDetailView``; Back returns here). Someone asked sees what is asked
for, by whom, how the answer may be read, and the answer form; the first
answer closes the request. The typed answer is cleared as soon as the server
has it.
"""
from __future__ import annotations

from typing import Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..formatters import format_datetime
from ..i18n import t
from ..models import MeResponse, SecretRequestResponse, SecretRequestTargetStatus
from ..secret_format import answer_terms_text, request_state
from ..secret_rules import answer_blockers, generate_password
from . import _messagebox as mb
from ._async import run_in_background
from .secret_widgets import (
    ERROR,
    MUTED,
    BlockerBox,
    LinkRow,
    PassphrasePair,
    back_button,
    danger_button,
    heading,
    help_label,
)
from .widgets import PillLabel, alive

_CLOSED_KEYS = {
    "fulfilled": "secrets.request.closed_fulfilled",
    "cancelled": "secrets.request.closed_cancelled",
    "expired": "secrets.request.closed_expired",
    "requester_unavailable": "secrets.request.closed_requester_unavailable",
}
_KIND_KEYS = {
    "user": "secrets.kind.user",
    "group": "secrets.kind.group",
    "email": "secrets.kind.email",
    "link": "secrets.kind.link",
}


class SecretRequestDetailView(ctk.CTkFrame):
    def __init__(
        self,
        master,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        request_id: str,
        *,
        nav,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._me = me
        self._request_id = request_id
        self._nav = nav
        self._req: Optional[SecretRequestResponse] = None
        self._answered_here: Optional[str] = None
        self._sending = False
        self.own_passphrase_var = ctk.BooleanVar(value=False)
        self.answer_error_var = ctk.StringVar()
        self._build()
        self._load()

    def _toast(self, text: str, kind: str = "info") -> None:
        self._nav.toast(text, kind=kind)

    # ---- skeleton ------------------------------------------------------------

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        back_button(header, t("secrets.request.back"), self._nav.back_to_list).pack(side="left")
        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=16, pady=(8, 12))
        self._body.grid_columnconfigure(0, weight=1)
        self._head_box = self._slot(0)
        self._status_box = self._slot(1)
        self._owner_box = self._slot(2)
        self._answer_box = self._slot(3)
        self._actions_box = self._slot(4)

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

    # ---- load + render -------------------------------------------------------

    def _load(self) -> None:
        def _fetch():
            return api_pkg.get_secret_request(self._api, self._request_id)

        def _done(req):
            if not alive(self):
                return
            self._req = req
            self._render()

        def _failed(exc):
            if not alive(self):
                return
            msg = exc.localized() if hasattr(exc, "localized") else str(exc)
            if self._req is None:
                self._toast(f"{t('secrets.request.could_not_load')}: {msg}", kind="error")
                self._nav.back_to_list()
            else:
                self._toast(msg, kind="error")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _is_owner(self) -> bool:
        return self._req is not None and self._req.viewer_role in ("requester", "admin")

    def _render(self) -> None:
        r = self._req
        if r is None:
            return
        self._render_head(r)
        self._render_status(r)
        if self._is_owner():
            self._render_owner(r)
            self._show(self._answer_box, False)
        else:
            self._show(self._owner_box, False)
            self._render_answer(r)
        self._render_actions(r)

    def _render_head(self, r: SecretRequestResponse) -> None:
        box = self._head_box
        self._clear(box)
        ctk.CTkLabel(box, text=r.label, anchor="w", justify="left", wraplength=760,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(fill="x")
        state_text, tone = request_state(r.state)
        PillLabel(box, text=state_text, state=tone, width=110).pack(anchor="w", pady=(4, 6))
        bits = []
        if not self._is_owner() or r.viewer_role == "admin":
            bits.append(t("secrets.request.asked_by_line", who=r.requester.display_name))
        bits.append(t("secrets.request.asked_line", d=format_datetime(r.created_at)))
        bits.append(t("secrets.request.open_until_line", d=format_datetime(r.expires_at)))
        bits.append(t("secrets.request.terms_line",
                      terms=answer_terms_text(r.answer_max_views, r.answer_expires_in_sec)))
        if r.has_passphrase:
            bits.append(t("secrets.request.passphrase_requester") if r.viewer_role != "requester"
                        else t("secrets.request.passphrase_yours"))
        help_label(box, "  ·  ".join(bits), wrap=860).pack(fill="x")
        if r.note:
            ctk.CTkLabel(box, text=t("secrets.request.note_heading"), anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(fill="x", pady=(8, 0))
            ctk.CTkLabel(box, text=r.note, anchor="w", justify="left", wraplength=760).pack(fill="x")
        self._show(box, True)

    def _render_status(self, r: SecretRequestResponse) -> None:
        box = self._status_box
        self._clear(box)
        lines: list[str] = []
        if self._answered_here:
            lines.append(t("secrets.request.answer_sent", name=self._answered_here))
        elif r.state != "open":
            key = _CLOSED_KEYS.get(r.closed_reason or r.state, "secrets.request.closed_expired")
            lines.append(t(key))
        if r.fulfilled_at is not None and self._is_owner():
            lines.append(t("secrets.request.answered_line", d=format_datetime(r.fulfilled_at),
                           who=self._answered_by(r)))
        for line in lines:
            help_label(box, line, text_color=("gray10", "gray90")).pack(fill="x")
        if self._is_owner() and r.answer_secret_id and r.viewer_role == "requester":
            sid, rid = r.answer_secret_id, r.id
            ctk.CTkButton(
                box, text=t("secrets.request.open_answer"), width=180,
                command=lambda: self._nav.open_secret(
                    sid, back=lambda: self._nav.open_request(rid),
                    back_text=t("secrets.request.back_to_request"),
                ),
            ).pack(anchor="w", pady=(6, 0))
        self._show(box, bool(box.winfo_children()))

    @staticmethod
    def _answered_by(r: SecretRequestResponse) -> str:
        if r.answered_by is not None:
            return r.answered_by.display_name
        if r.answered_via == "email" and r.answered_by_email:
            return r.answered_by_email
        if r.answered_via == "link":
            return t("secrets.request.via_link")
        return t("secrets.request.via_email")

    def _render_owner(self, r: SecretRequestResponse) -> None:
        box = self._owner_box
        self._clear(box)
        heading(box, t("secrets.request.asked_title")).pack(fill="x", pady=(0, 4))
        table = ctk.CTkFrame(box, fg_color="transparent")
        table.pack(fill="x")
        for col, weight in enumerate((4, 2, 3)):
            table.grid_columnconfigure(col, weight=weight, uniform="tgt")
        for col, key in enumerate(("secrets.request.col_target", "secrets.request.col_kind",
                                   "secrets.request.col_mailed")):
            ctk.CTkLabel(table, text=t(key), anchor="w", text_color=MUTED,
                         font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=col, sticky="ew", padx=4)
        for i, target in enumerate(r.targets, start=1):
            for col, text in enumerate((self._target_name(target), t(_KIND_KEYS.get(target.kind, "secrets.kind.user")),
                                        format_datetime(target.notified_at) if target.notified_at else "-")):
                ctk.CTkLabel(table, text=text, anchor="w").grid(row=i, column=col, sticky="ew", padx=4)

        if (r.viewer_role == "requester" and r.state == "open"
                and any(x.kind in ("email", "link") for x in r.targets)):
            heading(box, t("secrets.request.links_title")).pack(fill="x", pady=(12, 4))
            help_label(box, t("secrets.request.links_help")).pack(fill="x")
            self._links_host = ctk.CTkFrame(box, fg_color="transparent")
            self._links_host.pack(fill="x", pady=(4, 0))
            ctk.CTkButton(self._links_host, text=t("secrets.detail.show_links"), width=150,
                          command=self._show_links).pack(anchor="w")
        self._show(box, True)

    @staticmethod
    def _target_name(target: SecretRequestTargetStatus) -> str:
        if target.user is not None:
            return target.user.display_name
        if target.group is not None:
            return t("secrets.detail.group_name", name=target.group.name)
        if target.email:
            return target.email
        return t("secrets.detail.the_link")

    def _show_links(self) -> None:
        host = self._links_host

        def _fetch():
            return api_pkg.get_secret_request_links(self._api, self._request_id)

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

    # ---- the answer form (someone asked) -------------------------------------------

    def _render_answer(self, r: SecretRequestResponse) -> None:
        box = self._answer_box
        self._clear(box)
        if not r.can_answer or self._answered_here:
            self._show(box, False)
            return
        name = r.requester.display_name
        help_label(box, t("secrets.request.only_requester_passphrase", name=name) if r.has_passphrase
                   else t("secrets.request.only_requester", name=name)).pack(fill="x", pady=(0, 6))
        heading(box, t("secrets.request.content_label")).pack(fill="x")
        self.answer_text = ctk.CTkTextbox(box, height=90, wrap="word")
        self.answer_text.pack(fill="x")
        self.answer_text.bind("<KeyRelease>", lambda _e: self._recheck(), add="+")
        ctk.CTkButton(box, text=t("secrets.create.generate"), width=200,
                      command=self._generate).pack(anchor="w", pady=(6, 0))
        self._pp_toggle = ctk.CTkCheckBox(box, text=t("secrets.request.passphrase_toggle"),
                                          variable=self.own_passphrase_var,
                                          command=self._toggle_passphrase)
        self._pp_toggle.pack(anchor="w", pady=(10, 0))
        self._pp_host = ctk.CTkFrame(box, fg_color="transparent")
        self.answer_passphrase = PassphrasePair(self._pp_host, label=t("secrets.create.passphrase_label"),
                                                on_change=self._recheck)
        self.answer_passphrase.pack(fill="x")
        help_label(self._pp_host, t("secrets.request.answer_passphrase_help")).pack(fill="x")
        if self.own_passphrase_var.get():
            self._pp_host.pack(fill="x", padx=(28, 0), after=self._pp_toggle)
        ctk.CTkLabel(box, textvariable=self.answer_error_var, anchor="w", text_color=ERROR,
                     justify="left", wraplength=640).pack(fill="x", pady=(6, 0))
        footer = ctk.CTkFrame(box, fg_color="transparent")
        footer.pack(fill="x", pady=(4, 0))
        self._answer_blockers = BlockerBox(footer)
        self._answer_blockers.pack(side="left", fill="x", expand=True)
        self._answer_btn = ctk.CTkButton(footer, text=t("secrets.request.answer_send"), width=160,
                                         command=self._send_answer)
        self._answer_btn.pack(side="right", anchor="s")
        self._show(box, True)
        self._recheck()

    def _toggle_passphrase(self) -> None:
        if self.own_passphrase_var.get():
            self._pp_host.pack(fill="x", padx=(28, 0), after=self._pp_toggle)
        else:
            self.answer_passphrase.wipe()
            self._pp_host.pack_forget()
        self._recheck()

    def _generate(self) -> None:
        current = self.answer_text.get("1.0", "end-1c")
        self.answer_text.insert("end", ("\n" if current else "") + generate_password())
        self._recheck()

    def _answer_values(self) -> tuple[str, str, str]:
        content = self.answer_text.get("1.0", "end-1c")
        if self.own_passphrase_var.get():
            pp, rep = self.answer_passphrase.values()
        else:
            pp, rep = "", ""
        return content, pp, rep

    def _recheck(self) -> None:
        if not hasattr(self, "_answer_btn") or not alive(self._answer_btn):
            return
        blockers = answer_blockers(*self._answer_values())
        self._answer_blockers.show_blockers(blockers)
        self._answer_btn.configure(state="disabled" if blockers or self._sending else "normal")

    def _send_answer(self) -> None:
        r = self._req
        if r is None or self._sending:
            return
        content, pp, rep = self._answer_values()
        if answer_blockers(content, pp, rep):
            return
        passphrase = pp or None
        fallback_name = r.requester.display_name
        self._sending = True
        self.answer_error_var.set("")
        self._answer_btn.configure(state="disabled", text=t("secrets.sending"))

        def _fetch():
            return api_pkg.answer_secret_request(
                self._api, self._request_id, content=content, passphrase=passphrase,
            )

        def _done(resp):
            self._sending = False
            if not alive(self):
                return
            # The answer is not kept a moment longer than it has to be.
            try:
                self.answer_text.delete("1.0", "end")
                self.answer_passphrase.wipe()
            except Exception:
                pass
            self._answered_here = resp.requester_name or fallback_name or t("secrets.request.the_requester")
            self._toast(t("secrets.request.answer_sent", name=self._answered_here), kind="success")
            self._load()

        def _failed(exc):
            self._sending = False
            if not alive(self):
                return
            self.answer_error_var.set(exc.localized() if hasattr(exc, "localized") else str(exc))
            if alive(self._answer_btn):
                self._answer_btn.configure(text=t("secrets.request.answer_send"))
            self._recheck()
            # Someone else answered first, or it was cancelled: show that. Any
            # other failure keeps the form - and what was typed - as it is.
            if getattr(exc, "code", "") in ("SECRET_REQUEST_CLOSED", "SECRET_REQUEST_NOT_FOUND"):
                self._load()

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    # ---- actions -------------------------------------------------------------

    def _render_actions(self, r: SecretRequestResponse) -> None:
        box = self._actions_box
        self._clear(box)
        if self._is_owner() and r.state == "open":
            danger_button(box, t("secrets.request.cancel"), self._cancel, width=180).pack(anchor="w")
            self._show(box, True)
        else:
            self._show(box, False)

    def _cancel(self) -> None:
        if not mb.confirm(
            self, t("secrets.request.cancel_title"), t("secrets.request.cancel_confirm"),
            ok_text=t("secrets.request.cancel"),
        ):
            return

        def _fetch():
            return api_pkg.cancel_secret_request(self._api, self._request_id)

        def _done(req):
            if not alive(self):
                return
            self._req = req
            self._toast(t("secrets.request.cancelled_toast"), kind="success")
            self._render()

        def _failed(exc):
            if alive(self):
                msg = exc.localized() if hasattr(exc, "localized") else str(exc)
                self._toast(msg, kind="error")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)
