"""Small shared widgets — CustomTkinter port (v0.4.0)."""
from __future__ import annotations

import customtkinter as ctk


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
