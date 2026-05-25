"""Modal message boxes — Y/N confirm and info popup.

CustomTkinter doesn't ship its own message-box widget. Rather than
add the third-party ``CTkMessagebox`` package, we roll a small wrapper
around ``CTkToplevel`` here — it's ~80 lines and matches the rest of
the app's CTk styling exactly.

Both helpers block (``wait_window``) and return the user's choice
(True/False for confirm, None for info). Call from the Tk main thread
only — the threading primitive in ``_async.py`` already marshals
worker results back onto the main thread before any UI code runs."""
from __future__ import annotations

from typing import Optional

import customtkinter as ctk

from ..i18n import t


def confirm(
    parent,
    title: str,
    body: str,
    *,
    ok_text: Optional[str] = None,
    cancel_text: Optional[str] = None,
) -> bool:
    """Modal Y/N confirmation. Returns True if the user clicked OK,
    False if they cancelled (or closed the window via the X). Default
    button labels resolve through i18n at call time so the active
    locale is honoured (v0.8.0)."""
    return _modal(
        parent, title, body,
        kind="confirm",
        ok_text=ok_text or t("common.ok"),
        cancel_text=cancel_text or t("common.cancel"),
    )


def info(parent, title: str, body: str, *, ok_text: Optional[str] = None) -> None:
    """Modal info popup with a single OK button. Returns None."""
    _modal(parent, title, body, kind="info", ok_text=ok_text or t("common.ok"))


def warn(parent, title: str, body: str, *, ok_text: Optional[str] = None) -> None:
    """Modal warning popup — same shape as info; the caller picks the
    title/body wording, the visual treatment is the same as info today.
    Separate function so future styling (red accent, ! icon) can
    diverge without touching call sites."""
    _modal(parent, title, body, kind="info", ok_text=ok_text or t("common.ok"))


def _modal(
    parent,
    title: str,
    body: str,
    *,
    kind: str,
    ok_text: str,
    cancel_text: Optional[str] = None,
) -> bool:
    win = ctk.CTkToplevel(parent)
    win.title(title)
    # Modal: grab focus + block until closed.
    win.transient(parent)
    win.geometry("420x180")
    win.resizable(False, False)

    container = ctk.CTkFrame(win, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=20, pady=20)

    label = ctk.CTkLabel(
        container, text=body, wraplength=380, justify="left", anchor="w"
    )
    label.pack(fill="both", expand=True)

    btn_row = ctk.CTkFrame(container, fg_color="transparent")
    btn_row.pack(fill="x", pady=(12, 0))

    result = {"ok": False}

    def _on_ok() -> None:
        result["ok"] = True
        win.destroy()

    def _on_cancel() -> None:
        result["ok"] = False
        win.destroy()

    if kind == "confirm":
        cancel_btn = ctk.CTkButton(
            btn_row, text=cancel_text or t("common.cancel"),
            command=_on_cancel, width=100, fg_color="gray",
        )
        cancel_btn.pack(side="right", padx=(8, 0))
    ok_btn = ctk.CTkButton(btn_row, text=ok_text, command=_on_ok, width=100)
    ok_btn.pack(side="right")

    # Esc cancels, Enter confirms — keyboard parity with native dialogs.
    win.bind("<Escape>", lambda _e: _on_cancel())
    win.bind("<Return>", lambda _e: _on_ok())

    # grab_set() must happen after the window is mapped, hence after_idle.
    win.after_idle(lambda: (win.grab_set(), win.focus_force(), ok_btn.focus_set()))
    win.protocol("WM_DELETE_WINDOW", _on_cancel)
    win.wait_window()
    return result["ok"]
