"""Recipient picker - inline search (v0.9.4).

``RecipientPickerWidget`` keeps its external API (``user_ids()``,
``group_ids()``, ``has_any()``, ``reset()``) so ``upload_panel`` is untouched.
The user/group search used to open a ``CTkToplevel`` modal; it now reveals an
**inline** bounded panel inside the recipients section (one open at a time) -
no popup.

- ``_InlineUserSearch`` - type-to-search against /api/users/search (debounced).
- ``_InlineGroupSearch`` - one-shot /api/groups/recipient-targets + local filter.
"""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..i18n import t
from ..models import GroupItem
from ._async import run_in_background
from .widgets import alive

# Bounded height for the inline result list so it doesn't blow up the dense
# New-Share form.
_LIST_HEIGHT = 200


class _InlineMultiSelectPanel(ctk.CTkFrame):
    """Shared scaffolding for the inline user + group search panels. Renders a
    search entry + bounded checkbox list + Add/Cancel row into a bordered frame
    (not a toplevel). Subclasses implement ``_reload``."""

    def __init__(
        self,
        master,
        root,
        api,
        *,
        on_done: Callable[[list[int], list[str]], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(master, fg_color=("gray92", "gray16"), border_width=1, corner_radius=6)
        self._app_root = root
        self._api = api
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._debounce_token = None
        self._row_vars: list[tuple[int, str, ctk.BooleanVar]] = []

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        self.search_var = ctk.StringVar()
        self._search = ctk.CTkEntry(
            outer, textvariable=self.search_var,
            placeholder_text=t("common.search_placeholder_filter"),
        )
        self._search.pack(fill="x", pady=(0, 8))
        self.search_var.trace_add("write", self._on_search_change)
        self._search.bind("<Return>", lambda _e: self._on_add())
        self._search.bind("<Escape>", lambda _e: self._on_cancel())

        # Bounded scroll list (the single CTkScrollableFrame - leaf only).
        self._scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent", height=_LIST_HEIGHT)
        self._scroll.pack(fill="x")

        self.empty_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            outer, textvariable=self.empty_var, text_color="gray", anchor="w"
        ).pack(fill="x", pady=(4, 4))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(
            btn_row, text=t("common.ok"), command=self._on_add, width=90
        ).pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("common.cancel"), command=self._on_cancel,
            width=90, fg_color="gray",
        ).pack(side="right", padx=(0, 8))

        # A pending debounce after() firing into a destroyed panel would raise.
        self.bind("<Destroy>", lambda _e: self._cancel_debounce())

    def _on_search_change(self, *_args) -> None:
        self._cancel_debounce()
        # 200ms debounce - matches the SPA's recipient-picker debounce.
        self._debounce_token = self.after(200, self._reload)

    def _cancel_debounce(self) -> None:
        if self._debounce_token is not None:
            try:
                self.after_cancel(self._debounce_token)
            except Exception:
                pass
            self._debounce_token = None

    def _populate(self, rows: list[tuple[int, str]]) -> None:
        """Subclass calls this with ``(id, label)`` pairs after fetch."""
        if not alive(self):
            return
        for child in self._scroll.winfo_children():
            child.destroy()
        self._row_vars.clear()
        for iid, label in rows:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self._scroll, text=label, variable=var)
            cb.pack(anchor="w", pady=2)
            self._row_vars.append((iid, label.split("  ·  ")[0], var))
        self.empty_var.set("" if rows else t("common.no_matches"))

    def _reload(self) -> None:
        raise NotImplementedError

    def _on_add(self) -> None:
        ids = [iid for (iid, _l, v) in self._row_vars if v.get()]
        labels = [lbl for (_iid, lbl, v) in self._row_vars if v.get()]
        self._on_done(ids, labels)

    def focus_search(self) -> None:
        try:
            self._search.focus_set()
        except Exception:
            pass


class _InlineUserSearch(_InlineMultiSelectPanel):
    def __init__(self, master, root, api, **kw) -> None:
        super().__init__(master, root, api, **kw)
        self._reload()

    def _reload(self) -> None:
        if not alive(self):
            return
        q = self.search_var.get().strip()

        def _fetch():
            return api_pkg.search_users(self._api, q)

        def _done(resp):
            if not alive(self):
                return
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
            if not alive(self):
                return
            self.empty_var.set(
                f'{t("recipient_picker.search_failed_title")}: '
                f'{(exc.localized() if hasattr(exc, "localized") else str(exc))}'
            )

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)


class _InlineGroupSearch(_InlineMultiSelectPanel):
    """One-shot fetch + local substring filter - groups are few."""

    def __init__(self, master, root, api, **kw) -> None:
        super().__init__(master, root, api, **kw)
        self._all_groups: list[GroupItem] = []

        def _fetch():
            return api_pkg.list_recipient_groups(self._api)

        def _done(resp):
            if not alive(self):
                return
            self._all_groups = resp.items
            self._reload()

        def _failed(exc):
            if not alive(self):
                return
            self.empty_var.set(
                f'{t("recipient_picker.load_groups_failed_title")}: '
                f'{(exc.localized() if hasattr(exc, "localized") else str(exc))}'
            )

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _reload(self) -> None:
        if not alive(self):
            return
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
        self._inline_panel: _InlineMultiSelectPanel | None = None

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

        # Slot the inline search panel packs into (below the two rows).
        self._inline_host = ctk.CTkFrame(self, fg_color="transparent")
        # not packed until a search is opened.

    # ---- public API (unchanged) ----

    def user_ids(self) -> list[int]:
        return list(self._user_ids)

    def group_ids(self) -> list[int]:
        return list(self._group_ids)

    def has_any(self) -> bool:
        return bool(self._user_ids or self._group_ids)

    def reset(self) -> None:
        self._collapse_inline()
        self._user_ids.clear()
        self._user_labels.clear()
        self._group_ids.clear()
        self._group_labels.clear()
        self._render()

    # ---- inline search ----

    def _add_users(self) -> None:
        self._open_inline("users")

    def _add_groups(self) -> None:
        self._open_inline("groups")

    def _open_inline(self, kind: str) -> None:
        # One search panel open at a time (bounds vertical space in the form).
        self._collapse_inline()
        cls = _InlineUserSearch if kind == "users" else _InlineGroupSearch
        self._inline_panel = cls(
            self._inline_host, self._app_root, self._api,
            on_done=self._on_inline_done_users if kind == "users" else self._on_inline_done_groups,
            on_cancel=self._collapse_inline,
        )
        self._inline_panel.pack(fill="x", pady=(6, 0))
        self._inline_host.pack(fill="x")
        self._inline_panel.after_idle(self._inline_panel.focus_search)

    def _collapse_inline(self) -> None:
        if self._inline_panel is not None:
            try:
                self._inline_panel.destroy()
            except Exception:
                pass
            self._inline_panel = None
        try:
            self._inline_host.pack_forget()
        except Exception:
            pass

    def _on_inline_done_users(self, ids: list[int], labels: list[str]) -> None:
        # strict: ids and labels are two views of one selection - a length
        # mismatch means the picker lost a row, and silently dropping the tail
        # would show the user a name for someone else's id.
        for iid, label in zip(ids, labels, strict=True):
            if iid not in self._user_ids:
                self._user_ids.append(iid)
                self._user_labels.append(label)
        self._collapse_inline()
        self._render()

    def _on_inline_done_groups(self, ids: list[int], labels: list[str]) -> None:
        for iid, label in zip(ids, labels, strict=True):
            if iid not in self._group_ids:
                self._group_ids.append(iid)
                self._group_labels.append(label)
        self._collapse_inline()
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
