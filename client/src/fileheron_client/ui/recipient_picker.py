"""Recipient picker — v0.4.0 CustomTkinter port.

Same external API as v0.3.x (``RecipientPickerWidget`` exposes
``user_ids()``, ``group_ids()``, ``has_any()``, ``reset()``); the
internals swap Qt list-widgets for CTk scrollable frames of
checkboxes.

- ``UserPickerDialog`` — type-to-search list against
  /api/users/search, multi-select via checkboxes, OK adds the
  checked rows. Search is debounced (~200 ms) via ``after``.
- ``GroupPickerDialog`` — full list from
  /api/groups/recipient-targets, local substring filter.
- ``RecipientPickerWidget`` — embedded "Users: …" / "Groups: …" rows
  with Add / Clear buttons each."""
from __future__ import annotations

from typing import Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..i18n import t
from ..models import GroupItem, UserSearchItem
from ._async import run_in_background
from .app import center_window
from . import _messagebox as mb


class _MultiSelectPickerDialog:
    """Shared scaffolding for the user + group pickers."""

    def __init__(self, root, parent, title: str) -> None:
        self._app_root = root
        self._win = ctk.CTkToplevel(parent)
        self._win.title(title)
        center_window(self._win, 460, 520)
        self._win.transient(parent)
        self._selected_ids: list[int] = []
        self._selected_labels: list[str] = []

        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=14, pady=14)

        self.search_var = ctk.StringVar()
        search = ctk.CTkEntry(
            outer, textvariable=self.search_var,
            placeholder_text=t("common.search_placeholder_filter"),
        )
        search.pack(fill="x", pady=(0, 8))
        # Debounce typing via a single after-token we cancel + reissue
        # on each keystroke. 200ms matches the SPA's vue-i18n debounce
        # for the recipient picker.
        self._debounce_token = None

        def _on_search_change(*_args):
            if self._debounce_token is not None:
                try:
                    self._win.after_cancel(self._debounce_token)
                except Exception:
                    pass
            self._debounce_token = self._win.after(200, self._reload)

        self.search_var.trace_add("write", _on_search_change)

        self._scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)
        # Track CheckBox vars by id so we can pull selections on accept.
        self._row_vars: list[tuple[int, str, ctk.BooleanVar]] = []

        self.empty_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            outer, textvariable=self.empty_var, text_color="gray", anchor="w"
        ).pack(fill="x", pady=(4, 4))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        self._ok_btn = ctk.CTkButton(
            btn_row, text=t("common.ok"), command=self._on_ok, width=90
        )
        self._ok_btn.pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("common.cancel"), command=self._win.destroy,
            width=90, fg_color="gray",
        ).pack(side="right", padx=(0, 8))

    def _populate(self, rows: list[tuple[int, str]]) -> None:
        """Subclass calls this with ``(id, label)`` pairs after fetch."""
        for child in self._scroll.winfo_children():
            child.destroy()
        self._row_vars.clear()
        for iid, label in rows:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self._scroll, text=label, variable=var)
            cb.pack(anchor="w", pady=2)
            self._row_vars.append((iid, label.split("  ·  ")[0], var))
        if not rows:
            self.empty_var.set(t("common.no_matches"))
        else:
            self.empty_var.set("")

    def _reload(self) -> None:
        raise NotImplementedError

    def _on_ok(self) -> None:
        self._selected_ids = [iid for (iid, _l, v) in self._row_vars if v.get()]
        self._selected_labels = [l for (_iid, l, v) in self._row_vars if v.get()]
        self._win.destroy()

    def show_modal(self) -> tuple[list[int], list[str]]:
        self._win.after_idle(
            lambda: (self._win.grab_set(), self._win.focus_force())
        )
        self._win.wait_window()
        return self._selected_ids, self._selected_labels


class UserPickerDialog(_MultiSelectPickerDialog):
    def __init__(self, root, parent, api: ApiClient) -> None:
        super().__init__(root, parent, t("recipient_picker.users_title"))
        self._api = api
        self._reload()

    def _reload(self) -> None:
        q = self.search_var.get().strip()

        def _fetch():
            return api_pkg.search_users(self._api, q)

        def _done(resp):
            rows = [
                (u.user_id, f"{u.display_name}  ·  {u.email}  ·  {u.role}")
                for u in resp.items
            ]
            self._populate(rows)
            if not rows:
                self.empty_var.set(
                    t("common.no_matches") if q else t("recipient_picker.no_users")
                )

        def _failed(exc):
            mb.warn(
                self._win, t("recipient_picker.search_failed_title"),
                getattr(exc, "message", None) or str(exc),
            )

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)


class GroupPickerDialog(_MultiSelectPickerDialog):
    """One-shot fetch + local substring filter — groups are few."""

    def __init__(self, root, parent, api: ApiClient) -> None:
        super().__init__(root, parent, t("recipient_picker.groups_title"))
        self._api = api
        self._all_groups: list[GroupItem] = []

        def _fetch():
            return api_pkg.list_recipient_groups(self._api)

        def _done(resp):
            self._all_groups = resp.items
            self._reload()

        def _failed(exc):
            mb.warn(
                self._win, t("recipient_picker.load_groups_failed_title"),
                getattr(exc, "message", None) or str(exc),
            )

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _reload(self) -> None:
        needle = self.search_var.get().strip().lower()
        matched = [
            g
            for g in self._all_groups
            if not needle
            or needle in g.name.lower()
            or (g.description and needle in g.description.lower())
        ]
        rows = []
        for g in matched:
            badges = []
            if g.is_company_inbox:
                badges.append("inbox")
            badges.append(f"{g.member_count} member(s)")
            rows.append((g.id, f"{g.name}  ·  {', '.join(badges)}"))
        self._populate(rows)
        if not rows:
            self.empty_var.set(
                t("common.no_matches") if needle else t("recipient_picker.no_groups")
            )


class RecipientPickerWidget(ctk.CTkFrame):
    def __init__(self, master, root: ctk.CTk, api: ApiClient) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._user_ids: list[int] = []
        self._user_labels: list[str] = []
        self._group_ids: list[int] = []
        self._group_labels: list[str] = []

        # Users row
        users_row = ctk.CTkFrame(self, fg_color="transparent")
        users_row.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            users_row, text=t("recipient_picker.users_label"), width=60, anchor="w"
        ).pack(side="left")
        self.users_summary_var = ctk.StringVar(value=t("recipient_picker.none"))
        ctk.CTkLabel(
            users_row, textvariable=self.users_summary_var, anchor="w", wraplength=300
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            users_row, text=t("recipient_picker.add_user"), width=90, command=self._add_users
        ).pack(side="left")
        ctk.CTkButton(
            users_row, text=t("recipient_picker.clear"), width=60,
            command=self._clear_users, fg_color="gray",
        ).pack(side="left", padx=(8, 0))

        # Groups row
        groups_row = ctk.CTkFrame(self, fg_color="transparent")
        groups_row.pack(fill="x")
        ctk.CTkLabel(
            groups_row, text=t("recipient_picker.groups_label"), width=60, anchor="w"
        ).pack(side="left")
        self.groups_summary_var = ctk.StringVar(value=t("recipient_picker.none"))
        ctk.CTkLabel(
            groups_row, textvariable=self.groups_summary_var, anchor="w", wraplength=300
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            groups_row, text=t("recipient_picker.add_group"), width=90, command=self._add_groups
        ).pack(side="left")
        ctk.CTkButton(
            groups_row, text=t("recipient_picker.clear"), width=60,
            command=self._clear_groups, fg_color="gray",
        ).pack(side="left", padx=(8, 0))

    # ---- public API ----

    def user_ids(self) -> list[int]:
        return list(self._user_ids)

    def group_ids(self) -> list[int]:
        return list(self._group_ids)

    def has_any(self) -> bool:
        return bool(self._user_ids or self._group_ids)

    def reset(self) -> None:
        self._user_ids.clear()
        self._user_labels.clear()
        self._group_ids.clear()
        self._group_labels.clear()
        self._render()

    # ---- internal ----

    def _add_users(self) -> None:
        dlg = UserPickerDialog(self._app_root, self.winfo_toplevel(), self._api)
        ids, labels = dlg.show_modal()
        for iid, label in zip(ids, labels):
            if iid not in self._user_ids:
                self._user_ids.append(iid)
                self._user_labels.append(label)
        self._render()

    def _add_groups(self) -> None:
        dlg = GroupPickerDialog(self._app_root, self.winfo_toplevel(), self._api)
        ids, labels = dlg.show_modal()
        for iid, label in zip(ids, labels):
            if iid not in self._group_ids:
                self._group_ids.append(iid)
                self._group_labels.append(label)
        self._render()

    def _clear_users(self) -> None:
        self._user_ids.clear()
        self._user_labels.clear()
        self._render()

    def _clear_groups(self) -> None:
        self._group_ids.clear()
        self._group_labels.clear()
        self._render()

    def _render(self) -> None:
        self.users_summary_var.set(
            ", ".join(self._user_labels) if self._user_labels else t("recipient_picker.none")
        )
        self.groups_summary_var.set(
            ", ".join(self._group_labels) if self._group_labels else t("recipient_picker.none")
        )
