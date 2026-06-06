"""Standard right-click (Cut / Copy / Paste / Select all) context menu for
every text input.

Tkinter - and therefore CustomTkinter, which wraps it - ships no native context
menu, so a right-click on a ``CTkEntry`` / ``CTkTextbox`` did nothing. This wires
one up app-wide.

Installed once on the root via ``bind_class`` (interpreter-global), so it covers
widgets built later too (overlays rebuilt on each login, dialogs, …). ``CTkEntry``
is backed by a ``tkinter.Entry`` (``winfo_class() == "Entry"``) and ``CTkTextbox``
by a ``tkinter.Text`` (``"Text"``); binding those Tk widget *classes* catches the
inner widget that actually receives the click."""
from __future__ import annotations

import tkinter as tk

from ..i18n import t


def install_context_menus(root: tk.Misc) -> None:
    """Bind a right-click context menu to every Entry/Text widget. Call once,
    on the application root."""
    for cls in ("Entry", "TEntry", "Text"):
        root.bind_class(cls, "<Button-3>", _show_menu, add="+")


def _is_editable(widget: tk.Misc) -> bool:
    try:
        return str(widget.cget("state")) == "normal"
    except Exception:
        return True


def _select_all(widget: tk.Misc) -> None:
    try:
        if widget.winfo_class() == "Text":
            widget.tag_add("sel", "1.0", "end-1c")
        else:
            widget.select_range(0, "end")
            widget.icursor("end")
    except Exception:
        pass


def _show_menu(event: tk.Event) -> None:
    widget = event.widget
    try:
        widget.focus_set()
    except Exception:
        pass
    editable = _is_editable(widget)
    edit_state = "normal" if editable else "disabled"

    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(
        label=t("context_menu.cut"),
        state=edit_state,
        command=lambda: widget.event_generate("<<Cut>>"),
    )
    menu.add_command(
        label=t("context_menu.copy"),
        command=lambda: widget.event_generate("<<Copy>>"),
    )
    menu.add_command(
        label=t("context_menu.paste"),
        state=edit_state,
        command=lambda: widget.event_generate("<<Paste>>"),
    )
    menu.add_separator()
    menu.add_command(
        label=t("context_menu.select_all"),
        command=lambda: _select_all(widget),
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
