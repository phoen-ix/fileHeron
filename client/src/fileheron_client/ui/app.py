"""Root window + theme bootstrap (v0.4.0 CustomTkinter migration).

The desktop client is built around a single Tk root that's BOTH:
- a CustomTkinter ``ctk.CTk`` window (gives us theming + flat look),
- a tkinterdnd2 drag-drop target (gives us ``drop_target_register`` /
  ``dnd_bind`` on the file-list area in the upload panel).

v0.4.0 → v0.4.8 used ``ctk.CTk.__bases__ = (TkinterDnD.Tk, ...) +
...`` to retrofit dnd onto the existing CTk class. That mutation
created a diamond-inheritance MRO (CTk → TkinterDnD.Tk → tk.Tk AND
CTk → tk.Tk) which double-initialised tk.Tk and corrupted internal
event-dispatch state. The symptom was a "'CTk' object is not
callable" TypeError raised from ``tkinter._substitute`` /
``nametowidget`` on the FIRST event the main window dispatched
post-login (v0.4.8 was the first build to even get past sign-in).

v0.4.9 switches to the proper subclass mixin pattern documented in
the customtkinter community: a single fresh ``CTkDnD`` class that
inherits cleanly from ``ctk.CTk`` plus tkinterdnd2's
``DnDWrapper`` mixin, with one explicit ``_require()`` call to load
the Tcl dnd package. No more mutating any existing class's bases."""
from __future__ import annotations

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from ..assets_loader import asset_path


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """ctk.CTk + tkinterdnd2 drag-drop. Pure-Python subclass mixin —
    no ``__bases__`` mutation. ``DnDWrapper`` is a mixin (not a Tk
    subclass) that supplies ``drop_target_register`` and friends; the
    explicit ``_require()`` call loads the Tcl tkdnd package against
    this root's interpreter so the dnd methods actually work."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # tkinterdnd2 looks at this attribute to confirm dnd is wired.
        # _require returns the loaded tkdnd version string.
        self.TkdndVersion = TkinterDnD._require(self)


def build_root() -> CTkDnD:
    """Return the single application root."""
    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("blue")  # CTk stock; tweak in widgets

    root = CTkDnD()
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
