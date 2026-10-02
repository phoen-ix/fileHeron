"""Send a secret (server v2.24.0), and the base the request form shares.

Both forms derive Send from a VISIBLE blockers list (``secret_rules``): the
button is disabled exactly while the list below the form is non-empty, so it
can never be grey without a reason on screen.

Who may be reached follows ``/me``: a client picks only people (never a group),
and addresses and the link appear only with ``can_send_secrets_external``.
The text is cleared from the form as soon as the server has it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..i18n import t
from ..models import MeResponse
from ..secret_rules import (
    NEVER,
    SecretForm,
    default_preset,
    generate_password,
    limits_of,
    parse_addresses,
    preset_seconds,
    scope_options,
    secret_blockers,
    secret_expiry_presets,
    views_value,
)
from ._async import run_in_background
from .recipient_picker import RecipientPickerWidget
from .secret_widgets import (
    ERROR,
    BlockerBox,
    LinkRow,
    PassphrasePair,
    back_button,
    heading,
    help_label,
)
from .widgets import alive

# Preset key -> its label key (literal, so the locale tests can see them).
PRESET_LABEL_KEYS = {
    "1h": "secrets.preset.1h",
    "1d": "secrets.preset.1d",
    "7d": "secrets.preset.7d",
    "30d": "secrets.preset.30d",
    "90d": "secrets.preset.90d",
    NEVER: "secrets.preset.never",
}
SCOPE_LABEL_KEYS = {
    "per_person": ("secrets.scope.per_person", "secrets.scope.per_person_help"),
    "per_recipient": ("secrets.scope.per_recipient", "secrets.scope.per_recipient_help"),
    "total": ("secrets.scope.total", "secrets.scope.total_help"),
}


def until(seconds: int) -> datetime:
    """An aware UTC instant `seconds` from now (serialised as UTC as-is)."""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class ComposeBase(ctk.CTkFrame):
    """Header, a scrolling form, and a footer with the blockers and Send."""

    TITLE_KEY = ""
    SEND_KEY = ""
    SENDING_KEY = "secrets.sending"

    def __init__(self, master, root: ctk.CTk, api: ApiClient, me: MeResponse, *, nav) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._me = me
        self._nav = nav
        self._limits = limits_of(me)
        self._can_external = bool(me.can_send_secrets_external) and me.role != "client"
        self._is_client = me.role == "client"
        self._sending = False
        self.addresses_var = ctk.StringVar()
        self.link_var = ctk.BooleanVar(value=False)
        self.limit_views_var = ctk.BooleanVar(value=True)
        self.views_var = ctk.StringVar(value="1")
        self.error_var = ctk.StringVar()
        self.picker: Optional[RecipientPickerWidget] = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        back_button(header, t("secrets.detail.back"), self._nav.back_to_list).pack(side="left")
        ctk.CTkLabel(header, text=t(self.TITLE_KEY), anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(16, 0))

        self._footer = ctk.CTkFrame(self, fg_color="transparent")
        self._footer.pack(side="bottom", fill="x", padx=16, pady=(4, 12))
        self._blockers = BlockerBox(self._footer)
        self._blockers.pack(side="left", fill="x", expand=True)
        self._send_btn = ctk.CTkButton(self._footer, text=t(self.SEND_KEY), width=180,
                                       command=self._send)
        self._send_btn.pack(side="right", anchor="s")
        ctk.CTkLabel(self, textvariable=self.error_var, anchor="w", text_color=ERROR,
                     justify="left", wraplength=760).pack(side="bottom", fill="x", padx=16)

        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        self._build_form(self._body)
        self.addresses_var.trace_add("write", lambda *_a: self.recheck())
        self.link_var.trace_add("write", lambda *_a: self.recheck())
        self.limit_views_var.trace_add("write", lambda *_a: self.recheck())
        self.views_var.trace_add("write", lambda *_a: self.recheck())
        self.recheck()

    # ---- shared sections -------------------------------------------------------

    def _section(self, parent, title: str, help_text: str = "") -> ctk.CTkFrame:
        heading(parent, title).pack(fill="x", pady=(10, 2))
        if help_text:
            help_label(parent, help_text).pack(fill="x", pady=(0, 4))
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x")
        return box

    def _audience(self, parent, *, title: str, client_hint: str,
                  link_label: str, link_help: str) -> None:
        box = self._section(parent, title, client_hint if self._is_client else "")
        bordered = ctk.CTkFrame(box, border_width=1, fg_color="transparent")
        bordered.pack(fill="x")
        self.picker = RecipientPickerWidget(
            bordered, self._app_root, self._api,
            allow_groups=not self._is_client, on_change=self.recheck,
        )
        self.picker.pack(fill="x", padx=6, pady=6)
        if self._can_external:
            ctk.CTkLabel(box, text=t("secrets.addresses_label"), anchor="w").pack(fill="x", pady=(8, 0))
            ctk.CTkEntry(box, textvariable=self.addresses_var,
                         placeholder_text=t("secrets.addresses_placeholder")).pack(fill="x")
            help_label(box, t("secrets.addresses_help")).pack(fill="x")
            ctk.CTkCheckBox(box, text=link_label, variable=self.link_var).pack(anchor="w", pady=(8, 0))
            help_label(box, link_help).pack(fill="x", padx=(28, 0))

    def _views_row(self, parent, label: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(2, 2))
        ctk.CTkCheckBox(row, text=label, variable=self.limit_views_var).pack(side="left")
        self._views_entry = ctk.CTkEntry(row, textvariable=self.views_var, width=70)
        self._views_entry.pack(side="left", padx=(12, 6))
        ctk.CTkLabel(row, text=t("secrets.create.views_unit"), anchor="w").pack(side="left")

    def _presets(self, parent, presets, initial: str, on_pick) -> ctk.CTkSegmentedButton:
        by_label = {t(PRESET_LABEL_KEYS[k]): k for k, _s in presets}
        seg = ctk.CTkSegmentedButton(
            parent, values=list(by_label), command=lambda lbl: on_pick(by_label[lbl]),
        )
        seg.set(t(PRESET_LABEL_KEYS[initial]))
        seg.pack(anchor="w", pady=(2, 2))
        return seg

    def _audience_values(self) -> tuple[list[int], list[int], list[str], bool]:
        users = self.picker.user_ids() if self.picker else []
        groups = self.picker.group_ids() if self.picker else []
        emails = parse_addresses(self.addresses_var.get())[0] if self._can_external else []
        link = bool(self.link_var.get()) and self._can_external
        return users, groups, emails, link

    # ---- blockers + send -------------------------------------------------------

    def recheck(self) -> None:
        if not hasattr(self, "_send_btn"):
            return
        try:
            self._views_entry.configure(
                state="normal" if self.limit_views_var.get() else "disabled",
            )
        except AttributeError:
            pass
        self._after_recheck()
        blockers = self.blockers()
        self._blockers.show_blockers(blockers)
        self._send_btn.configure(
            state="disabled" if blockers or self._sending else "normal",
        )

    def _after_recheck(self) -> None:
        """Hook for a form whose options depend on the picks."""

    def blockers(self) -> list[tuple[str, dict]]:
        raise NotImplementedError

    def _submit(self):
        """Runs on the worker thread; returns the server's answer."""
        raise NotImplementedError

    def _on_sent(self, resp) -> None:
        raise NotImplementedError

    def _send(self) -> None:
        if self._sending or self.blockers():
            return
        call = self._submit()
        self._sending = True
        self.error_var.set("")
        self._send_btn.configure(state="disabled", text=t(self.SENDING_KEY))

        def _done(resp):
            self._sending = False
            if not alive(self):
                return
            self._on_sent(resp)

        def _failed(exc):
            self._sending = False
            if not alive(self):
                return
            self._send_btn.configure(text=t(self.SEND_KEY))
            self.error_var.set(exc.localized() if hasattr(exc, "localized") else str(exc))
            self.recheck()

        run_in_background(self._app_root, call, on_done=_done, on_failed=_failed)

    def _show_sent(self, *, title: str, link_url: Optional[str], link_help: str,
                   open_text: str, open_cmd) -> None:
        """Replace the form with the "sent" panel."""
        self._body.destroy()
        self._footer.destroy()
        self.error_var.set("")
        panel = ctk.CTkFrame(self, fg_color="transparent")
        panel.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(panel, text=title, anchor="w",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x")
        if link_url:
            help_label(panel, link_help).pack(fill="x", pady=(6, 4))
            LinkRow(panel, link_url,
                    on_copy_failed=lambda: self._nav.toast(t("secrets.copy_failed"), kind="error"),
                    ).pack(fill="x")
        btns = ctk.CTkFrame(panel, fg_color="transparent")
        btns.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(btns, text=open_text, width=180, command=open_cmd).pack(side="left")
        back_button(btns, t("secrets.detail.back"), self._nav.back_to_list).pack(side="left", padx=(8, 0))


class SecretComposeView(ComposeBase):
    TITLE_KEY = "secrets.create.title"
    SEND_KEY = "secrets.create.send"

    def _build_form(self, body) -> None:
        self.label_var = ctk.StringVar()
        self.expiry_key = default_preset(secret_expiry_presets(self._limits))
        self.scope_var = ctk.StringVar(value="per_person")
        self.burn_var = ctk.BooleanVar(value=False)
        self.notify_var = ctk.BooleanVar(value=False)
        self._scopes: list[str] = []

        help_label(body, t("secrets.create.intro")).pack(fill="x", pady=(0, 4))
        box = self._section(body, t("secrets.create.content_label"), t("secrets.create.content_help"))
        self.content_box = ctk.CTkTextbox(box, height=90, wrap="word")
        self.content_box.pack(fill="x")
        self.content_box.bind("<KeyRelease>", lambda _e: self.recheck(), add="+")
        ctk.CTkButton(box, text=t("secrets.create.generate"), width=200,
                      command=self._generate).pack(anchor="w", pady=(6, 0))

        ctk.CTkLabel(body, text=t("secrets.create.label_label"), anchor="w").pack(fill="x", pady=(10, 0))
        ctk.CTkEntry(body, textvariable=self.label_var,
                     placeholder_text=t("secrets.create.label_placeholder")).pack(fill="x")
        self.label_var.trace_add("write", lambda *_a: self.recheck())
        help_label(body, t("secrets.create.label_help")).pack(fill="x")

        self._audience(
            body, title=t("secrets.create.who_title"),
            client_hint=t("secrets.create.client_hint"),
            link_label=t("secrets.create.link_label"),
            link_help=t("secrets.create.link_toggle_help"),
        )

        box = self._section(body, t("secrets.create.limits_title"), t("secrets.create.limits_help"))
        self._views_row(box, t("secrets.create.limit_views"))
        self._scope_host = ctk.CTkFrame(box, fg_color="transparent")
        self._scope_host.pack(fill="x", padx=(28, 0))
        ctk.CTkLabel(box, text=t("secrets.create.expires_label"), anchor="w").pack(fill="x", pady=(8, 0))
        self._expiry_seg = self._presets(box, secret_expiry_presets(self._limits), self.expiry_key,
                                         self._pick_expiry)
        self._lifetime_note = help_label(box, "")

        box = self._section(body, t("secrets.create.passphrase_title"), t("secrets.create.passphrase_help"))
        self.passphrase = PassphrasePair(box, label=t("secrets.create.passphrase_label"),
                                         on_change=self.recheck)
        self.passphrase.pack(fill="x")
        if self._limits.passphrase_failure_mode == "burn":
            help_label(box, t("secrets.create.burn_by_admin", n=self._limits.passphrase_max_failures)).pack(
                fill="x", pady=(6, 0),
            )
        else:
            ctk.CTkCheckBox(box, text=t("secrets.create.burn_label", n=self._limits.passphrase_max_failures),
                            variable=self.burn_var).pack(anchor="w", pady=(6, 0))
            help_label(box, t("secrets.create.burn_help")).pack(fill="x", padx=(28, 0))

        box = self._section(body, t("secrets.create.notify_title"))
        ctk.CTkCheckBox(box, text=t("secrets.create.notify_label"), variable=self.notify_var).pack(anchor="w")
        help_label(box, t("secrets.create.notify_help")).pack(fill="x", padx=(28, 0))

    def _content(self) -> str:
        return self.content_box.get("1.0", "end-1c")

    def _generate(self) -> None:
        current = self._content()
        self.content_box.insert("end", ("\n" if current else "") + generate_password())
        self.recheck()

    def _pick_expiry(self, key: str) -> None:
        self.expiry_key = key
        self.recheck()

    def _after_recheck(self) -> None:
        has_group = bool(self.picker and self.picker.group_ids())
        scopes = scope_options(has_group)
        if scopes != self._scopes:
            self._scopes = scopes
            if self.scope_var.get() not in scopes:
                self.scope_var.set("per_person")
            for child in self._scope_host.winfo_children():
                child.destroy()
            ctk.CTkLabel(self._scope_host, text=t("secrets.create.scope_label"), anchor="w").pack(fill="x")
            for scope in scopes:
                name_key, help_key = SCOPE_LABEL_KEYS[scope]
                ctk.CTkRadioButton(self._scope_host, text=t(name_key), value=scope,
                                   variable=self.scope_var).pack(anchor="w", pady=(2, 0))
                help_label(self._scope_host, t(help_key)).pack(fill="x", padx=(28, 0))
        limited = self.limit_views_var.get()
        for child in self._scope_host.winfo_children():
            if isinstance(child, ctk.CTkRadioButton):
                child.configure(state="normal" if limited else "disabled")
        if self.expiry_key == NEVER and self._limits.max_lifetime_days > 0:
            self._lifetime_note.configure(
                text=t("secrets.create.lifetime_note", days=self._limits.max_lifetime_days),
            )
            self._lifetime_note.pack(fill="x", after=self._expiry_seg)
        else:
            self._lifetime_note.pack_forget()

    def blockers(self) -> list[tuple[str, dict]]:
        if not hasattr(self, "content_box"):
            return []
        pp, rep = self.passphrase.values()
        return secret_blockers(
            SecretForm(
                content=self._content(),
                label=self.label_var.get(),
                has_picked=bool(self.picker and (self.picker.user_ids() or self.picker.group_ids())),
                addresses=self.addresses_var.get(),
                create_link=bool(self.link_var.get()),
                can_external=self._can_external,
                limit_views=bool(self.limit_views_var.get()),
                max_views=self.views_var.get(),
                expiry_key=self.expiry_key,
                passphrase=pp,
                passphrase_repeat=rep,
            ),
            self._limits,
        )

    def _submit(self):
        users, groups, emails, link = self._audience_values()
        content = self._content()
        label = self.label_var.get().strip() or None
        passphrase = self.passphrase.values()[0] or None
        max_views = views_value(bool(self.limit_views_var.get()), self.views_var.get())
        scope = self.scope_var.get() if max_views is not None else "per_person"
        seconds = preset_seconds(secret_expiry_presets(self._limits), self.expiry_key)
        expires_at = until(seconds) if seconds is not None else None
        burn = bool(self.burn_var.get())
        notify = bool(self.notify_var.get())
        api = self._api
        return lambda: api_pkg.create_secret(
            api, content=content, label=label, passphrase=passphrase,
            max_views=max_views, view_scope=scope, expires_at=expires_at,
            user_ids=users, group_ids=groups, emails=emails, create_link=link,
            notify_on_view=notify, burn_on_failures=burn,
        )

    def _on_sent(self, secret) -> None:
        # The text is not kept a moment longer than it has to be.
        self.content_box.delete("1.0", "end")
        self.passphrase.wipe()
        self._nav.toast(t("secrets.create.sent_toast"), kind="success")
        sid = secret.id
        self._show_sent(
            title=t("secrets.create.sent_title"), link_url=secret.link_url,
            link_help=t("secrets.create.link_help"),
            open_text=t("secrets.create.view_status"),
            open_cmd=lambda: self._nav.open_secret(sid),
        )
