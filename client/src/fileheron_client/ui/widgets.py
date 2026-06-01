"""Small shared widgets — CustomTkinter port (v0.4.0)."""
from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk

from ..i18n import t

_log = logging.getLogger("fileheron_client.ui.widgets")


# State pill colours, mirroring the SPA's design tokens. (bg, fg) per
# state — picked to read well on both light + dark CTk themes.
_PILL_COLOURS: dict[str, tuple[str, str]] = {
    # state -> (background, text)
    "active": ("#dcfce7", "#166534"),
    "expired": ("#fef3c7", "#92400e"),
    "revoked": ("#fee2e2", "#991b1b"),
    "deleted": ("#e5e7eb", "#374151"),
    "clean": ("#dcfce7", "#166534"),
    "ready_unscanned": ("#fef3c7", "#92400e"),
    "infected": ("#fee2e2", "#991b1b"),
}


class PillLabel(ctk.CTkLabel):
    """Compact rounded chip for share / file states.

    CTk's ``corner_radius`` does the rounded edge for free — much
    cleaner than the Qt stylesheet hack the v0.3.x version used."""

    def __init__(
        self,
        master,
        text: str = "",
        state: str | None = None,
        **kwargs,
    ) -> None:
        # **kwargs forwards widget-level options (cursor, take_focus, …)
        # to CTkLabel. v0.5.4's share_list_panel started passing
        # cursor="hand2"; without forwarding it would raise TypeError
        # mid-render and the swallowed exception broke the whole rows.
        bg, fg = _PILL_COLOURS.get(state or text, ("#e5e7eb", "#374151"))
        # CTkLabel doesn't take padx/pady (those are geometry-manager
        # options). The "padding" effect is achieved with corner_radius
        # + a fixed width that's wider than the text. Width:auto would
        # give a tightly-clipped chip; 70px holds the longest state
        # ("ready_unscanned") with a margin.
        super().__init__(
            master,
            text=text,
            fg_color=bg,
            text_color=fg,
            corner_radius=10,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=80,
            height=20,
            **kwargs,
        )
        self._state_value = state or text

    def setState(self, state: str | None) -> None:  # Qt-style camelCase
        # Preserved name from the v0.3.x API so the callsites in
        # share_detail_view don't all need updating.
        bg, fg = _PILL_COLOURS.get(state or "", ("#e5e7eb", "#374151"))
        self.configure(fg_color=bg, text_color=fg)
        self._state_value = state or ""

    def setText(self, text: str) -> None:
        self.configure(text=text)


def alive(widget) -> bool:
    """True if `widget` still exists in Tk (finding C6).

    Background fetches marshal their on_done/on_failed back to the main
    thread via ui/_async. If the user navigated away (the view/window was
    destroyed) while the fetch was in flight, the callback would touch a
    dead widget. The async poll loop catches the resulting TclError, but it
    spams crash.log and the update is wasted — so callbacks that mutate
    widgets should early-return on `not alive(self)`."""
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def copy_to_clipboard_with_feedback(
    widget,
    text: str,
    *,
    feedback_var: "ctk.StringVar | None" = None,
    on_fail: Optional[Callable[[], None]] = None,
    duration_ms: int = 2000,
) -> bool:
    """Copy ``text`` to the OS clipboard and flash a transient "✓ Copied".

    The ``clipboard_clear()`` + ``clipboard_append()`` + ``update()`` sequence
    is required on Linux/X11 — the X selection is a live protocol owned by the
    source app; without pumping the event loop the data is dropped when focus
    leaves. (Harmless on Windows.) On failure ``on_fail`` is invoked (callers
    pass an ``mb.warn`` closure). Returns True on success."""
    if not text:
        return False
    top = widget.winfo_toplevel()
    try:
        top.clipboard_clear()
        top.clipboard_append(text)
        top.update()
    except Exception as e:
        _log.warning("clipboard copy failed: %s", e)
        if on_fail is not None:
            on_fail()
        return False
    if feedback_var is not None:
        feedback_var.set(t("common.copied"))

        def _clear() -> None:
            if alive(widget):
                try:
                    feedback_var.set("")
                except Exception:
                    pass

        try:
            widget.after(duration_ms, _clear)
        except Exception:
            pass
    return True


class Toast(ctk.CTkLabel):
    """Transient, non-modal status banner. ``show()`` places it centered at the
    bottom of its master (over content, no layout shift) and auto-hides it after
    a few seconds. Replaces the informational ``_messagebox.info`` popups."""

    # kind -> (fg_color, text_color), each a (light, dark) pair.
    _KIND_COLORS = {
        "info": (("#e5e7eb", "#374151"), ("#111827", "#f9fafb")),
        "success": (("#dcfce7", "#14532d"), ("#166534", "#bbf7d0")),
        "error": (("#fee2e2", "#7f1d1d"), ("#991b1b", "#fecaca")),
    }

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master, text="", corner_radius=8,
            font=ctk.CTkFont(size=12), wraplength=560,
            **kwargs,
        )
        self._after_id = None
        # A pending after() firing into a destroyed widget would raise; drop it.
        self.bind("<Destroy>", lambda _e: self._cancel())

    def show(self, text: str, *, kind: str = "info", duration_ms: int = 2800) -> None:
        self._cancel()
        fg, txt = self._KIND_COLORS.get(kind, self._KIND_COLORS["info"])
        self.configure(text=text, fg_color=fg, text_color=txt)
        # place (not pack) so showing/hiding never reflows the panels.
        self.place(relx=0.5, rely=1.0, anchor="s", y=-12)
        self.lift()
        try:
            self._after_id = self.after(duration_ms, self._hide)
        except Exception:
            self._after_id = None

    def _hide(self) -> None:
        self._after_id = None
        if alive(self):
            try:
                self.place_forget()
            except Exception:
                pass

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    k = n / 1024
    if k < 1024:
        return f"{k:.1f} KB"
    m = k / 1024
    if m < 1024:
        return f"{m:.1f} MB"
    return f"{m / 1024:.2f} GB"
