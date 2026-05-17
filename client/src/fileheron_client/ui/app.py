"""Root window + theme bootstrap.

The desktop client is built around a single ``ctk.CTk`` root.

**v0.5.0 brought tkinterdnd2 back.** v0.4.10 had ripped it out
blaming a ``TypeError: 'CTk' object is not callable`` crash on the
mixin's interaction with CTk's MRO. The real culprit turned out to
be our own widget subclasses doing ``self._root = root``, which
shadowed ``tkinter.Misc._root`` (an inherited method tkinter calls
during event substitution). v0.4.11 fixed that by renaming the
attribute to ``self._app_root`` everywhere, so the
``class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper)`` mixin pattern
works cleanly now."""
from __future__ import annotations

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from ..assets_loader import asset_path


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """ctk.CTk + tkinterdnd2 drag-drop. ``DnDWrapper`` is a pure
    mixin (no Tk state of its own) that supplies
    ``drop_target_register`` / ``dnd_bind``; the explicit
    ``_require()`` call loads the Tcl ``tkdnd`` package against
    this root's interpreter so the dnd methods actually work."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


def build_root() -> ctk.CTk:
    """Return the single application root."""
    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("blue")  # CTk stock; tweak in widgets

    root = CTkDnD()
    root.title("file:Heron")
    # The New share form pins its submit/Add files row to the bottom
    # (side="bottom") so 1000x640 fits everything; the file list area
    # absorbs slack space.
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
