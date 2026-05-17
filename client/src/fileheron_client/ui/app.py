"""Root window + theme bootstrap (v0.4.0 CustomTkinter migration).

The desktop client is built around a single ``ctk.CTk`` root.

**v0.4.10: tkinterdnd2 was ripped out.** Both attempts to combine it
with CTk crashed post-login with the same pattern:
- v0.4.8 — mutated ``ctk.CTk.__bases__`` to add ``TkinterDnD.Tk``;
  raised ``TypeError: 'CTk' object is not callable`` from
  ``tkinter._substitute`` on the first event dispatch.
- v0.4.9 — clean subclass mixin ``class CTkDnD(ctk.CTk,
  TkinterDnD.DnDWrapper)``; same crash, just with ``'CTkDnD'``
  in the error.

The common factor was the DnDWrapper mixin itself — its presence in
the MRO shadows / interacts badly with one of CTk's internals in a
way that takes out the Tk event dispatcher. Rather than chase yet
another permutation, drag-drop is dropped entirely; the "Add files…"
button in the upload panel covers the same ground. We can revisit a
different dnd library (or hand-rolled tkdnd Tcl bindings) in a
separate change."""
from __future__ import annotations

import customtkinter as ctk

from ..assets_loader import asset_path


def build_root() -> ctk.CTk:
    """Return the single application root."""
    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("blue")  # CTk stock; tweak in widgets

    root = ctk.CTk()
    root.title("file:Heron")
    root.geometry("1000x640")

    # Window icon — fall back to PNG if .ico missing in dev. iconbitmap
    # only works on Windows + uses .ico; iconphoto is cross-platform
    # but takes a PhotoImage. Try both so dev on Linux + prod on
    # Windows both look right.
    ico = asset_path("icon.ico")
    if ico.is_file():
        try:
            root.iconbitmap(default=str(ico))
        except Exception:
            pass
    png = asset_path("icon.png")
    if png.is_file():
        try:
            from tkinter import PhotoImage

            root._fh_icon = PhotoImage(file=str(png))  # keep ref alive
            root.iconphoto(True, root._fh_icon)
        except Exception:
            pass

    return root


def set_appearance_mode(mode: str) -> None:
    """Wrapper around ``ctk.set_appearance_mode`` so the settings
    dialog can swap themes at runtime without importing customtkinter
    directly. ``mode`` ∈ {"light", "dark", "system"}."""
    if mode in ("light", "dark", "system"):
        ctk.set_appearance_mode(mode)
