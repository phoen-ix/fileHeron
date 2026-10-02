"""Request a secret (server v2.24.0): ask someone for a password.

Whoever answers types it into file:Heron and it arrives as a secret only the
requester can open, within the limits set here; the first answer closes the
request. An optional passphrase is the requester's own: the answer is
encrypted to it, nobody stores it, and a lost one cannot be recovered - the
form says so.
"""
from __future__ import annotations

import customtkinter as ctk

from .. import api as api_pkg
from ..i18n import t
from ..secret_rules import (
    MAX_NOTE,
    RequestForm,
    answer_lifetime_options,
    default_preset,
    preset_seconds,
    request_blockers,
    request_open_presets,
    views_value,
)
from .secret_compose_view import ComposeBase, until
from .secret_widgets import PassphrasePair, help_label

# Answer lifetime key -> label key ("never" reads "No time limit" here).
LIFETIME_LABEL_KEYS = {
    "1h": "secrets.preset.1h",
    "1d": "secrets.preset.1d",
    "7d": "secrets.preset.7d",
    "30d": "secrets.preset.30d",
    "90d": "secrets.preset.90d",
    "never": "secrets.request.lifetime_none",
}


class SecretRequestComposeView(ComposeBase):
    TITLE_KEY = "secrets.request.create_title"
    SEND_KEY = "secrets.request.send"

    def _build_form(self, body) -> None:
        self.label_var = ctk.StringVar()
        self.open_key = default_preset(request_open_presets(self._limits))
        self.lifetime_key = default_preset(answer_lifetime_options(self._limits))

        help_label(body, t("secrets.request.intro")).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(body, text=t("secrets.request.label_label"), anchor="w").pack(fill="x", pady=(6, 0))
        ctk.CTkEntry(body, textvariable=self.label_var,
                     placeholder_text=t("secrets.request.label_placeholder")).pack(fill="x")
        self.label_var.trace_add("write", lambda *_a: self.recheck())
        ctk.CTkLabel(body, text=t("secrets.request.note_label"), anchor="w").pack(fill="x", pady=(8, 0))
        self.note_box = ctk.CTkTextbox(body, height=60, wrap="word")
        self.note_box.pack(fill="x")
        help_label(body, t("secrets.request.note_help")).pack(fill="x")

        self._audience(
            body, title=t("secrets.request.who_title"),
            client_hint=t("secrets.request.client_hint"),
            link_label=t("secrets.create.link_label"),
            link_help=t("secrets.request.link_toggle_help"),
        )

        box = self._section(body, t("secrets.request.open_until_label"), t("secrets.request.open_until_help"))
        self._presets(box, request_open_presets(self._limits), self.open_key, self._pick_open)

        box = self._section(body, t("secrets.request.limits_title"), t("secrets.request.limits_help"))
        self._views_row(box, t("secrets.create.limit_views"))
        ctk.CTkLabel(box, text=t("secrets.request.lifetime_label"), anchor="w").pack(fill="x", pady=(8, 0))
        options = answer_lifetime_options(self._limits)
        by_label = {t(LIFETIME_LABEL_KEYS[k]): k for k, _s in options}
        seg = ctk.CTkSegmentedButton(box, values=list(by_label),
                                     command=lambda lbl: self._pick_lifetime(by_label[lbl]))
        seg.set(t(LIFETIME_LABEL_KEYS[self.lifetime_key]))
        seg.pack(anchor="w", pady=(2, 2))
        help_label(box, t("secrets.request.lifetime_help")).pack(fill="x")

        box = self._section(body, t("secrets.request.passphrase_title"), t("secrets.request.passphrase_help"))
        self.passphrase = PassphrasePair(box, label=t("secrets.create.passphrase_label"),
                                         on_change=self.recheck)
        self.passphrase.pack(fill="x")

    def _pick_open(self, key: str) -> None:
        self.open_key = key
        self.recheck()

    def _pick_lifetime(self, key: str) -> None:
        self.lifetime_key = key
        self.recheck()

    def blockers(self) -> list[tuple[str, dict]]:
        if not hasattr(self, "passphrase"):
            return []
        pp, rep = self.passphrase.values()
        return request_blockers(
            RequestForm(
                label=self.label_var.get(),
                has_picked=bool(self.picker and (self.picker.user_ids() or self.picker.group_ids())),
                addresses=self.addresses_var.get(),
                create_link=bool(self.link_var.get()),
                can_external=self._can_external,
                limit_views=bool(self.limit_views_var.get()),
                max_views=self.views_var.get(),
                lifetime_key=self.lifetime_key,
                passphrase=pp,
                passphrase_repeat=rep,
            ),
            self._limits,
        )

    def _submit(self):
        users, groups, emails, link = self._audience_values()
        label = self.label_var.get().strip()
        note = self.note_box.get("1.0", "end-1c").strip()[:MAX_NOTE] or None
        open_for = preset_seconds(request_open_presets(self._limits), self.open_key)
        expires_at = until(open_for or 7 * 86400)
        max_views = views_value(bool(self.limit_views_var.get()), self.views_var.get())
        lifetime = preset_seconds(answer_lifetime_options(self._limits), self.lifetime_key)
        passphrase = self.passphrase.values()[0] or None
        api = self._api
        return lambda: api_pkg.create_secret_request(
            api, label=label, note=note, expires_at=expires_at,
            answer_max_views=max_views, answer_expires_in_sec=lifetime,
            passphrase=passphrase, user_ids=users, group_ids=groups,
            emails=emails, create_link=link,
        )

    def _on_sent(self, request) -> None:
        self.passphrase.wipe()
        self._nav.toast(t("secrets.request.sent_toast"), kind="success")
        rid = request.id
        self._show_sent(
            title=t("secrets.request.sent_title"), link_url=request.link_url,
            link_help=t("secrets.request.link_help"),
            open_text=t("secrets.request.view_status"),
            open_cmd=lambda: self._nav.open_request(rid),
        )
